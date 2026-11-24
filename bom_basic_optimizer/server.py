"""Zero-dependency stdio MCP server (JSON-RPC 2.0) for kicad-bom-basic-optimizer.

Supports both standard newline-delimited JSON-RPC framing (MCP 2024-11-05+)
and Content-Length framed messages.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Tuple

from .core import (
    DEFAULT_FEEDER_FEE_USD,
    PARTCROSSLAB_URL,
    analyze_bom_csv,
    analyze_bom_file,
    format_report_csv,
    format_report_json,
    format_report_markdown,
    lookup_basic_alternative,
)

SERVER_NAME = "kicad-bom-basic-optimizer"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

TOOLS_SPEC: List[Dict[str, Any]] = [
    {
        "name": "analyze_kicad_bom",
        "description": (
            "Parse a KiCad, Altium, or EasyEDA BOM CSV (via file_path or inline csv_content), "
            "run manufacturing health checks, map Extended SMD passives and popular semiconductors "
            "to JLCPCB Basic equivalents with A↑/A±/A↓/B grading, and compute $3/part feeder-fee savings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Local path to a KiCad, Altium, or EasyEDA BOM CSV file.",
                },
                "csv_content": {
                    "type": "string",
                    "description": "Inline BOM CSV text (alternative to file_path).",
                },
                "source_name": {
                    "type": "string",
                    "description": "Optional display name for inline CSV reports (default: 'inline_bom.csv').",
                },
                "output_format": {
                    "type": "string",
                    "enum": ["markdown", "json", "csv"],
                    "description": "Report format to return (default: 'markdown').",
                },
                "feeder_fee_usd": {
                    "type": "number",
                    "description": "Feeder loading fee per Extended part in USD (default: 3.0).",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "find_jlcpcb_basic_alternative",
        "description": (
            "Look up a single component MPN or SMD passive specification (e.g. 'RC0603FR-0710KL', "
            "'100nF 16V X7R', 'LM1117IMPX-3.3', 'USBLC6-2SC6', 'CH340G') and return a graded "
            "(A↑ / A± / A↓ / B) JLCPCB Basic equivalent with engineering cautions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Component MPN, part name, or passive description.",
                },
                "package": {
                    "type": "string",
                    "description": "Optional SMD package or footprint (e.g. '0402', '0603', 'SOT-23', 'SOIC-8').",
                },
                "value": {
                    "type": "string",
                    "description": "Optional electrical value (e.g. '10k', '100nF 50V X7R').",
                },
                "lcsc": {
                    "type": "string",
                    "description": "Optional LCSC part number (e.g. 'C14663') to check if already Basic.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]


def _make_result(req_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _handle_tool_call(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if name == "analyze_kicad_bom":
        file_path = arguments.get("file_path")
        csv_content = arguments.get("csv_content")
        output_format = str(arguments.get("output_format", "markdown")).lower()
        feeder_fee = float(arguments.get("feeder_fee_usd", DEFAULT_FEEDER_FEE_USD))

        if not file_path and not csv_content:
            return {
                "content": [{"type": "text", "text": "Error: provide either 'file_path' or 'csv_content'."}],
                "isError": True,
            }

        try:
            if file_path:
                path = Path(str(file_path))
                if not path.is_file():
                    return {
                        "content": [{"type": "text", "text": f"Error: file not found: {path}"}],
                        "isError": True,
                    }
                report = analyze_bom_file(path, feeder_fee_usd=feeder_fee)
            else:
                source_name = str(arguments.get("source_name", "inline_bom.csv"))
                report = analyze_bom_csv(str(csv_content), source_name=source_name, feeder_fee_usd=feeder_fee)
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": f"Error analyzing BOM: {exc}"}],
                "isError": True,
            }

        if output_format == "json":
            text = format_report_json(report)
        elif output_format == "csv":
            text = format_report_csv(report)
        else:
            text = format_report_markdown(report)

        return {"content": [{"type": "text", "text": text}], "isError": False}

    if name == "find_jlcpcb_basic_alternative":
        query = str(arguments.get("query", "")).strip()
        if not query:
            return {
                "content": [{"type": "text", "text": "Error: 'query' must be a non-empty string."}],
                "isError": True,
            }
        package = str(arguments.get("package", "") or "")
        value = str(arguments.get("value", "") or "")
        lcsc = str(arguments.get("lcsc", "") or "")

        spec, match = lookup_basic_alternative(
            query=query,
            package=package,
            value=value,
            lcsc=lcsc,
        )
        payload = {
            "query": query,
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
        return {
            "content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)}],
            "isError": False,
        }

    return {
        "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
        "isError": True,
    }


def handle_jsonrpc_message(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Process one JSON-RPC 2.0 request/notification dictionary."""
    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}

    # Notifications have no "id" and expect no response
    if req_id is None and isinstance(method, str) and method.startswith("notifications/"):
        return None

    if method == "initialize":
        client_version = params.get("protocolVersion") or PROTOCOL_VERSION
        return _make_result(
            req_id,
            {
                "protocolVersion": client_version,
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
            },
        )

    if method == "ping":
        return _make_result(req_id, {})

    if method == "tools/list":
        return _make_result(req_id, {"tools": TOOLS_SPEC})

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        if not isinstance(tool_args, dict):
            return _make_error(req_id, -32602, "Tool arguments must be a JSON object.")
        return _make_result(req_id, _handle_tool_call(str(tool_name), tool_args))

    if req_id is None:
        return None
    return _make_error(req_id, -32601, f"Method not found: {method}")


def _read_message(stream: BinaryIO) -> Tuple[Optional[Dict[str, Any]], str]:
    """Read one JSON-RPC message from a binary stream.

    Returns (parsed_message | None, framing_mode) where framing_mode is 'jsonl' or 'lsp'.
    """
    while True:
        line = stream.readline()
        if not line:
            return None, "jsonl"
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.lower().startswith(b"content-length:"):
            headers: Dict[str, str] = {}
            header_line = stripped
            while header_line:
                if b":" in header_line:
                    k, v = header_line.decode("ascii", errors="ignore").split(":", 1)
                    headers[k.strip().lower()] = v.strip()
                next_line = stream.readline()
                if not next_line:
                    return None, "lsp"
                header_line = next_line.strip()
            length = int(headers.get("content-length", "0"))
            if length <= 0:
                continue
            body = stream.read(length)
            if not body:
                return None, "lsp"
            return json.loads(body.decode("utf-8")), "lsp"

        return json.loads(stripped.decode("utf-8")), "jsonl"


def _write_message(stream: BinaryIO, payload: Dict[str, Any], framing: str) -> None:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if framing == "lsp":
        header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
        stream.write(header + encoded)
    else:
        stream.write(encoded + b"\n")
    stream.flush()


def serve_stdio(
    in_stream: Optional[BinaryIO] = None,
    out_stream: Optional[BinaryIO] = None,
) -> None:
    """Run the stdio MCP JSON-RPC loop until EOF."""
    stdin = in_stream if in_stream is not None else sys.stdin.buffer
    stdout = out_stream if out_stream is not None else sys.stdout.buffer

    while True:
        try:
            message, framing = _read_message(stdin)
        except json.JSONDecodeError as exc:
            _write_message(stdout, _make_error(None, -32700, f"Parse error: {exc}"), "jsonl")
            continue

        if message is None:
            break

        if not isinstance(message, dict):
            _write_message(stdout, _make_error(None, -32600, "Invalid JSON-RPC request."), framing)
            continue

        response = handle_jsonrpc_message(message)
        if response is not None:
            _write_message(stdout, response, framing)


def main() -> int:
    serve_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
