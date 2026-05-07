"""
Validate V2 frontend data files and print dataset statistics.

This is a lightweight sanity checker for generated JSON outputs. It does not
validate coverage math; it verifies that station and summary files are present,
parseable, and internally plausible before UI/WebGPU work consumes them.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "v2/frontend/public/data"

STATION_SOURCES = [
    ("tesla", DATA_DIR / "chargers_tesla.json", True),
    ("bnetza", DATA_DIR / "chargers_bnetza.json", True),
    ("irve", DATA_DIR / "chargers_irve.json", False),
    ("nobil", DATA_DIR / "chargers_nobil.json", False),
    ("afdc", DATA_DIR / "chargers_afdc.json", False),
]


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def validate_station(station: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ["id", "lat", "lon", "network", "date_source"]:
        if key not in station:
            errors.append(f"missing {key}")
    lat = station.get("lat")
    lon = station.get("lon")
    if not is_number(lat) or not -90 <= float(lat) <= 90:
        errors.append("invalid lat")
    if not is_number(lon) or not -180 <= float(lon) <= 180:
        errors.append("invalid lon")
    if station.get("date_source") not in {"authoritative_install", "registry_listing", "unknown"}:
        errors.append("invalid date_source")
    if station.get("kind") not in {None, "fast", "slow", "unknown"}:
        errors.append("invalid kind")
    return errors


def validate_stations() -> tuple[int, int]:
    total = 0
    failures = 0
    print("Station files:", file=sys.stderr)
    for name, path, required in STATION_SOURCES:
        if not path.exists():
            status = "missing required" if required else "missing optional"
            print(f"  {name:<8} {status}: {path}", file=sys.stderr)
            failures += 1 if required else 0
            continue
        data = load_json(path)
        if not isinstance(data, list):
            print(f"  {name:<8} ERROR: not a JSON list", file=sys.stderr)
            failures += 1
            continue

        country_counts: Counter[str] = Counter()
        network_counts: Counter[str] = Counter()
        kind_counts: Counter[str] = Counter()
        missing_open_date = 0
        row_errors = 0
        ids: set[str] = set()
        duplicate_ids = 0
        for station in data:
            if not isinstance(station, dict):
                row_errors += 1
                continue
            errors = validate_station(station)
            if errors:
                row_errors += 1
            station_id = str(station.get("id", ""))
            if station_id in ids:
                duplicate_ids += 1
            ids.add(station_id)
            country_counts[str(station.get("country") or "(missing)")] += 1
            network_counts[str(station.get("network") or "Unknown")] += 1
            kind_counts[str(station.get("kind") or "unknown")] += 1
            if not station.get("open_date"):
                missing_open_date += 1

        total += len(data)
        if row_errors or duplicate_ids:
            failures += 1
        top_networks = ", ".join(f"{k}={v:,}" for k, v in network_counts.most_common(5))
        print(
            f"  {name:<8} rows={len(data):,} countries={len(country_counts):,} "
            f"networks={len(network_counts):,} missing_open_date={missing_open_date:,} "
            f"errors={row_errors:,} duplicate_ids={duplicate_ids:,}",
            file=sys.stderr,
        )
        print(f"           kind={dict(kind_counts)}", file=sys.stderr)
        print(f"           top_networks: {top_networks}", file=sys.stderr)
    return total, failures


def validate_ev_stock() -> int:
    path = DATA_DIR / "ev_stock_country_year.json"
    if not path.exists():
        print("EV stock: missing optional ev_stock_country_year.json", file=sys.stderr)
        return 0
    data = load_json(path)
    if not isinstance(data, dict):
        print("EV stock: ERROR not a JSON object", file=sys.stderr)
        return 1
    rows = 0
    countries = 0
    years: set[int] = set()
    latest_year = None
    latest_total = 0
    failures = 0
    for iso, by_year in data.items():
        if not isinstance(by_year, dict):
            failures += 1
            continue
        countries += 1
        for year, values in by_year.items():
            rows += 1
            try:
                y = int(year)
            except ValueError:
                failures += 1
                continue
            years.add(y)
            if not isinstance(values, dict) or "total" not in values:
                failures += 1
                continue
    latest_year = max(years) if years else None
    if latest_year is not None:
        for by_year in data.values():
            values = by_year.get(str(latest_year), {})
            total = values.get("total") if isinstance(values, dict) else None
            if isinstance(total, int):
                latest_total += total
    print(
        f"EV stock: countries={countries:,} rows={rows:,} "
        f"years={min(years) if years else None}..{max(years) if years else None} "
        f"latest_total={latest_total:,} ({latest_year}) errors={failures:,}",
        file=sys.stderr,
    )
    return failures


def validate_station_summary(expected_station_total: int) -> int:
    path = DATA_DIR / "station_summary.json"
    if not path.exists():
        print("Station summary: missing optional station_summary.json", file=sys.stderr)
        return 0
    data = load_json(path)
    failures = 0
    meta_count = (((data or {}).get("meta") or {}).get("station_count")) if isinstance(data, dict) else None
    global_count = (((data or {}).get("global") or {}).get("station_count")) if isinstance(data, dict) else None
    countries = ((data or {}).get("countries") or {}) if isinstance(data, dict) else {}
    networks = ((data or {}).get("networks") or {}) if isinstance(data, dict) else {}
    if meta_count != expected_station_total:
        failures += 1
    if global_count != expected_station_total:
        failures += 1
    print(
        f"Station summary: meta_count={meta_count:,} global_count={global_count:,} "
        f"countries={len(countries):,} networks={len(networks):,} errors={failures:,}",
        file=sys.stderr,
    )
    return failures


def validate_population_grid() -> int:
    meta_path = DATA_DIR / "pop_025deg_world_2030_meta.json"
    bin_path = DATA_DIR / "pop_025deg_world_2030.bin"
    if not meta_path.exists() or not bin_path.exists():
        print("Population grid: missing optional pop_025deg_world_2030 outputs", file=sys.stderr)
        return 0
    meta = load_json(meta_path)
    failures = 0
    width = int(meta.get("width", 0))
    height = int(meta.get("height", 0))
    expected_bytes = width * height * 4
    actual_bytes = bin_path.stat().st_size
    if expected_bytes != actual_bytes:
        failures += 1
    arr = np.fromfile(bin_path, dtype=np.float32)
    nonzero = int(np.count_nonzero(arr))
    pop_sum = float(arr.sum(dtype=np.float64))
    meta_sum = float(meta.get("population_sum", 0.0))
    rel_err = abs(pop_sum - meta_sum) / meta_sum if meta_sum else 0.0
    if rel_err > 1e-6:
        failures += 1
    print(
        f"Population grid: cells={width * height:,} nonzero={nonzero:,} "
        f"sum={pop_sum:,.0f} rel_err={rel_err:.2e} bytes={actual_bytes:,} errors={failures:,}",
        file=sys.stderr,
    )
    return failures


def main() -> int:
    station_total, failures = validate_stations()
    failures += validate_ev_stock()
    failures += validate_station_summary(station_total)
    failures += validate_population_grid()
    print(f"[done] station_total={station_total:,} failures={failures:,}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
