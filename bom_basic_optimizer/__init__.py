"""Zero-dependency KiCad/Altium/EasyEDA BOM analyzer and JLCPCB Basic part optimizer."""

from __future__ import annotations

from .core import (
    DEFAULT_FEEDER_FEE_USD,
    BasicMatch,
    BomReport,
    BomRow,
    HealthIssue,
    RowAnalysis,
    analyze_bom_csv,
    analyze_bom_file,
    format_report_csv,
    format_report_json,
    format_report_markdown,
    lookup_basic_alternative,
    parse_bom_csv,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "DEFAULT_FEEDER_FEE_USD",
    "BasicMatch",
    "BomReport",
    "BomRow",
    "HealthIssue",
    "RowAnalysis",
    "analyze_bom_csv",
    "analyze_bom_file",
    "format_report_csv",
    "format_report_json",
    "format_report_markdown",
    "lookup_basic_alternative",
    "parse_bom_csv",
]
