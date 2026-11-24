"""Command-line interface for kicad-bom-basic-optimizer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .core import (
    DEFAULT_FEEDER_FEE_USD,
    PARTCROSSLAB_URL,
    analyze_bom_file,
    format_report_csv,
    format_report_json,
    format_report_markdown,
    lookup_basic_alternative,
)
from .server import serve_stdio


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bom-basic-optimizer",
        description="Analyze KiCad/Altium/EasyEDA BOM CSVs and map Extended parts to JLCPCB Basic equivalents ($3/part saved).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="Analyze a BOM CSV file and output an optimization report.")
    p_analyze.add_argument("bom_csv", help="Path to KiCad, Altium, or EasyEDA BOM CSV file.")
    p_analyze.add_argument(
        "--format",
        choices=("md", "csv", "json"),
        default="md",
        help="Output report format (default: md).",
    )
    p_analyze.add_argument(
        "--output",
        "-o",
        help="Write report to a file instead of stdout.",
    )
    p_analyze.add_argument(
        "--fee",
        type=float,
        default=DEFAULT_FEEDER_FEE_USD,
        help="JLCPCB Extended part feeder loading fee in USD (default: 3.0).",
    )
    p_analyze.add_argument(
        "--strict",
        action="store_true",
        help="Return exit code 2 if any ERROR-level BOM health issue is found.",
    )

    p_lookup = sub.add_parser("lookup", help="Look up a single MPN or passive value for a JLCPCB Basic equivalent.")
    p_lookup.add_argument("query", help="MPN or part query (e.g. 'RC0603FR-0710KL', 'LM1117IMPX-3.3', '100nF 0603').")
    p_lookup.add_argument("--package", default="", help="Optional package/footprint hint (e.g. 0603, SOT-23, SOIC-8).")
    p_lookup.add_argument("--value", default="", help="Optional electrical value hint (e.g. 10k, 100nF 50V X7R).")
    p_lookup.add_argument("--lcsc", default="", help="Optional existing LCSC part number (e.g. C14663).")
    p_lookup.add_argument("--format", choices=("md", "json"), default="md", help="Output format (default: md).")

    sub.add_parser("mcp", help="Run the zero-dependency stdio MCP server (JSON-RPC 2.0).")
    return parser


def _cmd_analyze(args: argparse.Namespace) -> int:
    path = Path(args.bom_csv)
    if not path.is_file():
        print(f"Error: BOM file not found: {path}", file=sys.stderr)
        return 1

    report = analyze_bom_file(path, feeder_fee_usd=args.fee)
    if args.format == "md":
        rendered = format_report_markdown(report)
    elif args.format == "csv":
        rendered = format_report_csv(report)
    else:
        rendered = format_report_json(report)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)

    if args.strict and any(i.severity == "error" for i in report.health_issues):
        return 2
    return 0


def _cmd_lookup(args: argparse.Namespace) -> int:
    spec, match = lookup_basic_alternative(
        query=args.query,
        package=args.package,
        value=args.value,
        lcsc=args.lcsc,
    )
    if args.format == "json":
        payload = {
            "query": args.query,
            "normalized_spec": spec,
            "found": match is not None,
            "match": None
            if match is None
            else {
                "grade": match.grade,
                "basic_lcsc": match.basic_lcsc,
                "basic_mpn": match.basic_mpn,
                "basic_manufacturer": match.basic_manufacturer,
                "basic_package": match.basic_package,
                "basic_description": match.basic_description,
                "category": match.category,
                "already_basic": match.already_basic,
                "confidence": match.confidence,
                "reason": match.reason,
                "caution": match.caution,
            },
            "partcrosslab_url": PARTCROSSLAB_URL,
        }
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return 0

    if match is None:
        sys.stdout.write(
            f"No bundled JLCPCB Basic equivalent found for `{args.query}` (spec: {spec}).\n"
            f"Search the extended parametric cross-reference catalog at {PARTCROSSLAB_URL}\n"
        )
        return 0

    status = "Already JLCPCB Basic ($0 feeder fee)" if match.already_basic else "JLCPCB Basic Equivalent ($3 feeder fee saved)"
    sys.stdout.write(
        f"### {status}\n\n"
        f"- **Normalized Spec:** `{spec}`\n"
        f"- **Grade:** `{match.grade}` ({match.confidence} confidence)\n"
        f"- **Basic LCSC:** `{match.basic_lcsc}`\n"
        f"- **Basic MPN:** `{match.basic_mpn}` ({match.basic_manufacturer})\n"
        f"- **Package:** `{match.basic_package}`\n"
        f"- **Description:** {match.basic_description}\n"
        f"- **Rationale:** {match.reason}\n"
        f"- **Caution:** {match.caution}\n"
        f"- **Full Catalog:** {PARTCROSSLAB_URL}\n"
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "analyze":
        return _cmd_analyze(args)
    if args.command == "lookup":
        return _cmd_lookup(args)
    if args.command == "mcp":
        serve_stdio()
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
