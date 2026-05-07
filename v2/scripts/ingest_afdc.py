"""
Ingest U.S. / Canada AFDC electric charging stations.

Source:
  NREL / National Laboratory of the Rockies Alternative Fuel Stations API
  https://developer.nlr.gov/docs/transportation/alt-fuel-stations-v1/all/

License:
  Public domain / U.S. government data.

Output:
  v2/frontend/public/data/chargers_afdc.json

Notes:
- Pulls public, currently available electric stations for US and Canada.
- Drops Tesla and Tesla Destination rows because supercharge.info is the Tesla
  source of truth.
- AFDC has station records, not stall-level power per connector. We use port
  counts to infer a coarse `power_kw` and `kind`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent.parent
RAW = ROOT / "data/afdc_electric_public_us_ca.json"
OUT = ROOT / "v2/frontend/public/data/chargers_afdc.json"

BASE_URL = "https://developer.nlr.gov/api/alt-fuel-stations/v1.json"
API_KEY = "DEMO_KEY"

TESLA_NETWORKS = {"Tesla", "Tesla Destination", "US_SUPERCHARGE"}


def download_if_needed() -> None:
    if RAW.exists() and RAW.stat().st_size > 1_000_000:
        print(f"[fetch] using cached {RAW} ({RAW.stat().st_size / 1024 / 1024:.1f} MiB)", file=sys.stderr)
        return
    params = urlencode(
        {
            "api_key": API_KEY,
            "fuel_type": "ELEC",
            "country": "all",
            "status": "E",
            "access": "public",
            "limit": "all",
        }
    )
    RAW.parent.mkdir(parents=True, exist_ok=True)
    url = f"{BASE_URL}?{params}"
    print(f"[fetch] downloading AFDC stations to {RAW}", file=sys.stderr)
    subprocess.run(
        ["curl.exe", "-L", "--fail", "--show-error", "--progress-bar", "-o", str(RAW), url],
        check=True,
    )
    print(f"[fetch] downloaded {RAW.stat().st_size / 1024 / 1024:.1f} MiB", file=sys.stderr)


def parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_open_date(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    return None


def network_name(raw: Any) -> str:
    value = str(raw or "").strip()
    rules = {
        "ChargePoint Network": "ChargePoint",
        "eVgo Network": "EVgo",
        "Electrify America": "Electrify America",
        "Electrify Canada": "Electrify Canada",
        "Circuit électrique": "Circuit electrique",
        "SHELL_RECHARGE": "Shell Recharge",
        "BP_PULSE": "bp pulse",
        "FLO": "FLO",
    }
    return rules.get(value, value or "Unknown")


def infer_power_kw(rec: dict[str, Any]) -> float | None:
    dc = parse_int(rec.get("ev_dc_fast_num")) or 0
    level2 = parse_int(rec.get("ev_level2_evse_num")) or 0
    level1 = parse_int(rec.get("ev_level1_evse_num")) or 0
    if dc > 0:
        # AFDC does not expose per-station DC kW in this endpoint. Use the
        # conservative fast-charger threshold so power-tier filtering is honest.
        return 50.0
    if level2 > 0:
        return 7.0
    if level1 > 0:
        return 1.4
    return None


def normalize_record(rec: dict[str, Any]) -> dict[str, Any] | None:
    lat = parse_float(rec.get("latitude"))
    lon = parse_float(rec.get("longitude"))
    if lat is None or lon is None:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    network = network_name(rec.get("ev_network"))
    if network in TESLA_NETWORKS:
        return None

    dc = parse_int(rec.get("ev_dc_fast_num")) or 0
    level2 = parse_int(rec.get("ev_level2_evse_num")) or 0
    level1 = parse_int(rec.get("ev_level1_evse_num")) or 0
    power_kw = infer_power_kw(rec)
    country = rec.get("country")
    iso3 = "USA" if country == "US" else "CAN" if country == "CA" else country
    return {
        "id": f"afdc:{rec.get('id')}",
        "lat": lat,
        "lon": lon,
        "network": network,
        "power_kw": power_kw,
        "stall_count": dc + level2 + level1 or None,
        "open_date": parse_open_date(rec.get("open_date")),
        "close_date": None,
        "date_source": "authoritative_install" if rec.get("open_date") else "unknown",
        "country": iso3,
        "name": rec.get("station_name"),
        "kind": "fast" if dc > 0 else "slow" if (level2 > 0 or level1 > 0) else "unknown",
        "state": rec.get("state"),
        "city": rec.get("city"),
        "street_address": rec.get("street_address"),
        "ev_dc_fast_num": dc,
        "ev_level2_evse_num": level2,
        "ev_level1_evse_num": level1,
        "ev_connector_types": rec.get("ev_connector_types"),
    }


def main() -> None:
    download_if_needed()
    with RAW.open(encoding="utf-8") as f:
        payload = json.load(f)

    raw_records = payload.get("fuel_stations")
    if not isinstance(raw_records, list):
        raise RuntimeError("AFDC payload missing fuel_stations array")

    out: list[dict[str, Any]] = []
    skipped_tesla = 0
    skipped_geo = 0
    missing_open_date = 0
    for i, rec in enumerate(raw_records, start=1):
        if i % 50_000 == 0:
            print(
                f"  parsed {i:,} records | output: {len(out):,} | "
                f"skipped tesla: {skipped_tesla:,} | skipped geo: {skipped_geo:,}",
                file=sys.stderr,
            )
        if not isinstance(rec, dict):
            continue
        raw_network = network_name(rec.get("ev_network"))
        if raw_network in TESLA_NETWORKS:
            skipped_tesla += 1
            continue
        norm = normalize_record(rec)
        if norm is None:
            skipped_geo += 1
            continue
        if norm["open_date"] is None:
            missing_open_date += 1
        out.append(norm)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)

    fast = sum(1 for s in out if s["kind"] == "fast")
    slow = sum(1 for s in out if s["kind"] == "slow")
    unknown = sum(1 for s in out if s["kind"] == "unknown")
    total_results = payload.get("total_results")
    print(f"[done] wrote {len(out):,} stations to {OUT}", file=sys.stderr)
    print(f"  API total_results     : {total_results:,}" if isinstance(total_results, int) else f"  API total_results     : {total_results}", file=sys.stderr)
    print(f"  raw input records     : {len(raw_records):,}", file=sys.stderr)
    print(f"  skipped Tesla rows    : {skipped_tesla:,}", file=sys.stderr)
    print(f"  skipped missing geo   : {skipped_geo:,}", file=sys.stderr)
    print(f"  fast / slow / unknown : {fast:,} / {slow:,} / {unknown:,}", file=sys.stderr)
    print(f"  no open_date output   : {missing_open_date:,}", file=sys.stderr)
    print(f"  output size           : {OUT.stat().st_size / 1024 / 1024:.1f} MiB", file=sys.stderr)


if __name__ == "__main__":
    main()

