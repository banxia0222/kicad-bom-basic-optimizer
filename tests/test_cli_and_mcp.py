"""Comprehensive unit and end-to-end tests for CLI, core engine, and stdio MCP server."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from bom_basic_optimizer.core import (
    analyze_bom_csv,
    analyze_bom_file,
    format_report_csv,
    format_report_json,
    format_report_markdown,
    lookup_basic_alternative,
    parse_bom_csv,
)
from bom_basic_optimizer.server import handle_jsonrpc_message, serve_stdio

SAMPLE_BOM_PATH = PACKAGE_ROOT / "samples" / "sample_kicad_bom.csv"


class TestCoreEngine(unittest.TestCase):
    def test_sample_kicad_bom_end_to_end(self) -> None:
        report = analyze_bom_file(SAMPLE_BOM_PATH)
        self.assertEqual(report.detected_format, "kicad")
        self.assertEqual(report.total_bom_lines, 22)
        self.assertEqual(report.dnp_lines, 1)
        self.assertEqual(report.active_bom_lines, 21)
        self.assertEqual(report.already_basic_lines, 1)
        self.assertEqual(report.optimizable_lines, 17)
        self.assertEqual(report.drop_in_optimizable_lines, 16)
        self.assertEqual(report.review_required_lines, 1)
        self.assertEqual(report.keep_extended_lines, 3)

        # $3 * 20 non-basic active lines before = $60; $3 * 3 keep_extended after = $9
        self.assertAlmostEqual(report.estimated_feeder_fee_before_usd, 60.0)
        self.assertAlmostEqual(report.estimated_feeder_fee_after_usd, 9.0)
        self.assertAlmostEqual(report.estimated_savings_usd, 51.0)
        self.assertAlmostEqual(report.drop_in_savings_usd, 48.0)

        # Verify all 4 grades appear in sample report
        self.assertGreaterEqual(report.grade_counts["A↑"], 3)
        self.assertGreaterEqual(report.grade_counts["A±"], 10)
        self.assertGreaterEqual(report.grade_counts["A↓"], 2)
        self.assertEqual(report.grade_counts["B"], 1)

        # Markdown, CSV, and JSON rendering
        md = format_report_markdown(report)
        self.assertIn("https://partcrosslab.pages.dev", md)
        self.assertIn("$51.00", md)
        self.assertIn("C14663", md)

        csv_out = format_report_csv(report)
        self.assertIn("Basic_LCSC", csv_out)
        self.assertIn("C6186", csv_out)

        parsed_json = json.loads(format_report_json(report))
        self.assertEqual(parsed_json["summary"]["optimizable_lines"], 17)

    def test_passive_normalization_manufacturers(self) -> None:
        # Yageo 0603 10k 5% -> A↑ (1% Basic C25804)
        spec, match = lookup_basic_alternative("RC0603JR-0710KL")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.basic_lcsc, "C25804")
        self.assertEqual(match.grade, "A↑")
        self.assertIn("0603", spec)

        # Murata 0603 100nF 16V X7R -> A↑ (50V X7R Basic C14663)
        _, match = lookup_basic_alternative("GRM188R71C104KA01D")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.basic_lcsc, "C14663")
        self.assertEqual(match.grade, "A↑")

        # Murata 0402 100nF 50V X7R -> A↓ (16V Basic C1525)
        _, match = lookup_basic_alternative("GRM155R71H104KE14D")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.basic_lcsc, "C1525")
        self.assertEqual(match.grade, "A↓")
        self.assertIn("Lower voltage", match.caution)

        # Samsung 0805 10uF 25V X5R -> A± (C15850)
        _, match = lookup_basic_alternative("CL21A106KAYNNNE")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.basic_lcsc, "C15850")
        self.assertEqual(match.grade, "A±")

        # TDK 0402 1uF 10V X5R -> A↑ (25V Basic C52923)
        _, match = lookup_basic_alternative("C1005X5R1A105K050BB")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.basic_lcsc, "C52923")
        self.assertEqual(match.grade, "A↑")

        # UniOhm 0402 4.7k 1% -> A± (C25900)
        _, match = lookup_basic_alternative("0402WGF4701TCE")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.basic_lcsc, "C25900")
        self.assertEqual(match.grade, "A±")

        # Already Basic LCSC lookup
        _, match = lookup_basic_alternative("CC0603KRX7R9BB104", lcsc="C14663")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertTrue(match.already_basic)

    def test_popular_semiconductors_and_protection(self) -> None:
        cases = [
            ("LM1117IMPX-3.3", "C6186", "A±"),
            ("AMS1117-3.3", "C6186", "A±"),
            ("AO3400A", "C20917", "A±"),
            ("SI2302", "C20917", "A↑"),
            ("1N4148WS", "C2128", "A±"),
            ("BAT54C", "C37704", "A±"),
            ("BAT54S", "C37705", "A±"),
            ("BAT54", "C37704", "B"),
            ("SMBJ5.0A", "C78392", "B"),
            ("SS34", "C8678", "A±"),
            ("USBLC6-2SC6", "C7519", "A±"),
            ("TJA1051T/3", "C38695", "A±"),
            ("CH340C", "C84681", "A±"),
            ("CH340G", "C84681", "A↑"),
            ("NE555DR", "C46749", "A±"),
            ("TLC555", "C46749", "B"),
        ]
        for query, expected_lcsc, expected_grade in cases:
            with self.subTest(query=query):
                _, match = lookup_basic_alternative(query)
                self.assertIsNotNone(match)
                assert match is not None
                self.assertEqual(match.basic_lcsc, expected_lcsc)
                self.assertEqual(match.grade, expected_grade)

    def test_altium_and_easyeda_bom_parsing_and_health_checks(self) -> None:
        altium_bom = "\n".join(
            [
                "Designator,Comment,Footprint,Quantity,Manufacturer Part Number 1,Description",
                '"R1-R3",10k,0603,2,RC0603FR-0710KL,Qty mismatch on range',
                "R2,4.7k,0603,1,RC0603FR-074K7L,Duplicate refdes R2",
                "C1,TBD,0402,1,,Placeholder value",
                "C2,10uF 6.3V Y5V,0402,1,CL05F106MQ5NUNC,Low voltage unstable dielectric",
                "U1,CUSTOM-ADC-16B,,1,CUSTOM-ADC-16B,0 to 70C commercial temp no footprint",
            ]
        )
        rows, fmt = parse_bom_csv(altium_bom)
        self.assertEqual(fmt, "altium")
        self.assertEqual(rows[0].refdes_list, ["R1", "R2", "R3"])

        report = analyze_bom_csv(altium_bom, source_name="altium_test.csv")
        codes = {issue.code for issue in report.health_issues}
        self.assertIn("QTY_REFDES_MISMATCH", codes)
        self.assertIn("DUPLICATE_REFDES", codes)
        self.assertIn("PLACEHOLDER_VALUE", codes)
        self.assertIn("NARROW_VOLTAGE_MARGIN", codes)
        self.assertIn("NARROW_TEMP_DIELECTRIC", codes)
        self.assertIn("MISSING_PACKAGE", codes)
        self.assertIn("NARROW_TEMP_RANGE", codes)
        self.assertIn("SINGLE_SOURCE_PART", codes)

        easyeda_bom = "\n".join(
            [
                "Designator\tComment\tFootprint\tQuantity\tLCSC Part #\tManufacturer Part",
                "R1\t10k\tR0603\t1\tC25804\t0603WAF1002T5E",
            ]
        )
        e_rows, e_fmt = parse_bom_csv(easyeda_bom)
        self.assertEqual(e_fmt, "easyeda")
        self.assertEqual(e_rows[0].lcsc, "C25804")


class TestCliAndMcp(unittest.TestCase):
    def test_cli_analyze_and_lookup_subprocesses(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "bom_basic_optimizer",
                "analyze",
                str(SAMPLE_BOM_PATH),
                "--format",
                "md",
            ],
            cwd=str(PACKAGE_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("Feeder-Fee Savings Summary", proc.stdout)
        self.assertIn("$51.00", proc.stdout)
        self.assertIn("https://partcrosslab.pages.dev", proc.stdout)

        with tempfile.TemporaryDirectory() as tmpdir:
            out_csv = Path(tmpdir) / "out.csv"
            proc_csv = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bom_basic_optimizer",
                    "analyze",
                    str(SAMPLE_BOM_PATH),
                    "--format",
                    "csv",
                    "-o",
                    str(out_csv),
                ],
                cwd=str(PACKAGE_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_csv.returncode, 0, msg=proc_csv.stderr)
            self.assertTrue(out_csv.is_file())
            self.assertIn("C25804", out_csv.read_text(encoding="utf-8"))

        proc_lookup = subprocess.run(
            [
                sys.executable,
                "-m",
                "bom_basic_optimizer",
                "lookup",
                "USBLC6-2SC6",
                "--format",
                "json",
            ],
            cwd=str(PACKAGE_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc_lookup.returncode, 0, msg=proc_lookup.stderr)
        data = json.loads(proc_lookup.stdout)
        self.assertTrue(data["found"])
        self.assertEqual(data["match"]["basic_lcsc"], "C7519")

    def test_cli_strict_mode_exits_nonzero_on_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_bom = Path(tmpdir) / "bad.csv"
            bad_bom.write_text("Reference,Qty,Value,Footprint\nR1,1,TBD,0603\n", encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bom_basic_optimizer",
                    "analyze",
                    str(bad_bom),
                    "--strict",
                ],
                cwd=str(PACKAGE_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 2)

    def test_mcp_stdio_jsonl_and_lsp_framing(self) -> None:
        # 1. Test JSON-lines framing via real subprocess
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "analyze_kicad_bom",
                    "arguments": {"file_path": str(SAMPLE_BOM_PATH), "output_format": "json"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "find_jlcpcb_basic_alternative",
                    "arguments": {"query": "RC0603FR-0710KL"},
                },
            },
        ]
        stdin_payload = "\n".join(json.dumps(r) for r in requests) + "\n"
        proc = subprocess.run(
            [sys.executable, "-m", "bom_basic_optimizer.server"],
            cwd=str(PACKAGE_ROOT),
            input=stdin_payload,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        resp_lines = [json.loads(line) for line in proc.stdout.strip().splitlines() if line.strip()]
        self.assertEqual(len(resp_lines), 4)

        # initialize
        self.assertEqual(resp_lines[0]["id"], 1)
        self.assertEqual(resp_lines[0]["result"]["serverInfo"]["name"], "kicad-bom-basic-optimizer")

        # tools/list
        tool_names = [t["name"] for t in resp_lines[1]["result"]["tools"]]
        self.assertEqual(tool_names, ["analyze_kicad_bom", "find_jlcpcb_basic_alternative"])

        # tools/call analyze_kicad_bom
        analyze_res = resp_lines[2]["result"]
        self.assertFalse(analyze_res["isError"])
        report_json = json.loads(analyze_res["content"][0]["text"])
        self.assertEqual(report_json["summary"]["estimated_savings_usd"], 51.0)

        # tools/call find_jlcpcb_basic_alternative
        lookup_res = resp_lines[3]["result"]
        self.assertFalse(lookup_res["isError"])
        lookup_json = json.loads(lookup_res["content"][0]["text"])
        self.assertEqual(lookup_json["match"]["basic_lcsc"], "C25804")

        # 2. Test Content-Length LSP framing in-process
        req_bytes = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}).encode("utf-8")
        framed_in = io.BytesIO(f"Content-Length: {len(req_bytes)}\r\n\r\n".encode("ascii") + req_bytes)
        framed_out = io.BytesIO()
        serve_stdio(framed_in, framed_out)
        raw_out = framed_out.getvalue().decode("utf-8")
        self.assertTrue(raw_out.startswith("Content-Length:"))
        self.assertIn('"id": 9', raw_out)

    def test_mcp_error_handling(self) -> None:
        resp = handle_jsonrpc_message({"jsonrpc": "2.0", "id": 99, "method": "nonexistent/method"})
        assert resp is not None
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32601)

        bad_tool = handle_jsonrpc_message(
            {
                "jsonrpc": "2.0",
                "id": 100,
                "method": "tools/call",
                "params": {"name": "analyze_kicad_bom", "arguments": {}},
            }
        )
        assert bad_tool is not None
        self.assertTrue(bad_tool["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
