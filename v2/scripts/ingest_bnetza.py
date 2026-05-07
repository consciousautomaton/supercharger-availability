"""
Ingest German Bundesnetzagentur Ladesäulenregister.

Source: data/Ladesaeulenregister_BNetzA_2026-04-22.csv (already on disk)
License: Public registry (CC-like, attribute Bundesnetzagentur)

Output: v2/frontend/public/data/chargers_bnetza.json

Notes:
- Encoding cp1252, semicolon-delimited, decimal comma.
- 10 metadata header lines precede the column header.
- 47 columns; up to 6 connectors per station.
- Status filter: "In Betrieb" (operational) only.
- Date format DD.MM.YYYY → ISO YYYY-MM-DD.
- Network = Betreiber column. Light normalization for top operators.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "data/Ladesaeulenregister_BNetzA_2026-04-22.csv"
OUT = ROOT / "v2/frontend/public/data/chargers_bnetza.json"

OPERATIONAL_STATUS = {"In Betrieb"}
HEADER_SKIP_LINES = 10


def normalize_network(betreiber: str) -> str:
    """
    Map raw Betreiber strings to canonical network names.
    Errs on the side of leaving unknown operators as-is, so the long tail is
    preserved verbatim and we don't fabricate brand identity.
    """
    s = betreiber.strip()
    low = s.lower()
    rules = [
        ("tesla", "Tesla"),
        ("ionity", "Ionity"),
        ("enbw", "EnBW"),
        ("aral pulse", "Aral Pulse"),
        ("aral ", "Aral Pulse"),
        ("eon ", "E.ON"),
        ("e.on ", "E.ON"),
        ("shell recharge", "Shell Recharge"),
        ("allego", "Allego"),
        ("fastned", "Fastned"),
        ("ewe go", "EWE Go"),
        ("vattenfall", "Vattenfall"),
        ("compleo", "Compleo"),
        ("mer ", "Mer"),
        ("stadtwerke", "Stadtwerke (regional utility)"),
    ]
    for needle, canon in rules:
        if needle in low:
            return canon
    return s if s else "Unknown"


def parse_de_date(s: str) -> str | None:
    s = s.strip()
    if not s:
        return None
    parts = s.split(".")
    if len(parts) != 3:
        return None
    d, m, y = parts
    if len(y) != 4 or not y.isdigit() or not m.isdigit() or not d.isdigit():
        return None
    return f"{y}-{int(m):02d}-{int(d):02d}"


def parse_de_decimal(s: str) -> float | None:
    s = s.strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_int(s: str) -> int | None:
    s = s.strip().replace(",", ".")
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def main() -> None:
    out: list[dict] = []
    skipped_status = 0
    skipped_no_geo = 0
    rows = 0

    with SRC.open(encoding="cp1252", newline="") as f:
        for _ in range(HEADER_SKIP_LINES):
            next(f)
        rdr = csv.reader(f, delimiter=";")
        next(rdr)  # column header

        for row in rdr:
            rows += 1
            if len(row) < 17:
                continue
            status = row[3].strip()
            if status not in OPERATIONAL_STATUS:
                skipped_status += 1
                continue
            lat = parse_de_decimal(row[15])
            lon = parse_de_decimal(row[16])
            if lat is None or lon is None:
                skipped_no_geo += 1
                continue

            connectors = []
            for slot in range(6):
                base = 23 + slot * 4
                ctype = row[base].strip() if base < len(row) else ""
                cpower = (
                    parse_de_decimal(row[base + 1])
                    if base + 1 < len(row)
                    else None
                )
                if ctype:
                    connectors.append({"type": ctype, "power_kw": cpower})

            network = normalize_network(row[1])
            # Tesla deduplication: supercharge.info is the authoritative Tesla
            # source globally; drop Tesla rows from BNetzA so we don't render
            # duplicates in Germany.
            if network == "Tesla":
                continue
            out.append(
                {
                    "id": f"bnetza:{row[0].strip()}",
                    "lat": lat,
                    "lon": lon,
                    "network": network,
                    "power_kw": parse_de_decimal(row[6]),
                    "stall_count": parse_int(row[5]),
                    "open_date": parse_de_date(row[7]),
                    "close_date": None,
                    "date_source": "authoritative_install",
                    "country": "DEU",
                    "name": row[2].strip() or None,
                    "kind": (
                        "fast"
                        if "Schnell" in row[4]
                        else "slow"
                        if "Normal" in row[4]
                        else "unknown"
                    ),
                    "connectors": connectors,
                }
            )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)

    size_mb = OUT.stat().st_size / (1024 * 1024)
    print(f"[done] wrote {len(out):,} stations to {OUT}", file=sys.stderr)
    print(f"  raw data rows           : {rows:,}", file=sys.stderr)
    print(f"  skipped (status)        : {skipped_status:,}", file=sys.stderr)
    print(f"  skipped (missing geo)   : {skipped_no_geo:,}", file=sys.stderr)
    print(f"  output size             : {size_mb:.1f} MiB", file=sys.stderr)


if __name__ == "__main__":
    main()
