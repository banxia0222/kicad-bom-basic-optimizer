# kicad-bom-basic-optimizer

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB.svg)](pyproject.toml)
[![Zero Dependencies](https://img.shields.io/badge/Dependencies-stdlib%20only-10b981.svg)](bom_basic_optimizer/core.py)
[![MCP Ready](https://img.shields.io/badge/MCP-stdio%20JSON--RPC%202.0-f59e0b.svg)](bom_basic_optimizer/server.py)

**Stop paying `$3 × N` in avoidable JLCPCB Extended-part feeder fees on every prototype PCBA spin.**

`kicad-bom-basic-optimizer` is a zero-dependency Python 3 CLI, stdio MCP server, and AI Coding Agent Skill that parses KiCad, Altium, and EasyEDA BOM CSVs, runs pre-assembly BOM health checks, normalizes Yageo/Murata/Samsung/TDK/Vishay/UniOhm SMD passives and common semiconductors to **JLCPCB Basic equivalents**, and grades every swap transparently (`A↑ / A± / A↓ / B`) so you never accidentally downgrade a precision divider or high-voltage bypass cap.

> **Prefer a zero-upload browser UI or need the full cross-reference catalog?**  
> Try **[PartCrossLab](https://partcrosslab.pages.dev)** — 100% client-side interactive BOM optimizer & parametric part cross-reference (your BOM never leaves your browser tab).

---

## Why This Exists: The `$3/Part` JLCPCB Feeder-Fee Tax

On JLCPCB PCBA assembly, every unique **Extended** reel costs **$3.00** in feeder-loading setup fees — even if you only place a single 10kΩ `0603` resistor on 5 prototype boards.

Most KiCad schematics pick generic DigiKey/Mouser MPNs (`RC0603FR-075K1L`, `GRM188R71C104KA01D`, `LM1117IMPX-3.3`, `SI2302CDS`) that are electrically identical to `$0` JLCPCB Basic parts. On a typical 20-line ESP32/STM32 dev board, that single oversight adds **$30–$51** in pure feeder fees per order.

### Real Before / After on [`samples/sample_kicad_bom.csv`](samples/sample_kicad_bom.csv)

Running the CLI on our 22-line USB-C + ESP32-S3 CAN logger demo BOM:

```bash
python3 -m bom_basic_optimizer analyze samples/sample_kicad_bom.csv --format md
```

| Metric | Before | After (Auto-Safe `A↑/A±` Only) | After (All Suggested) |
|---|---:|---:|---:|
| Active BOM Lines (excl. 1 DNP) | 21 | 21 | 21 |
| JLCPCB Basic Lines (`$0` fee) | 1 | 15 | 18 |
| Extended Lines (`$3` fee each) | 20 | 6 | 3 |
| **Estimated Feeder Fees** | **$60.00** | **$18.00** | **$9.00** |
| **Feeder Fees Saved** | — | **$42.00** (zero risk) | **$51.00** (review `A↓`/`B`) |

Notice why transparent grading matters:
- **`R7` (`4.7k 0.1%` 0402 ADC divider)** is flagged **`A↓`** because the Basic resistor is `1%` — the report explicitly warns you to **keep it Extended** for precision sensing.
- **`C9` (`100nF 50V X7R` 0402 bypass)** is flagged **`A↓`** because the 0402 Basic part (`C1525`) is only rated `16V`.
- **`D4` (`SMBJ5.0A` 600W SMB TVS)** is flagged **`B`** because the Basic option (`SMAJ5.0A`) is a smaller `SMA` 400W footprint.
- **`C7, C8` (`10uF 10V X5R` 0603)** triggers a **`NARROW_VOLTAGE_MARGIN`** health warning reminding you to check MLCC DC-bias derating.

---

## Grading Legend

| Grade | Meaning | Action |
|---|---|---|
| **`A↑`** | Same footprint, **higher margin** (e.g., `5% → 1%` resistor, `16V → 50V` MLCC, `CH340G → CH340C` crystal-less) | Safe drop-in swap |
| **`A±`** | Same footprint and equivalent nominal electrical spec | Safe drop-in swap |
| **`A↓`** | Same footprint, **tighter/lower margin** (e.g., `0.1% → 1%` tolerance, `50V → 16V` cap, `X7R → X5R`) | Verify rail voltage / tolerance before swapping |
| **`B`** | Footprint, pinout, polarity, or surge-power difference (e.g., `SMBJ → SMAJ`, `BAT54 → BAT54C`, `CP2102 → CH340C`) | Requires schematic or layout review |

---

## Quick Start (Zero Dependencies)

Requires **Python 3.9+**. No `pip` packages needed to run directly from source.

```bash
git clone https://github.com/banxia0222/kicad-bom-basic-optimizer.git
cd kicad-bom-basic-optimizer

# 1. Analyze a KiCad, Altium, or EasyEDA BOM CSV -> Markdown report
python3 -m bom_basic_optimizer analyze samples/sample_kicad_bom.csv --format md

# 2. Export an enriched CSV for spreadsheet review / re-import
python3 -m bom_basic_optimizer analyze samples/sample_kicad_bom.csv --format csv -o optimized_bom.csv

# 3. Run in CI / pre-fab check (exits with code 2 on duplicate refdes or TBD placeholders)
python3 -m bom_basic_optimizer analyze samples/sample_kicad_bom.csv --strict

# 4. Look up a single MPN or passive spec
python3 -m bom_basic_optimizer lookup "GRM188R71C104KA01D"
python3 -m bom_basic_optimizer lookup "LM1117IMPX-3.3" --package SOT-223
```

Optional editable install to register the `bom-basic-optimizer` and `bom-basic-optimizer-mcp` entry points:

```bash
pip install -e .
bom-basic-optimizer analyze samples/sample_kicad_bom.csv --format md
```

---

## What It Checks & Normalizes

1. **Multi-EDA CSV / TSV Auto-Detection**: KiCad (`Reference`, `Footprint`), Altium (`Designator`, `Comment`), EasyEDA (`LCSC Part #`), refdes ranges (`R1-R4`), and DNP columns.
2. **Pre-Assembly BOM Health Checks**:
   - `DUPLICATE_REFDES` (error): RefDes repeated across rows or within a row.
   - `PLACEHOLDER_VALUE` (error): `TBD`, `TBC`, `XXX`, `???`, or blank Value + MPN.
   - `QTY_REFDES_MISMATCH` (warning): `Qty` column disagrees with the expanded designator count.
   - `MISSING_PACKAGE` (warning): Active part lacks a recognizable SMD/IC footprint.
   - `NARROW_VOLTAGE_MARGIN` & `NARROW_TEMP_DIELECTRIC` (warning): Low-voltage bulk MLCC DC-bias derating (`<= 10V` for `>= 4.7uF`, or `<= 6.3V`), unstable `Y5V`/`Z5U` dielectrics, or `0..70°C` commercial-only specs.
   - `SINGLE_SOURCE_PART` (info): Active Extended ICs/modules/connectors with no bundled Basic alternate.
3. **SMD Passive Normalization (`0402` / `0603` / `0805` / `1206`)**:
   - Decodes Yageo (`RC`, `AC`, `RT`, `CC`), Samsung (`CL05/10/21/31`), Murata (`GRM15/18/21/31`), TDK (`C1005/1608/2012/3216`), UniOhm (`0402W/0603W/0805W/1206W`), and Vishay (`CRCW`) MPNs.
4. **Curated Popular Components**:
   - `AMS1117` / `LM1117`, `XC6206`, `ME6211` / `AP2112K`, `AO3400A` / `SI2302`, `AO3401A`, `2N7002` / `BSS138`, `MMBT3904`, `1N4148WS`, `BAT54` / `BAT54C` / `BAT54S`, `SS34`, `SS14`, `1N5819WS`, `SMBJ5.0A` / `SMAJ5.0A`, `USBLC6-2SC6`, `TJA1051T/3`, `CH340C` / `CH340G`, `NE555` / `LM555`, `LM358`, `74HC595`, `EL817` / `PC817`, `MP1584EN`, and standard `0603` indicator LEDs.

---

## MCP Server Setup (Claude Code / Cursor / Codex)

`bom_basic_optimizer.server` is a zero-dependency **stdio JSON-RPC 2.0 MCP server** exposing two tools:
- `analyze_kicad_bom` — accepts `file_path` or inline `csv_content`, returns Markdown, CSV, or JSON report.
- `find_jlcpcb_basic_alternative` — looks up any MPN or passive spec and returns a graded Basic equivalent.

### Claude Code (`~/.claude.json` or project `.mcp.json`)

```json
{
  "mcpServers": {
    "kicad-bom-basic-optimizer": {
      "command": "python3",
      "args": ["-m", "bom_basic_optimizer.server"],
      "cwd": "/absolute/path/to/kicad-bom-basic-optimizer"
    }
  }
}
```

### Cursor (`.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "kicad-bom-basic-optimizer": {
      "command": "python3",
      "args": ["-m", "bom_basic_optimizer.server"],
      "cwd": "/absolute/path/to/kicad-bom-basic-optimizer"
    }
  }
}
```

### Codex (`~/.codex/config.toml`)

```toml
[mcp_servers.kicad_bom_basic_optimizer]
command = "python3"
args = ["-m", "bom_basic_optimizer.server"]
cwd = "/absolute/path/to/kicad-bom-basic-optimizer"
```

### AI Coding Agent Skill (`SKILL.md`)

Drop [`SKILL.md`](SKILL.md) into your repository's `.agents/skills/kicad-bom-basic-optimizer/` or `.claude/skills/kicad-bom-basic-optimizer/` folder so coding agents automatically run BOM hygiene checks and protect `A↓`/`B` precision/power components before suggesting swaps.

---

## Running Tests

```bash
python3 -m unittest discover -s tests -v
```

---

## Full Web App & Cross-Reference Catalog

This open-source MIT CLI ships with a curated offline snapshot of the most common JLCPCB Basic passives and semiconductors. When you need:
- An **instant zero-upload browser tool** (drag-and-drop KiCad/Altium/EasyEDA BOMs with strict local execution),
- Broader parametric **cross-reference tables** (voltage regulators, MOSFETs, ESD/TVS diodes, CAN transceivers, op-amps, and SMD passives),
- Side-by-side spec comparison and printable review sheets,

visit **[https://partcrosslab.pages.dev](https://partcrosslab.pages.dev)**.

> **Disclaimer:** JLCPCB Basic vs. Extended catalog status and LCSC stock levels can change over time. Always confirm live tier & stock in the JLCPCB PCBA viewer before placing an assembly order.

## License

MIT — see [LICENSE](LICENSE).
