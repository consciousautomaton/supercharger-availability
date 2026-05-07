"""
Build compact station summary tables for the V2 stats panel.

Inputs:
  v2/frontend/public/data/chargers_*.json

Output:
  v2/frontend/public/data/station_summary.json

Purpose:
  This is not a coverage/population computation. It summarizes station counts by
  country, network, power tier, and open year so the UI can display meaningful
  dataset/network context before the WebGPU population layer is complete.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "v2/frontend/public/data"
COUNTRIES = ROOT / "data/ne_10m_admin_0_countries.geojson"
OUT = DATA_DIR / "station_summary.json"

SOURCES = [
    ("tesla", DATA_DIR / "chargers_tesla.json"),
    ("bnetza", DATA_DIR / "chargers_bnetza.json"),
    ("irve", DATA_DIR / "chargers_irve.json"),
    ("nobil", DATA_DIR / "chargers_nobil.json"),
    ("afdc", DATA_DIR / "chargers_afdc.json"),
]

MANUAL_COUNTRIES = {
    "usa": "USA",
    "united states": "USA",
    "united states of america": "USA",
    "canada": "CAN",
    "germany": "DEU",
    "france": "FRA",
    "united kingdom": "GBR",
    "uk": "GBR",
    "south korea": "KOR",
    "korea": "KOR",
    "china": "CHN",
    "mainland china": "CHN",
    "taiwan": "TWN",
    "norway": "NOR",
}


def load_country_lookup() -> dict[str, str]:
    lookup = dict(MANUAL_COUNTRIES)
    if not COUNTRIES.exists():
        return lookup
    with COUNTRIES.open(encoding="utf-8") as f:
        geo = json.load(f)
    for feature in geo.get("features", []):
        props = feature.get("properties") or {}
        iso = props.get("ISO_A3") or props.get("ADM0_A3")
        if not iso or iso == "-99":
            continue
        for key in ["ADMIN", "NAME", "NAME_LONG", "FORMAL_EN", "SOVEREIGNT", "GEOUNIT"]:
            value = props.get(key)
            if value:
                lookup[str(value).strip().lower()] = iso
    return lookup


def normalize_country(value: Any, lookup: dict[str, str]) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if re.fullmatch(r"[A-Z]{3}", raw):
        return raw
    return lookup.get(raw.lower())


def parse_open_year(value: Any) -> int | None:
    if value is None:
        return None
    raw = str(value).strip()
    if len(raw) < 4:
        return None
    try:
        year = int(raw[:4])
    except ValueError:
        return None
    return year if 1900 <= year <= 2100 else None


def parse_power(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def power_tier(station: dict[str, Any]) -> str:
    p = parse_power(station.get("power_kw"))
    if p is None:
        return "unknown"
    if p >= 150:
        return "ultra"
    if p >= 50:
        return "dc_fast"
    return "slow"


def blank_bucket() -> dict[str, Any]:
    return {
        "station_count": 0,
        "slow_count": 0,
        "dc_fast_count": 0,
        "ultra_count": 0,
        "unknown_power_count": 0,
        "with_open_date": 0,
        "opened_by_year": {},
        "networks": {},
    }


def increment_bucket(bucket: dict[str, Any], station: dict[str, Any], year: int | None) -> None:
    bucket["station_count"] += 1
    tier = power_tier(station)
    if tier == "slow":
        bucket["slow_count"] += 1
    elif tier == "dc_fast":
        bucket["dc_fast_count"] += 1
    elif tier == "ultra":
        bucket["ultra_count"] += 1
    else:
        bucket["unknown_power_count"] += 1
    if year is not None:
        bucket["with_open_date"] += 1
        opened = bucket["opened_by_year"]
        key = str(year)
        opened[key] = opened.get(key, 0) + 1


def network_bucket(bucket: dict[str, Any], network: str) -> dict[str, Any]:
    networks = bucket["networks"]
    if network not in networks:
        networks[network] = {
            "station_count": 0,
            "slow_count": 0,
            "dc_fast_count": 0,
            "ultra_count": 0,
            "unknown_power_count": 0,
            "with_open_date": 0,
            "opened_by_year": {},
        }
    return networks[network]


def main() -> None:
    lookup = load_country_lookup()
    global_bucket = blank_bucket()
    countries: dict[str, dict[str, Any]] = {}
    networks: dict[str, dict[str, Any]] = {}
    source_counts: dict[str, int] = {}
    unmapped_country_values: Counter[str] = Counter()
    total = 0

    for source, path in SOURCES:
        if not path.exists():
            print(f"[skip] {source}: missing {path}", file=sys.stderr)
            continue
        print(f"[load] {source}: {path}", file=sys.stderr)
        with path.open(encoding="utf-8") as f:
            stations = json.load(f)
        if not isinstance(stations, list):
            raise RuntimeError(f"{path} did not contain a station list")

        source_counts[source] = len(stations)
        for i, station in enumerate(stations, start=1):
            total += 1
            if i % 50_000 == 0:
                print(f"  {source}: summarized {i:,}/{len(stations):,} stations", file=sys.stderr)
            if not isinstance(station, dict):
                continue
            year = parse_open_year(station.get("open_date"))
            network = str(station.get("network") or "Unknown").strip() or "Unknown"
            country = normalize_country(station.get("country"), lookup)
            if country is None:
                raw_country = str(station.get("country") or "").strip() or "(missing)"
                unmapped_country_values[raw_country] += 1
                country = "UNK"

            increment_bucket(global_bucket, station, year)
            increment_bucket(network_bucket(global_bucket, network), station, year)

            country_bucket = countries.setdefault(country, blank_bucket())
            increment_bucket(country_bucket, station, year)
            increment_bucket(network_bucket(country_bucket, network), station, year)

            network_global = networks.setdefault(network, blank_bucket())
            increment_bucket(network_global, station, year)

    # Keep network maps deterministic and avoid huge long-tail order changes.
    for bucket in [global_bucket, *countries.values()]:
        bucket["networks"] = dict(
            sorted(
                bucket["networks"].items(),
                key=lambda kv: (-kv[1]["station_count"], kv[0].lower()),
            )
        )
        bucket["opened_by_year"] = dict(sorted(bucket["opened_by_year"].items()))
        for net_bucket in bucket["networks"].values():
            net_bucket["opened_by_year"] = dict(sorted(net_bucket["opened_by_year"].items()))

    networks = dict(
        sorted(networks.items(), key=lambda kv: (-kv[1]["station_count"], kv[0].lower()))
    )
    for bucket in networks.values():
        bucket["opened_by_year"] = dict(sorted(bucket["opened_by_year"].items()))
        bucket.pop("networks", None)

    out = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sources": source_counts,
            "station_count": total,
            "unmapped_country_values": dict(unmapped_country_values.most_common(50)),
            "notes": [
                "Counts summarize station records, not connectors.",
                "Country codes are normalized from source country fields where available; UNK means missing or unmapped source country.",
            ],
        },
        "global": global_bucket,
        "countries": dict(sorted(countries.items())),
        "networks": networks,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)

    print(f"[done] wrote {OUT}", file=sys.stderr)
    print(f"  stations summarized      : {total:,}", file=sys.stderr)
    print(f"  sources                  : {source_counts}", file=sys.stderr)
    print(f"  countries                : {len(countries):,}", file=sys.stderr)
    print(f"  networks                 : {len(networks):,}", file=sys.stderr)
    print(f"  unmapped country records : {sum(unmapped_country_values.values()):,}", file=sys.stderr)
    print(f"  output size              : {OUT.stat().st_size / 1024 / 1024:.1f} MiB", file=sys.stderr)


if __name__ == "__main__":
    main()

