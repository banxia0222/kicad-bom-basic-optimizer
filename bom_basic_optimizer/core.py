"""Core BOM parsing, health auditing, passive normalization, and JLCPCB Basic mapping engine.

Zero third-party dependencies (Python 3.9+ stdlib only).
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_FEEDER_FEE_USD = 3.0
PARTCROSSLAB_URL = "https://partcrosslab.pages.dev"

_CATALOG_PATH = Path(__file__).resolve().parent / "data" / "jlcpcb_basic_catalog.json"
_CATALOG_CACHE: Optional[Dict[str, Any]] = None

_HEADER_ALIASES: Dict[str, Tuple[str, ...]] = {
    "refdes": (
        "reference",
        "references",
        "designator",
        "designators",
        "ref",
        "refdes",
        "part reference",
        "reference(s)",
    ),
    "qty": ("qty", "quantity", "count", "q'ty"),
    "value": ("value", "comment", "val", "component value", "spec"),
    "footprint": ("footprint", "package", "case", "pcb footprint", "smd package"),
    "mpn": (
        "mpn",
        "manufacturer part",
        "manufacturer part number",
        "manufacturer_part_number",
        "mfr part",
        "mfr part #",
        "mfr. no",
        "part number",
        "pn",
        "manufacturer part number 1",
    ),
    "manufacturer": (
        "manufacturer",
        "mfr",
        "mfg",
        "brand",
        "manufacturer 1",
    ),
    "lcsc": (
        "lcsc",
        "lcsc part",
        "lcsc part #",
        "lcsc #",
        "lcsc_part",
        "jlcpcb part #",
        "supplier part",
        "supplier part number 1",
    ),
    "description": ("description", "desc", "notes", "note", "remarks"),
    "dnp": ("dnp", "do not populate", "populate", "fitted", "mount", "exclude from bom"),
}

_PLACEHOLDER_RE = re.compile(
    r"(?:^|[\s_/,-])(?:TBD|TBC|TODO|FIXME|XXX|\?\?\?|PLACEHOLDER|N/?A_TBD)(?:$|[\s_/,-])",
    re.IGNORECASE,
)

_PACKAGE_RE = re.compile(r"(?<![0-9])(0402|0603|0805|1206)(?![0-9])")
_KNOWN_PACKAGES_RE = re.compile(
    r"\b(0201|0402|0603|0805|1206|1210|2512|SOT-23(?:-[356])?|SOT-223|SOD-123|SOD-323|"
    r"SMA|SMB|SMC|SOIC-\d+(?:-EP)?|SOP-\d+|SSOP-\d+|TSSOP-\d+|QFN-\d+|LQFP-\d+|"
    r"SMD-\d+|TO-252|TO-263(?:-\d+)?|USB-C|TYPE-C|MODULE|WROOM|LGA-\d+|DFN-\d+)\b",
    re.IGNORECASE,
)

_SAMSUNG_PKG_MAP = {"05": "0402", "10": "0603", "21": "0805", "31": "1206"}
_MURATA_PKG_MAP = {"15": "0402", "18": "0603", "21": "0805", "31": "1206"}
_TDK_PKG_MAP = {"1005": "0402", "1608": "0603", "2012": "0805", "3216": "1206"}

_SAMSUNG_DIELECTRIC = {"C": "C0G", "B": "X7R", "A": "X5R", "F": "Y5V"}
_SAMSUNG_VOLT = {"Q": 6.3, "P": 10.0, "O": 16.0, "A": 25.0, "L": 35.0, "B": 50.0, "C": 100.0}
_MURATA_DIELECTRIC = {"5C": "C0G", "R7": "X7R", "R6": "X5R", "F5": "Y5V"}
_EIA_VOLT = {"0J": 6.3, "1A": 10.0, "1C": 16.0, "1E": 25.0, "1V": 35.0, "1H": 50.0, "2A": 100.0}
_YAGEO_VOLT = {"5": 6.3, "6": 10.0, "7": 16.0, "8": 25.0, "9": 50.0, "0": 100.0}
_TOL_LETTER_MAP = {"B": 0.1, "C": 0.25, "D": 0.5, "F": 1.0, "G": 2.0, "J": 5.0, "K": 10.0, "M": 20.0}
_DIELECTRIC_RANK = {"Y5V": 0, "Z5U": 0, "X5R": 1, "X7R": 2, "X7S": 2, "C0G": 3, "NP0": 3}

_PACKAGE_ORDER = ("0402", "0603", "0805", "1206")


@dataclass
class BomRow:
    row_index: int
    refdes_list: List[str]
    refdes_raw: str
    qty: int
    qty_raw: str
    value: str
    footprint: str
    mpn: str
    manufacturer: str
    lcsc: str
    description: str
    dnp: bool = False
    raw: Dict[str, str] = field(default_factory=dict)


@dataclass
class HealthIssue:
    severity: str  # "error" | "warning" | "info"
    code: str
    refdes: str
    message: str


@dataclass
class BasicMatch:
    grade: str  # "A↑" | "A±" | "A↓" | "B"
    basic_lcsc: str
    basic_mpn: str
    basic_manufacturer: str
    basic_package: str
    basic_description: str
    category: str
    reason: str
    caution: str
    already_basic: bool = False
    confidence: str = "high"


@dataclass
class RowAnalysis:
    row: BomRow
    normalized_spec: str
    status: str  # "already_basic" | "optimizable" | "keep_extended" | "dnp"
    match: Optional[BasicMatch]
    feeder_fee_before_usd: float
    feeder_fee_after_usd: float
    savings_usd: float


@dataclass
class BomReport:
    source_name: str
    detected_format: str
    total_bom_lines: int
    active_bom_lines: int
    dnp_lines: int
    already_basic_lines: int
    optimizable_lines: int
    drop_in_optimizable_lines: int
    review_required_lines: int
    keep_extended_lines: int
    feeder_fee_per_part_usd: float
    estimated_feeder_fee_before_usd: float
    estimated_feeder_fee_after_usd: float
    estimated_savings_usd: float
    drop_in_savings_usd: float
    grade_counts: Dict[str, int]
    health_issues: List[HealthIssue]
    rows: List[RowAnalysis]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_name": self.source_name,
            "detected_format": self.detected_format,
            "summary": {
                "total_bom_lines": self.total_bom_lines,
                "active_bom_lines": self.active_bom_lines,
                "dnp_lines": self.dnp_lines,
                "already_basic_lines": self.already_basic_lines,
                "optimizable_lines": self.optimizable_lines,
                "drop_in_optimizable_lines": self.drop_in_optimizable_lines,
                "review_required_lines": self.review_required_lines,
                "keep_extended_lines": self.keep_extended_lines,
                "feeder_fee_per_part_usd": self.feeder_fee_per_part_usd,
                "estimated_feeder_fee_before_usd": self.estimated_feeder_fee_before_usd,
                "estimated_feeder_fee_after_usd": self.estimated_feeder_fee_after_usd,
                "estimated_savings_usd": self.estimated_savings_usd,
                "drop_in_savings_usd": self.drop_in_savings_usd,
                "grade_counts": self.grade_counts,
            },
            "health_issues": [asdict(i) for i in self.health_issues],
            "rows": [
                {
                    "row_index": r.row.row_index,
                    "refdes": r.row.refdes_raw,
                    "qty": r.row.qty,
                    "value": r.row.value,
                    "footprint": r.row.footprint,
                    "mpn": r.row.mpn,
                    "manufacturer": r.row.manufacturer,
                    "lcsc": r.row.lcsc,
                    "dnp": r.row.dnp,
                    "normalized_spec": r.normalized_spec,
                    "status": r.status,
                    "feeder_fee_before_usd": r.feeder_fee_before_usd,
                    "feeder_fee_after_usd": r.feeder_fee_after_usd,
                    "savings_usd": r.savings_usd,
                    "match": asdict(r.match) if r.match else None,
                }
                for r in self.rows
            ],
            "partcrosslab_url": PARTCROSSLAB_URL,
        }


def load_catalog() -> Dict[str, Any]:
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        with _CATALOG_PATH.open("r", encoding="utf-8") as fh:
            _CATALOG_CACHE = json.load(fh)
    return _CATALOG_CACHE


def _build_basic_lcsc_index(catalog: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for pkg, entries in catalog.get("resistors", {}).items():
        for ohms_key, item in entries.items():
            lcsc = item["lcsc"].upper()
            index[lcsc] = {
                "lcsc": lcsc,
                "mpn": item["mpn"],
                "manufacturer": item["manufacturer"],
                "package": pkg,
                "description": item["description"],
                "category": "SMD Resistor",
            }
    for pkg, entries in catalog.get("capacitors", {}).items():
        for cap_key, item in entries.items():
            lcsc = item["lcsc"].upper()
            index[lcsc] = {
                "lcsc": lcsc,
                "mpn": item["mpn"],
                "manufacturer": item["manufacturer"],
                "package": pkg,
                "description": item["description"],
                "category": "SMD MLCC",
            }
    for comp in catalog.get("components", []):
        lcsc = comp["basic_lcsc"].upper()
        index[lcsc] = {
            "lcsc": lcsc,
            "mpn": comp["basic_mpn"],
            "manufacturer": comp["basic_manufacturer"],
            "package": comp["basic_package"],
            "description": comp["basic_description"],
            "category": comp["category"],
        }
    return index


def _clean_header(header: str) -> str:
    return re.sub(r"\s+", " ", header.strip().lstrip("\ufeff").lower())


def _detect_format(raw_headers: List[str]) -> str:
    cleaned = {_clean_header(h) for h in raw_headers}
    if "reference" in cleaned and "footprint" in cleaned:
        return "kicad"
    if "designator" in cleaned and "comment" in cleaned and any("lcsc" in h for h in cleaned):
        return "easyeda"
    if "designator" in cleaned and "comment" in cleaned:
        return "altium"
    return "generic"


def _map_columns(raw_headers: List[str]) -> Dict[str, str]:
    mapped: Dict[str, str] = {}
    cleaned_map = {h: _clean_header(h) for h in raw_headers}
    for canonical, aliases in _HEADER_ALIASES.items():
        for raw_h, clean_h in cleaned_map.items():
            if clean_h in aliases and canonical not in mapped:
                mapped[canonical] = raw_h
                break
        if canonical not in mapped:
            for raw_h, clean_h in cleaned_map.items():
                if any(alias in clean_h for alias in aliases) and canonical not in mapped:
                    mapped[canonical] = raw_h
                    break
    return mapped


def _expand_refdes(refdes_raw: str) -> List[str]:
    if not refdes_raw.strip():
        return []
    tokens = re.split(r"[\s,;]+", refdes_raw.strip())
    expanded: List[str] = []
    range_re = re.compile(r"^([A-Za-z_]+)(\d+)(?:-|\.\.)([A-Za-z_]*)(\d+)$")
    for token in tokens:
        if not token:
            continue
        m = range_re.match(token)
        if m:
            prefix, start_s, end_prefix, end_s = m.groups()
            if not end_prefix or end_prefix.upper() == prefix.upper():
                start_i, end_i = int(start_s), int(end_s)
                if start_i <= end_i and (end_i - start_i) <= 500:
                    for idx in range(start_i, end_i + 1):
                        expanded.append(f"{prefix}{idx}")
                    continue
        expanded.append(token)
    return expanded


def _is_dnp_row(dnp_cell: str, value: str, description: str, dnp_header: str) -> bool:
    clean_cell = dnp_cell.strip().lower()
    clean_header = _clean_header(dnp_header) if dnp_header else ""
    if clean_header in ("populate", "fitted", "mount"):
        if clean_cell in ("no", "false", "0", "n", "dnp", "nf", "not mounted", "do not populate"):
            return True
    else:
        if clean_cell in ("yes", "true", "1", "y", "dnp", "nf", "nm", "do not populate", "not mounted"):
            return True
    combined = f"{value} {description}"
    if re.search(r"\b(?:DNP|DO NOT POPULATE|NOT MOUNTED|NO FIT)\b", combined, re.IGNORECASE):
        return True
    return False


def _normalize_lcsc(text: str) -> str:
    m = re.search(r"\b(C\d{4,9})\b", text.upper())
    return m.group(1) if m else ""


def parse_bom_csv(csv_text: str) -> Tuple[List[BomRow], str]:
    """Parse KiCad, Altium, EasyEDA, or generic BOM CSV/TSV text."""
    stripped = csv_text.lstrip("\ufeff").strip()
    if not stripped:
        return [], "generic"

    sample = stripped[:4096]
    delimiter = ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        first_line = stripped.splitlines()[0]
        if "\t" in first_line and "," not in first_line:
            delimiter = "\t"
        elif ";" in first_line and "," not in first_line:
            delimiter = ";"

    reader = csv.DictReader(io.StringIO(stripped), delimiter=delimiter)
    if not reader.fieldnames:
        return [], "generic"

    raw_headers = [h for h in reader.fieldnames if h is not None]
    detected_format = _detect_format(raw_headers)
    col_map = _map_columns(raw_headers)
    dnp_header = col_map.get("dnp", "")

    rows: List[BomRow] = []
    for idx, raw_row in enumerate(reader, start=1):
        clean_raw = {k: (v.strip() if isinstance(v, str) else "") for k, v in raw_row.items() if k is not None}
        if not any(clean_raw.values()):
            continue

        refdes_raw = clean_raw.get(col_map.get("refdes", ""), "")
        refdes_list = _expand_refdes(refdes_raw)
        qty_raw = clean_raw.get(col_map.get("qty", ""), "")
        qty = len(refdes_list) if refdes_list else 1
        if qty_raw:
            qty_m = re.search(r"\d+", qty_raw)
            if qty_m:
                qty = int(qty_m.group(0))

        value = clean_raw.get(col_map.get("value", ""), "")
        footprint = clean_raw.get(col_map.get("footprint", ""), "")
        mpn = clean_raw.get(col_map.get("mpn", ""), "")
        manufacturer = clean_raw.get(col_map.get("manufacturer", ""), "")
        lcsc_raw = clean_raw.get(col_map.get("lcsc", ""), "")
        lcsc = _normalize_lcsc(lcsc_raw)
        description = clean_raw.get(col_map.get("description", ""), "")
        dnp_cell = clean_raw.get(dnp_header, "") if dnp_header else ""
        dnp = _is_dnp_row(dnp_cell, value, description, dnp_header)

        rows.append(
            BomRow(
                row_index=idx,
                refdes_list=refdes_list,
                refdes_raw=refdes_raw,
                qty=qty,
                qty_raw=qty_raw,
                value=value,
                footprint=footprint,
                mpn=mpn,
                manufacturer=manufacturer,
                lcsc=lcsc,
                description=description,
                dnp=dnp,
                raw=clean_raw,
            )
        )
    return rows, detected_format


def _extract_smd_package(*texts: str) -> Optional[str]:
    joined = " ".join(t for t in texts if t)
    m = _PACKAGE_RE.search(joined)
    if m:
        return m.group(1)

    # Samsung MLCC: CL05 / CL10 / CL21 / CL31
    m = re.search(r"\bCL(05|10|21|31)[A-Z]", joined, re.IGNORECASE)
    if m:
        return _SAMSUNG_PKG_MAP[m.group(1)]

    # Murata MLCC: GRM15 / GRM18 / GRM21 / GRM31
    m = re.search(r"\bGRM(15|18|21|31)[0-9A-Z]", joined, re.IGNORECASE)
    if m:
        return _MURATA_PKG_MAP[m.group(1)]

    # TDK MLCC: C1005 / C1608 / C2012 / C3216
    m = re.search(r"\bC(1005|1608|2012|3216)(?:C0G|NP0|X7R|X5R|JB|CH)", joined, re.IGNORECASE)
    if m:
        return _TDK_PKG_MAP[m.group(1)]

    return None


def _decode_eia_resistor_code(code: str) -> Optional[float]:
    code = code.upper()
    if code in ("000", "0000", "0R0", "0R"):
        return 0.0
    if "R" in code:
        try:
            return float(code.replace("R", "."))
        except ValueError:
            return None
    if len(code) == 3 and code.isdigit():
        return int(code[:2]) * (10 ** int(code[2]))
    if len(code) == 4 and code.isdigit():
        return int(code[:3]) * (10 ** int(code[3]))
    return None


def _parse_resistance_ohms(text: str) -> Optional[float]:
    if not text:
        return None

    # Yageo RC/AC/RT style e.g. RC0603FR-0710KL, RC0402JR-074K7L, RC0603FR-070RL
    m = re.search(r"\b(?:RC|AC|RT)(?:0402|0603|0805|1206)[A-Z]{2}-\d{2}(\d+(?:\.\d+)?[RKM]\d*|\d+[RKM])L?\b", text, re.IGNORECASE)
    if m:
        val = _parse_resistance_token(m.group(1))
        if val is not None:
            return val

    # UniOhm style e.g. 0603WAF1002T5E, 0402WGF4701TCE
    m = re.search(r"\b(?:0402|0603|0805|1206)W[A-Z0-9]{2}(\d{3,4}|[0-9R]{3,4})T[0-9A-Z]{2}\b", text, re.IGNORECASE)
    if m:
        val = _decode_eia_resistor_code(m.group(1))
        if val is not None:
            return val

    # Vishay CRCW style e.g. CRCW060310K0FKEA, CRCW04024K70FKED, CRCW06030000Z0EA
    m = re.search(r"\bCRCW(?:0402|0603|0805|1206)([0-9RKM]{4})[A-Z]{4}\b", text, re.IGNORECASE)
    if m:
        code = m.group(1).upper()
        if code == "0000":
            return 0.0
        val = _parse_resistance_token(code)
        if val is not None:
            return val

    # Direct human-readable token e.g. "10k", "4.7k", "4k7", "5.1kΩ", "100R", "0R", "22 ohm", "1M"
    for match in re.finditer(
        r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?\s*(?:[kKmM](?:[oO]hm|Ω)?|[rR]|Ω|[oO]hms?)|\d+[rRkKmM]\d+)(?![A-Za-z0-9])",
        text,
    ):
        val = _parse_resistance_token(match.group(1))
        if val is not None:
            return val

    # Plain numeric like "100" or "0" when the whole value string is just a number
    stripped = text.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", stripped):
        return float(stripped)
    return None


def _parse_resistance_token(token: str) -> Optional[float]:
    t = re.sub(r"\s+", "", token).replace("Ω", "R")
    t = re.sub(r"ohms?$", "R", t, flags=re.IGNORECASE)

    # Inline decimal letter e.g. 4K7, 2M2, 0R0, 5R1, 10K0
    m = re.fullmatch(r"(\d+)([rRkKmM])(\d+)", t)
    if m:
        whole, unit, frac = m.groups()
        num = float(f"{whole}.{frac}")
        mult = {"r": 1.0, "k": 1e3, "m": 1e6}[unit.lower()]
        return num * mult

    # Trailing unit e.g. 10k, 4.7K, 100R, 0R, 1M
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([rRkKmM])", t)
    if m:
        num, unit = float(m.group(1)), m.group(2).lower()
        mult = {"r": 1.0, "k": 1e3, "m": 1e6}[unit]
        return num * mult

    return None


def _format_ohms_key(ohms: float) -> str:
    if ohms == 0:
        return "0"
    if abs(ohms - round(ohms)) < 1e-6:
        return str(int(round(ohms)))
    return f"{ohms:g}"


def _format_ohms_human(ohms: float) -> str:
    if ohms == 0:
        return "0R"
    if ohms >= 1e6:
        return f"{ohms / 1e6:g}M"
    if ohms >= 1e3:
        return f"{ohms / 1e3:g}k"
    return f"{ohms:g}R"


def _decode_eia_cap_pf(code: str) -> Optional[float]:
    code = code.upper()
    if "R" in code:
        try:
            return float(code.replace("R", "."))
        except ValueError:
            return None
    if len(code) == 3 and code.isdigit():
        return int(code[:2]) * (10 ** int(code[2]))
    return None


def _parse_capacitance_pf(text: str) -> Optional[float]:
    if not text:
        return None

    # Explicit human-readable value: 100nF, 0.1uF, 22pF, 4u7, 10µF, 1n
    for match in re.finditer(
        r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?\s*(?:pF|nF|uF|µF|μF|[pnuµμ])|\d+[pnuµμ]\d+)(?![A-Za-z0-9])",
        text,
        re.IGNORECASE,
    ):
        token = re.sub(r"\s+", "", match.group(1)).replace("µ", "u").replace("μ", "u")
        # Inline e.g. 4u7, 2n2, 4p7
        m_inline = re.fullmatch(r"(\d+)([pnu])(\d+)", token, re.IGNORECASE)
        if m_inline:
            whole, unit, frac = m_inline.groups()
            val = float(f"{whole}.{frac}")
            mult = {"p": 1.0, "n": 1e3, "u": 1e6}[unit.lower()]
            return val * mult

        m_unit = re.fullmatch(r"(\d+(?:\.\d+)?)([pnu])f?", token, re.IGNORECASE)
        if m_unit:
            val, unit = float(m_unit.group(1)), m_unit.group(2).lower()
            mult = {"p": 1.0, "n": 1e3, "u": 1e6}[unit]
            return val * mult

    # Samsung CLxx[A-Z]104...
    m = re.search(r"\bCL(?:05|10|21|31)[A-Z]([0-9R]{3})[A-Z]", text, re.IGNORECASE)
    if m:
        return _decode_eia_cap_pf(m.group(1))

    # Murata GRM...1H104K...
    m = re.search(r"\bGRM\d{2}[0-9A-Z](?:5C|R7|R6|F5)(?:0J|1A|1C|1E|1V|1H|2A)([0-9R]{3})[A-Z]", text, re.IGNORECASE)
    if m:
        return _decode_eia_cap_pf(m.group(1))

    # Yageo CC0603KRX7R9BB104
    m = re.search(r"\bCC(?:0402|0603|0805|1206)[A-Z]{2}(?:NPO|C0G|X7R|X5R|Y5V)\d[A-Z]{2}([0-9R]{3})\b", text, re.IGNORECASE)
    if m:
        return _decode_eia_cap_pf(m.group(1))

    # TDK C1608X7R1H104K080AA
    m = re.search(r"\bC(?:1005|1608|2012|3216)(?:C0G|NP0|X7R|X5R)(?:0J|1A|1C|1E|1V|1H|2A)([0-9R]{3})[A-Z]", text, re.IGNORECASE)
    if m:
        return _decode_eia_cap_pf(m.group(1))

    return None


def _format_cap_key(pf: float) -> str:
    if pf >= 1e6:
        val = pf / 1e6
        return f"{val:g}u"
    if pf >= 1e3:
        val = pf / 1e3
        return f"{val:g}n"
    return f"{pf:g}p"


def _extract_voltage(*texts: str) -> Optional[float]:
    joined = " ".join(t for t in texts if t)
    m = re.search(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*V(?:DC)?\b", joined, re.IGNORECASE)
    if m:
        return float(m.group(1))

    # Samsung CLxxB104K[B]8NNNC (10th char is voltage code)
    m = re.search(r"\bCL(?:05|10|21|31)[A-Z][0-9R]{3}[A-Z]([QPOALBC])\d", joined, re.IGNORECASE)
    if m:
        return _SAMSUNG_VOLT.get(m.group(1).upper())

    # Murata / TDK EIA voltage code
    m = re.search(r"\b(?:GRM\d{2}[0-9A-Z](?:5C|R7|R6|F5)|C(?:1005|1608|2012|3216)(?:C0G|NP0|X7R|X5R))(0J|1A|1C|1E|1V|1H|2A)", joined, re.IGNORECASE)
    if m:
        return _EIA_VOLT.get(m.group(1).upper())

    # Yageo CC0603KRX7R[9]BB104
    m = re.search(r"\bCC(?:0402|0603|0805|1206)[A-Z]{2}(?:NPO|C0G|X7R|X5R|Y5V)(\d)[A-Z]{2}[0-9R]{3}\b", joined, re.IGNORECASE)
    if m:
        return _YAGEO_VOLT.get(m.group(1))

    return None


def _extract_dielectric(*texts: str) -> Optional[str]:
    joined = " ".join(t for t in texts if t)
    m = re.search(r"\b(C0G|NP0|NPO|X7R|X7S|X5R|Y5V|Z5U)\b", joined, re.IGNORECASE)
    if m:
        d = m.group(1).upper()
        return "C0G" if d in ("NP0", "NPO") else d

    m = re.search(r"\bCL(?:05|10|21|31)([CBAF])[0-9R]{3}", joined, re.IGNORECASE)
    if m:
        return _SAMSUNG_DIELECTRIC.get(m.group(1).upper())

    m = re.search(r"\bGRM\d{2}[0-9A-Z](5C|R7|R6|F5)", joined, re.IGNORECASE)
    if m:
        return _MURATA_DIELECTRIC.get(m.group(1).upper())

    return None


def _extract_tolerance_pct(*texts: str) -> Optional[float]:
    joined = " ".join(t for t in texts if t)
    m = re.search(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*%", joined)
    if m:
        return float(m.group(1))

    # Yageo RC0603[F]R-..., UniOhm 0603WA[F]..., Vishay CRCW060310K0[F]KEA
    m = re.search(r"\b(?:RC|AC|RT)(?:0402|0603|0805|1206)([BCDFGJKM])[A-Z]-", joined, re.IGNORECASE)
    if m:
        return _TOL_LETTER_MAP.get(m.group(1).upper())
    m = re.search(r"\b(?:0402|0603|0805|1206)W[A-Z0-9]([BCDFGJKM])\d", joined, re.IGNORECASE)
    if m:
        return _TOL_LETTER_MAP.get(m.group(1).upper())
    m = re.search(r"\bCRCW(?:0402|0603|0805|1206)[0-9RKM]{4}([BCDFGJKM])[A-Z]{3}\b", joined, re.IGNORECASE)
    if m:
        return _TOL_LETTER_MAP.get(m.group(1).upper())
    return None


def _infer_passive_kind(refdes_list: List[str], footprint: str, mpn: str, value: str, description: str) -> Optional[str]:
    if refdes_list:
        first_prefix = re.match(r"^([A-Za-z]+)", refdes_list[0])
        if first_prefix:
            p = first_prefix.group(1).upper()
            if p in ("R", "RN"):
                return "resistor"
            if p == "C":
                return "capacitor"

    joined = f"{footprint} {mpn} {value} {description}"
    if re.search(r"\b(?:CL(?:05|10|21|31)|GRM(?:15|18|21|31)|CC(?:0402|0603|0805|1206)|C(?:1005|1608|2012|3216))\w*", joined, re.IGNORECASE):
        return "capacitor"
    if re.search(r"\b(?:RC(?:0402|0603|0805|1206)|CRCW(?:0402|0603|0805|1206)|(?:0402|0603|0805|1206)W[A-Z0-9]{2})\w*", joined, re.IGNORECASE):
        return "resistor"
    if re.search(r"\b(?:MLCC|CAPACITOR|C0G|NP0|X7R|X5R)\b|C_(?:0402|0603|0805|1206)", joined, re.IGNORECASE):
        return "capacitor"
    if re.search(r"\b(?:RESISTOR|RES)\b|R_(?:0402|0603|0805|1206)", joined, re.IGNORECASE):
        return "resistor"
    if re.search(r"\b\d+(?:\.\d+)?\s*(?:pF|nF|uF|µF|μF)\b", joined, re.IGNORECASE):
        return "capacitor"
    if re.search(r"\b\d+(?:\.\d+)?\s*(?:[kKmM]?Ω|[kKmM]?ohm|[rR])\b|\b\d+[rRkKmM]\d+\b", joined, re.IGNORECASE):
        return "resistor"
    return None


def _match_resistor(
    package: Optional[str],
    ohms: float,
    tol_pct: Optional[float],
    catalog: Dict[str, Any],
) -> Optional[BasicMatch]:
    ohms_key = _format_ohms_key(ohms)
    resistors = catalog.get("resistors", {})
    pkg_used = package
    footprint_changed = False

    entry = resistors.get(pkg_used, {}).get(ohms_key) if pkg_used else None
    if entry is None:
        # Look for closest package carrying the same resistance value
        for candidate_pkg in _PACKAGE_ORDER:
            if ohms_key in resistors.get(candidate_pkg, {}):
                entry = resistors[candidate_pkg][ohms_key]
                pkg_used = candidate_pkg
                footprint_changed = package is not None and package != candidate_pkg
                break
    if entry is None:
        return None

    basic_tol = float(entry.get("tolerance_pct", 1.0))
    human_val = _format_ohms_human(ohms)

    if footprint_changed or package is None:
        grade = "B"
        reason = f"JLCPCB Basic {human_val} {basic_tol:g}% resistor available in {pkg_used}."
        caution = (
            f"Package differs ({package} -> {pkg_used}); update PCB footprint before substituting."
            if footprint_changed
            else f"Source SMD package not specified; confirm {pkg_used} footprint matches PCB."
        )
        confidence = "medium"
    elif tol_pct is not None and tol_pct < basic_tol:
        grade = "A↓"
        reason = f"Normalized {package} {human_val} resistor to JLCPCB Basic {entry['lcsc']} ({basic_tol:g}%)."
        caution = f"Basic part is {basic_tol:g}% tolerance vs requested {tol_pct:g}%; keep Extended for precision sensing or feedback dividers."
        confidence = "high"
    elif tol_pct is not None and tol_pct > basic_tol:
        grade = "A↑"
        reason = f"Normalized {package} {human_val} ({tol_pct:g}%) to tighter {basic_tol:g}% JLCPCB Basic {entry['lcsc']}."
        caution = "Verify pulse power rating if used as current-limiting or snubber resistor."
        confidence = "high"
    else:
        grade = "A±"
        reason = f"Drop-in {package} {human_val} {basic_tol:g}% JLCPCB Basic equivalent ({entry['lcsc']})."
        caution = "Standard thick-film resistor; verify power dissipation within package rating."
        confidence = "high"

    return BasicMatch(
        grade=grade,
        basic_lcsc=entry["lcsc"],
        basic_mpn=entry["mpn"],
        basic_manufacturer=entry["manufacturer"],
        basic_package=pkg_used or "",
        basic_description=entry["description"],
        category="SMD Resistor",
        reason=reason,
        caution=caution,
        already_basic=False,
        confidence=confidence,
    )


def _match_capacitor(
    package: Optional[str],
    pf: float,
    req_voltage: Optional[float],
    req_dielectric: Optional[str],
    catalog: Dict[str, Any],
) -> Optional[BasicMatch]:
    cap_key = _format_cap_key(pf)
    capacitors = catalog.get("capacitors", {})
    pkg_used = package
    footprint_changed = False

    entry = capacitors.get(pkg_used, {}).get(cap_key) if pkg_used else None
    if entry is None:
        for candidate_pkg in _PACKAGE_ORDER:
            if cap_key in capacitors.get(candidate_pkg, {}):
                entry = capacitors[candidate_pkg][cap_key]
                pkg_used = candidate_pkg
                footprint_changed = package is not None and package != candidate_pkg
                break
    if entry is None:
        return None

    basic_v = float(entry["voltage_v"])
    basic_diel = str(entry["dielectric"])
    req_rank = _DIELECTRIC_RANK.get(req_dielectric, -1) if req_dielectric else -1
    basic_rank = _DIELECTRIC_RANK.get(basic_diel, 1)

    cautions: List[str] = []
    if footprint_changed:
        grade = "B"
        reason = f"JLCPCB Basic {cap_key}F MLCC available in {pkg_used} ({basic_v:g}V {basic_diel})."
        cautions.append(f"Package differs ({package} -> {pkg_used}); update PCB footprint before substituting.")
        confidence = "medium"
    elif package is None:
        grade = "B"
        reason = f"JLCPCB Basic {cap_key}F MLCC available in {pkg_used} ({basic_v:g}V {basic_diel})."
        cautions.append(f"Source package missing; verify PCB footprint is {pkg_used}.")
        confidence = "medium"
    elif req_dielectric == "C0G" and basic_diel != "C0G":
        grade = "B"
        reason = f"Only Class-II ({basic_diel}) JLCPCB Basic option found for {package} {cap_key}F."
        cautions.append("Do NOT swap C0G/NP0 timing, crystal load, or precision filter caps to X5R/X7R.")
        confidence = "medium"
    else:
        downgraded = False
        upgraded = False
        if req_voltage is not None:
            if basic_v < req_voltage:
                downgraded = True
                cautions.append(
                    f"Lower voltage rating ({basic_v:g}V Basic vs {req_voltage:g}V requested); verify rail voltage and DC-bias derating."
                )
            elif basic_v > req_voltage:
                upgraded = True
        if req_dielectric is not None and req_rank >= 0:
            if basic_rank < req_rank:
                downgraded = True
                cautions.append(
                    f"Dielectric shifts from {req_dielectric} to {basic_diel} (85C upper temp limit vs 125C)."
                )
            elif basic_rank > req_rank:
                upgraded = True

        if downgraded:
            grade = "A↓"
            reason = f"Normalized {package} {cap_key}F MLCC to JLCPCB Basic {entry['lcsc']} ({basic_v:g}V {basic_diel}) with margin downgrade."
        elif upgraded:
            grade = "A↑"
            reason = f"Upgraded {package} {cap_key}F MLCC to higher-margin JLCPCB Basic {entry['lcsc']} ({basic_v:g}V {basic_diel})."
        else:
            grade = "A±"
            reason = f"Drop-in {package} {cap_key}F {basic_v:g}V {basic_diel} JLCPCB Basic equivalent ({entry['lcsc']})."
        confidence = "high"

    if basic_v <= 10.0 and pf >= 4.7e6:
        cautions.append(f"Check MLCC DC-bias derating on {basic_v:g}V {package} {cap_key}F bulk capacitor.")

    return BasicMatch(
        grade=grade,
        basic_lcsc=entry["lcsc"],
        basic_mpn=entry["mpn"],
        basic_manufacturer=entry["manufacturer"],
        basic_package=pkg_used or "",
        basic_description=entry["description"],
        category="SMD MLCC",
        reason=reason,
        caution=" ".join(cautions) if cautions else "Verify rail voltage and DC-bias margin against datasheet.",
        already_basic=False,
        confidence=confidence,
    )


def _match_component_catalog(
    mpn: str,
    value: str,
    description: str,
    footprint: str,
    catalog: Dict[str, Any],
) -> Optional[BasicMatch]:
    search_blob = " ".join(x for x in (mpn, value, description) if x)
    if not search_blob.strip():
        return None

    for comp in catalog.get("components", []):
        for rule in comp.get("patterns", []):
            if re.search(rule["regex"], search_blob, re.IGNORECASE):
                grade = rule["grade"]
                caution = rule["caution"]
                # Check obvious package mismatch for SMD diodes/ICs
                basic_pkg = comp["basic_package"]
                if footprint and basic_pkg.upper() == "SOT-223" and re.search(r"\b(TO-252|DPAK|SOT-89|SOIC)\b", footprint, re.IGNORECASE):
                    grade = "B"
                    caution = f"Footprint mismatch ({footprint} vs {basic_pkg}). " + caution
                elif footprint and basic_pkg.upper() == "SMA" and re.search(r"\b(SMB|SMC|SOD-123)\b", footprint, re.IGNORECASE):
                    grade = "B"
                    caution = f"Footprint mismatch ({footprint} vs {basic_pkg}). " + caution
                return BasicMatch(
                    grade=grade,
                    basic_lcsc=comp["basic_lcsc"],
                    basic_mpn=comp["basic_mpn"],
                    basic_manufacturer=comp["basic_manufacturer"],
                    basic_package=basic_pkg,
                    basic_description=comp["basic_description"],
                    category=comp["category"],
                    reason=rule["reason"],
                    caution=caution,
                    already_basic=False,
                    confidence="high" if grade.startswith("A") else "medium",
                )
    return None


def lookup_basic_alternative(
    query: str,
    package: str = "",
    value: str = "",
    lcsc: str = "",
    description: str = "",
    refdes_list: Optional[List[str]] = None,
) -> Tuple[str, Optional[BasicMatch]]:
    """Look up a single part or passive specification in the JLCPCB Basic reference catalog.

    Returns (normalized_spec, BasicMatch | None).
    """
    catalog = load_catalog()
    basic_index = _build_basic_lcsc_index(catalog)

    norm_lcsc = _normalize_lcsc(lcsc) or _normalize_lcsc(query)
    if norm_lcsc and norm_lcsc in basic_index:
        info = basic_index[norm_lcsc]
        return (
            f"Basic {info['lcsc']} ({info['package']})",
            BasicMatch(
                grade="A±",
                basic_lcsc=info["lcsc"],
                basic_mpn=info["mpn"],
                basic_manufacturer=info["manufacturer"],
                basic_package=info["package"],
                basic_description=info["description"],
                category=info["category"],
                reason=f"{info['lcsc']} is already a JLCPCB Basic part ($0 feeder fee).",
                caution="Verify live JLCPCB stock status before placing PCBA order.",
                already_basic=True,
                confidence="high",
            ),
        )

    # Try component catalog first for explicit semiconductor/protection MPNs
    comp_match = _match_component_catalog(query, value, description, package, catalog)
    if comp_match is not None:
        return (f"{comp_match.category}: {query or value}", comp_match)

    # Passive normalization (resistors & MLCCs)
    kind = _infer_passive_kind(refdes_list or [], package, query, value, description)
    smd_pkg = _extract_smd_package(package, query, value, description)

    if kind == "resistor":
        ohms = _parse_resistance_ohms(value)
        if ohms is None:
            ohms = _parse_resistance_ohms(query)
        if ohms is None:
            ohms = _parse_resistance_ohms(description)
        tol = _extract_tolerance_pct(value, query, description)
        if ohms is not None:
            spec = f"RES {smd_pkg or '?'} {_format_ohms_human(ohms)}" + (f" {tol:g}%" if tol is not None else "")
            return spec, _match_resistor(smd_pkg, ohms, tol, catalog)

    if kind == "capacitor":
        pf = _parse_capacitance_pf(value)
        if pf is None:
            pf = _parse_capacitance_pf(query)
        if pf is None:
            pf = _parse_capacitance_pf(description)
        volts = _extract_voltage(value, query, description)
        diel = _extract_dielectric(value, query, description)
        if pf is not None:
            parts = ["MLCC", smd_pkg or "?", f"{_format_cap_key(pf)}F"]
            if volts is not None:
                parts.append(f"{volts:g}V")
            if diel:
                parts.append(diel)
            spec = " ".join(parts)
            return spec, _match_capacitor(smd_pkg, pf, volts, diel, catalog)

    # Fallback: try passive parsing even if refdes didn't give a hint
    ohms = _parse_resistance_ohms(f"{value} {query}")
    if ohms is not None and smd_pkg:
        tol = _extract_tolerance_pct(value, query, description)
        spec = f"RES {smd_pkg} {_format_ohms_human(ohms)}" + (f" {tol:g}%" if tol is not None else "")
        return spec, _match_resistor(smd_pkg, ohms, tol, catalog)

    pf = _parse_capacitance_pf(f"{value} {query}")
    if pf is not None and smd_pkg:
        volts = _extract_voltage(value, query, description)
        diel = _extract_dielectric(value, query, description)
        spec = f"MLCC {smd_pkg} {_format_cap_key(pf)}F"
        return spec, _match_capacitor(smd_pkg, pf, volts, diel, catalog)

    fallback_spec = (query or value or "Unknown part").strip()
    return fallback_spec, None


def run_health_checks(rows: List[BomRow], analyses: List[RowAnalysis]) -> List[HealthIssue]:
    """Run BOM manufacturing & engineering hygiene checks."""
    issues: List[HealthIssue] = []
    seen_refdes: Dict[str, int] = {}

    for row, analysis in zip(rows, analyses):
        if row.dnp:
            continue

        # 1. Duplicate reference designators (within row or across rows)
        local_seen: set[str] = set()
        for ref in row.refdes_list:
            ref_key = ref.upper()
            if ref_key in local_seen or ref_key in seen_refdes:
                first_row = seen_refdes.get(ref_key, row.row_index)
                issues.append(
                    HealthIssue(
                        severity="error",
                        code="DUPLICATE_REFDES",
                        refdes=ref,
                        message=f"Duplicate reference designator '{ref}' found on BOM line {row.row_index} (first seen on line {first_row}).",
                    )
                )
            else:
                seen_refdes[ref_key] = row.row_index
                local_seen.add(ref_key)

        # 2. Quantity vs refdes count mismatch
        if row.refdes_list and row.qty_raw and row.qty != len(row.refdes_list):
            issues.append(
                HealthIssue(
                    severity="warning",
                    code="QTY_REFDES_MISMATCH",
                    refdes=row.refdes_raw or f"Line {row.row_index}",
                    message=f"Quantity column says {row.qty}, but {len(row.refdes_list)} designators are listed ({row.refdes_raw}).",
                )
            )

        # 3. Placeholder / TBD / missing value & MPN
        combined_fields = f"{row.value} {row.mpn} {row.footprint} {row.description}"
        if _PLACEHOLDER_RE.search(combined_fields) or (not row.value.strip() and not row.mpn.strip()):
            issues.append(
                HealthIssue(
                    severity="error",
                    code="PLACEHOLDER_VALUE",
                    refdes=row.refdes_raw or f"Line {row.row_index}",
                    message=f"BOM line {row.row_index} contains a placeholder/TBD value or is missing both Value and MPN.",
                )
            )

        # 4. Missing recognizable footprint/package
        if not row.footprint.strip() and not _extract_smd_package(row.mpn, row.value, row.description) and not _KNOWN_PACKAGES_RE.search(combined_fields):
            issues.append(
                HealthIssue(
                    severity="warning",
                    code="MISSING_PACKAGE",
                    refdes=row.refdes_raw or f"Line {row.row_index}",
                    message=f"No recognizable package/footprint on BOM line {row.row_index}; JLCPCB SMT matching requires an explicit footprint.",
                )
            )

        # 5. Narrow voltage margin / MLCC DC-bias check
        kind = _infer_passive_kind(row.refdes_list, row.footprint, row.mpn, row.value, row.description)
        if kind == "capacitor":
            pf = _parse_capacitance_pf(f"{row.value} {row.mpn} {row.description}")
            volts = _extract_voltage(row.value, row.mpn, row.description)
            diel = _extract_dielectric(row.value, row.mpn, row.description)
            if volts is not None and (volts <= 6.3 or (volts <= 10.0 and pf is not None and pf >= 4.7e6)):
                issues.append(
                    HealthIssue(
                        severity="warning",
                        code="NARROW_VOLTAGE_MARGIN",
                        refdes=row.refdes_raw or f"Line {row.row_index}",
                        message=(
                            f"Low-voltage MLCC ({volts:g}V, {row.value or row.mpn}) can lose 50-75% effective capacitance under DC bias; "
                            f"confirm rail derating or upsize voltage/case."
                        ),
                    )
                )
            if diel in ("Y5V", "Z5U"):
                issues.append(
                    HealthIssue(
                        severity="warning",
                        code="NARROW_TEMP_DIELECTRIC",
                        refdes=row.refdes_raw or f"Line {row.row_index}",
                        message=f"Unstable capacitor dielectric ({diel}) has severe temperature/voltage capacitance drop; prefer X7R/X5R/C0G.",
                    )
                )

        # 6. Narrow commercial temperature warning if explicitly flagged in text
        if re.search(r"\b(?:0\s*(?:to|\.\.|-)\s*70\s*°?C|0C\s*to\s*70C|commercial\s+temp)\b", combined_fields, re.IGNORECASE):
            issues.append(
                HealthIssue(
                    severity="warning",
                    code="NARROW_TEMP_RANGE",
                    refdes=row.refdes_raw or f"Line {row.row_index}",
                    message=f"Commercial-only temperature spec (0..70C) detected on {row.refdes_raw}; verify ambient envelope for industrial/enclosure use.",
                )
            )

        # 7. Single-source warning on non-passive Extended ICs/modules/connectors
        if analysis.status == "keep_extended" and kind not in ("resistor", "capacitor") and row.mpn.strip():
            issues.append(
                HealthIssue(
                    severity="info",
                    code="SINGLE_SOURCE_PART",
                    refdes=row.refdes_raw or f"Line {row.row_index}",
                    message=f"Single-source Extended component '{row.mpn}' has no bundled Basic equivalent; keep Extended and verify LCSC stock or add an AVL alternate.",
                )
            )

    return issues


def analyze_bom_csv(
    csv_text: str,
    source_name: str = "inline.csv",
    feeder_fee_usd: float = DEFAULT_FEEDER_FEE_USD,
) -> BomReport:
    """Analyze a BOM CSV string and compute JLCPCB Basic optimizations and feeder-fee savings."""
    rows, detected_format = parse_bom_csv(csv_text)
    analyses: List[RowAnalysis] = []

    already_basic_lines = 0
    optimizable_lines = 0
    drop_in_optimizable_lines = 0
    review_required_lines = 0
    keep_extended_lines = 0
    dnp_lines = 0
    grade_counts: Dict[str, int] = {"A↑": 0, "A±": 0, "A↓": 0, "B": 0}

    fee_before_total = 0.0
    fee_after_total = 0.0
    drop_in_savings = 0.0

    for row in rows:
        if row.dnp:
            dnp_lines += 1
            analyses.append(
                RowAnalysis(
                    row=row,
                    normalized_spec="DNP (Not Populated)",
                    status="dnp",
                    match=None,
                    feeder_fee_before_usd=0.0,
                    feeder_fee_after_usd=0.0,
                    savings_usd=0.0,
                )
            )
            continue

        norm_spec, match = lookup_basic_alternative(
            query=row.mpn,
            package=row.footprint,
            value=row.value,
            lcsc=row.lcsc,
            description=row.description,
            refdes_list=row.refdes_list,
        )

        if match is not None and match.already_basic:
            already_basic_lines += 1
            analyses.append(
                RowAnalysis(
                    row=row,
                    normalized_spec=norm_spec,
                    status="already_basic",
                    match=match,
                    feeder_fee_before_usd=0.0,
                    feeder_fee_after_usd=0.0,
                    savings_usd=0.0,
                )
            )
        elif match is not None:
            optimizable_lines += 1
            grade_counts[match.grade] = grade_counts.get(match.grade, 0) + 1
            fee_before_total += feeder_fee_usd
            if match.grade.startswith("A"):
                drop_in_optimizable_lines += 1
                drop_in_savings += feeder_fee_usd
            else:
                review_required_lines += 1
            analyses.append(
                RowAnalysis(
                    row=row,
                    normalized_spec=norm_spec,
                    status="optimizable",
                    match=match,
                    feeder_fee_before_usd=feeder_fee_usd,
                    feeder_fee_after_usd=0.0,
                    savings_usd=feeder_fee_usd,
                )
            )
        else:
            keep_extended_lines += 1
            fee_before_total += feeder_fee_usd
            fee_after_total += feeder_fee_usd
            analyses.append(
                RowAnalysis(
                    row=row,
                    normalized_spec=norm_spec,
                    status="keep_extended",
                    match=None,
                    feeder_fee_before_usd=feeder_fee_usd,
                    feeder_fee_after_usd=feeder_fee_usd,
                    savings_usd=0.0,
                )
            )

    health_issues = run_health_checks(rows, analyses)
    active_lines = len(rows) - dnp_lines

    return BomReport(
        source_name=source_name,
        detected_format=detected_format,
        total_bom_lines=len(rows),
        active_bom_lines=active_lines,
        dnp_lines=dnp_lines,
        already_basic_lines=already_basic_lines,
        optimizable_lines=optimizable_lines,
        drop_in_optimizable_lines=drop_in_optimizable_lines,
        review_required_lines=review_required_lines,
        keep_extended_lines=keep_extended_lines,
        feeder_fee_per_part_usd=feeder_fee_usd,
        estimated_feeder_fee_before_usd=round(fee_before_total, 2),
        estimated_feeder_fee_after_usd=round(fee_after_total, 2),
        estimated_savings_usd=round(fee_before_total - fee_after_total, 2),
        drop_in_savings_usd=round(drop_in_savings, 2),
        grade_counts=grade_counts,
        health_issues=health_issues,
        rows=analyses,
    )


def analyze_bom_file(
    file_path: str | Path,
    feeder_fee_usd: float = DEFAULT_FEEDER_FEE_USD,
) -> BomReport:
    path = Path(file_path)
    csv_text = path.read_text(encoding="utf-8-sig")
    return analyze_bom_csv(csv_text, source_name=path.name, feeder_fee_usd=feeder_fee_usd)


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def format_report_markdown(report: BomReport) -> str:
    """Render a clean engineering Markdown optimization and BOM health report."""
    lines: List[str] = []
    lines.append(f"# JLCPCB Basic BOM Optimization Report — `{report.source_name}`")
    lines.append("")
    lines.append(
        f"Detected BOM format: **`{report.detected_format}`** | "
        f"Active lines: **{report.active_bom_lines}** (DNP: {report.dnp_lines}) | "
        f"Extended feeder fee rate: **${report.feeder_fee_per_part_usd:.2f}/part**"
    )
    lines.append("")
    lines.append("## 1. Feeder-Fee Savings Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Already JLCPCB Basic (`$0`) | **{report.already_basic_lines}** lines |")
    lines.append(f"| Optimizable to Basic (`A↑ / A± / A↓ / B`) | **{report.optimizable_lines}** lines ({report.drop_in_optimizable_lines} drop-in `A`, {report.review_required_lines} review `B`) |")
    lines.append(f"| Keep Extended | **{report.keep_extended_lines}** lines |")
    lines.append(f"| Estimated Feeder Fees **Before** | **${report.estimated_feeder_fee_before_usd:.2f}** |")
    lines.append(f"| Estimated Feeder Fees **After** (all suggested) | **${report.estimated_feeder_fee_after_usd:.2f}** |")
    lines.append(f"| **Drop-In Safe Savings (`A↑ / A± / A↓`)** | **${report.drop_in_savings_usd:.2f}** |")
    lines.append(f"| **Total Potential Savings (incl. `B` review)** | **${report.estimated_savings_usd:.2f}** |")
    lines.append("")
    lines.append(
        "**Grade Breakdown:** "
        f"`A↑` Upgrade: **{report.grade_counts.get('A↑', 0)}** · "
        f"`A±` Equivalent: **{report.grade_counts.get('A±', 0)}** · "
        f"`A↓` Margin Check: **{report.grade_counts.get('A↓', 0)}** · "
        f"`B` Schematic/Footprint Review: **{report.grade_counts.get('B', 0)}**"
    )
    lines.append("")

    lines.append("## 2. BOM Health Checks")
    lines.append("")
    if not report.health_issues:
        lines.append("- PASS: No duplicate designators, quantity mismatches, placeholders, or derating flags detected.")
    else:
        for issue in report.health_issues:
            tag = issue.severity.upper()
            lines.append(f"- **[{tag}] `{issue.code}` ({_md_escape(issue.refdes)})**: {_md_escape(issue.message)}")
    lines.append("")

    lines.append("## 3. Line-by-Line Optimization Plan")
    lines.append("")
    lines.append("| RefDes | Qty | Original Value / MPN | Status | Grade | Basic LCSC | Basic MPN (Pkg) | Savings | Engineering Notes |")
    lines.append("|---|---:|---|---|---|---|---|---:|---|")

    for r in report.rows:
        orig_parts = [p for p in (r.row.value, r.row.mpn) if p]
        orig_label = " / ".join(orig_parts) if orig_parts else "(empty)"
        if r.status == "dnp":
            lines.append(
                f"| `{_md_escape(r.row.refdes_raw)}` | {r.row.qty} | {_md_escape(orig_label)} | "
                f"DNP | — | — | — | $0.00 | Excluded from PCBA feeder fees |"
            )
            continue

        if r.match is None:
            lines.append(
                f"| `{_md_escape(r.row.refdes_raw)}` | {r.row.qty} | {_md_escape(orig_label)} | "
                f"Keep Extended | — | `{_md_escape(r.row.lcsc or '—')}` | — | $0.00 | No Basic drop-in in local table; check full catalog |"
            )
            continue

        status_label = "Already Basic" if r.status == "already_basic" else "Swap to Basic"
        note = r.match.caution if r.match.grade in ("A↓", "B") else r.match.reason
        lines.append(
            f"| `{_md_escape(r.row.refdes_raw)}` | {r.row.qty} | {_md_escape(orig_label)} | "
            f"{status_label} | **`{r.match.grade}`** | `{r.match.basic_lcsc}` | "
            f"`{_md_escape(r.match.basic_mpn)}` ({_md_escape(r.match.basic_package)}) | "
            f"${r.savings_usd:.2f} | {_md_escape(note)} |"
        )

    lines.append("")
    lines.append("---")
    lines.append(
        f"Need full parametric cross-referencing, interactive zero-upload BOM cleanup, or extended LCSC stock verification? "
        f"Open **{PARTCROSSLAB_URL}** (100% client-side, your BOM never leaves your browser)."
    )
    return "\n".join(lines) + "\n"


def format_report_csv(report: BomReport) -> str:
    """Render an enriched CSV ready for spreadsheet review or KiCad BOM re-import."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "RefDes",
            "Qty",
            "Original_Value",
            "Original_Footprint",
            "Original_MPN",
            "Original_LCSC",
            "Normalized_Spec",
            "Status",
            "Grade",
            "Basic_LCSC",
            "Basic_MPN",
            "Basic_Package",
            "Fee_Before_USD",
            "Fee_After_USD",
            "Savings_USD",
            "Engineering_Notes",
        ]
    )
    for r in report.rows:
        m = r.match
        notes = ""
        if m:
            notes = f"{m.reason} Caution: {m.caution}"
        elif r.status == "dnp":
            notes = "Do Not Populate"
        else:
            notes = "Keep Extended or check https://partcrosslab.pages.dev"

        writer.writerow(
            [
                r.row.refdes_raw,
                r.row.qty,
                r.row.value,
                r.row.footprint,
                r.row.mpn,
                r.row.lcsc,
                r.normalized_spec,
                r.status,
                m.grade if m else "",
                m.basic_lcsc if m else "",
                m.basic_mpn if m else "",
                m.basic_package if m else "",
                f"{r.feeder_fee_before_usd:.2f}",
                f"{r.feeder_fee_after_usd:.2f}",
                f"{r.savings_usd:.2f}",
                notes,
            ]
        )
    return buf.getvalue()


def format_report_json(report: BomReport) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"
