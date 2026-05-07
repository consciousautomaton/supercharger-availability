"""
Ingest EV stock by country/year.

Primary source used:
  Our World in Data grapher CSV derived from IEA Global EV Outlook 2025:
  https://ourworldindata.org/grapher/electric-car-stocks.csv

Original source:
  IEA Global EV Outlook 2025, CC BY 4.0.

Why OWID instead of direct IEA XLSX:
  The IEA product page lists the Global EV Outlook 2025 country data as a free
  XLSX/.Stat dataset, but direct download is login-gated in the browser. OWID
  publishes a stable, open CSV derived from the same IEA 2025 source.

Output:
  v2/frontend/public/data/ev_stock_country_year.json

Output shape:
  {
    "DEU": {
      "2024": { "bev": null, "phev": null, "total": 2390000 }
    }
  }

Notes:
- This OWID chart exposes total electric car stock only, not BEV/PHEV split.
  `bev` and `phev` are therefore null rather than inferred.
- Regional/non-country OWID aggregates are skipped. Only ISO-A3 country codes
  are retained.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
RAW_CSV = ROOT / "data/owid_iea_electric_car_stocks.csv"
RAW_META = ROOT / "data/owid_iea_electric_car_stocks.metadata.json"
OUT = ROOT / "v2/frontend/public/data/ev_stock_country_year.json"
OUT_META = ROOT / "v2/frontend/public/data/ev_stock_country_year_meta.json"

CSV_URL = "https://ourworldindata.org/grapher/electric-car-stocks.csv?v=1&csvType=full&useColumnShortNames=false"
META_URL = "https://ourworldindata.org/grapher/electric-car-stocks.metadata.json?v=1&csvType=full&useColumnShortNames=false"


def download(url: str, path: Path) -> None:
    if path.exists() and path.stat().st_size > 1_000:
        print(f"[fetch] using cached {path} ({path.stat().st_size:,} bytes)", file=sys.stderr)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[fetch] downloading {url} to {path}", file=sys.stderr)
    subprocess.run(
        ["curl.exe", "-L", "--fail", "--show-error", "--progress-bar", "-o", str(path), url],
        check=True,
    )
    print(f"[fetch] downloaded {path.stat().st_size:,} bytes", file=sys.stderr)


def parse_stock(value: str) -> int | None:
    raw = value.strip()
    if not raw:
        return None
    try:
        return int(round(float(raw)))
    except ValueError:
        return None


def is_iso_a3(code: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{3}", code))


def main() -> None:
    download(CSV_URL, RAW_CSV)
    download(META_URL, RAW_META)

    out: dict[str, dict[str, dict[str, int | None]]] = {}
    rows = 0
    kept = 0
    skipped_non_country = 0
    skipped_missing_stock = 0
    min_year: int | None = None
    max_year: int | None = None

    with RAW_CSV.open(encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f)
        required = {"Entity", "Code", "Year", "Electric car stocks"}
        missing = required.difference(rdr.fieldnames or [])
        if missing:
            raise RuntimeError(f"OWID CSV missing columns: {sorted(missing)}")

        for row in rdr:
            rows += 1
            code = (row.get("Code") or "").strip()
            if not is_iso_a3(code):
                skipped_non_country += 1
                continue
            stock = parse_stock(row.get("Electric car stocks") or "")
            if stock is None:
                skipped_missing_stock += 1
                continue
            year = int(row["Year"])
            min_year = year if min_year is None else min(min_year, year)
            max_year = year if max_year is None else max(max_year, year)
            out.setdefault(code, {})[str(year)] = {
                "bev": None,
                "phev": None,
                "total": stock,
            }
            kept += 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), sort_keys=True)

    meta: dict[str, Any] = {
        "source": "Our World in Data electric-car-stocks, derived from IEA Global EV Outlook 2025",
        "source_url": "https://ourworldindata.org/grapher/electric-car-stocks",
        "raw_csv_url": CSV_URL,
        "original_source": "IEA Global EV Outlook 2025",
        "license": "CC BY 4.0; cite IEA and Our World in Data",
        "notes": [
            "Total electric car stock only; BEV/PHEV split is not available in this OWID chart.",
            "Only ISO-A3 country rows are retained; regional aggregates are skipped.",
        ],
        "countries": len(out),
        "rows": kept,
        "year_min": min_year,
        "year_max": max_year,
    }
    with OUT_META.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, sort_keys=True)

    latest_year = str(max_year) if max_year is not None else None
    latest_total = 0
    latest_countries = 0
    if latest_year is not None:
        for by_year in out.values():
            value = by_year.get(latest_year)
            if value and value["total"] is not None:
                latest_total += int(value["total"])
                latest_countries += 1

    print(f"[done] wrote {OUT}", file=sys.stderr)
    print(f"  raw rows              : {rows:,}", file=sys.stderr)
    print(f"  kept country rows     : {kept:,}", file=sys.stderr)
    print(f"  countries             : {len(out):,}", file=sys.stderr)
    print(f"  year range            : {min_year}..{max_year}", file=sys.stderr)
    print(f"  skipped non-country   : {skipped_non_country:,}", file=sys.stderr)
    print(f"  skipped missing stock : {skipped_missing_stock:,}", file=sys.stderr)
    if latest_year is not None:
        print(
            f"  latest-year total     : {latest_total:,} cars across {latest_countries:,} countries ({latest_year})",
            file=sys.stderr,
        )
    print(f"  output size           : {OUT.stat().st_size / 1024:.1f} KiB", file=sys.stderr)


if __name__ == "__main__":
    main()

