"""
Ingest Norway Nobil charging data.

Source:
  https://nobil.no/api/server/datadump.php

License / attribution:
  NOBIL by Enova is licensed under Creative Commons Attribution 4.0.
  Attribution required: "NOBIL by Enova / Norsk elbilforening".

Output:
  v2/frontend/public/data/chargers_nobil.json

Authentication:
  The Nobil datadump API requires an API key. Provide it with:
    $env:NOBIL_API_KEY = "..."
  or place it in:
    data/nobil_api_key.txt

Alternative:
  If a raw dump already exists at data/nobil_datadump.json, the script parses it
  without making a network request.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent.parent
RAW = ROOT / "data/nobil_datadump.json"
KEY_PATH = ROOT / "data/nobil_api_key.txt"
OUT = ROOT / "v2/frontend/public/data/chargers_nobil.json"

BASE_URL = "https://nobil.no/api/server/datadump.php"


def get_api_key() -> str | None:
    env_key = os.environ.get("NOBIL_API_KEY", "").strip()
    if env_key:
        return env_key
    if KEY_PATH.exists():
        key = KEY_PATH.read_text(encoding="utf-8").strip()
        return key or None
    return None


def download_if_needed() -> bool:
    if RAW.exists() and RAW.stat().st_size > 1_000:
        print(f"[fetch] using cached {RAW} ({RAW.stat().st_size / 1024 / 1024:.1f} MiB)", file=sys.stderr)
        return True

    api_key = get_api_key()
    if not api_key:
        print("[blocked] Nobil requires an API key.", file=sys.stderr)
        print("  Set NOBIL_API_KEY or create data/nobil_api_key.txt, then rerun.", file=sys.stderr)
        print("  No output was written.", file=sys.stderr)
        return False

    params = urlencode(
        {
            "apikey": api_key,
            "countrycode": "NOR",
            "format": "json",
            "file": "false",
        }
    )
    url = f"{BASE_URL}?{params}"
    RAW.parent.mkdir(parents=True, exist_ok=True)
    print(f"[fetch] downloading Nobil JSON to {RAW}", file=sys.stderr)
    subprocess.run(
        ["curl.exe", "-L", "--fail", "--show-error", "--progress-bar", "-o", str(RAW), url],
        check=True,
    )
    print(f"[fetch] downloaded {RAW.stat().st_size / 1024 / 1024:.1f} MiB", file=sys.stderr)
    return True


def first_present(rec: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = rec.get(key)
        if value not in (None, ""):
            return value
    return None


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip().replace(",", ".")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_position(value: Any) -> tuple[float | None, float | None]:
    if isinstance(value, dict):
        lat = parse_float(first_present(value, "lat", "latitude", "Latitude"))
        lon = parse_float(first_present(value, "lon", "lng", "longitude", "Longitude"))
        return lat, lon
    if isinstance(value, str):
        nums = re.findall(r"-?\d+(?:[\.,]\d+)?", value)
        if len(nums) >= 2:
            return parse_float(nums[0]), parse_float(nums[1])
    return None, None


def parse_date(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T.*", raw):
        return raw[:10]
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", raw):
        d, m, y = raw.split(".")
        return f"{y}-{m}-{d}"
    return None


def normalize_network(value: Any) -> str:
    raw = str(value or "").strip()
    low = raw.lower()
    rules = [
        ("tesla", "Tesla"),
        ("circle k", "Circle K"),
        ("ionity", "Ionity"),
        ("mer", "Mer"),
        ("recharge", "Recharge"),
        ("eviny", "Eviny"),
        ("kople", "Kople"),
        ("fortum", "Fortum"),
        ("eon", "E.ON"),
        ("uno-x", "Uno-X"),
    ]
    for needle, canon in rules:
        if needle in low:
            return canon
    return raw or "Unknown"


def connector_power_kw(connector: dict[str, Any]) -> float | None:
    for key in ["Power", "power", "PowerKW", "power_kw", "ChargingPower"]:
        value = parse_float(connector.get(key))
        if value is not None:
            return value
    return None


def normalize_record(rec: dict[str, Any]) -> dict[str, Any] | None:
    station_id = first_present(rec, "csmd", "id", "ID", "NobilId", "nobilId")
    position = first_present(rec, "Position", "position", "Coordinates", "coordinates")
    lat = parse_float(first_present(rec, "Latitude", "latitude", "lat"))
    lon = parse_float(first_present(rec, "Longitude", "longitude", "lon", "lng"))
    if lat is None or lon is None:
        lat, lon = parse_position(position)
    if lat is None or lon is None:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    connectors = first_present(rec, "Connectors", "connectors", "Connector") or []
    if isinstance(connectors, dict):
        connectors = list(connectors.values())
    if not isinstance(connectors, list):
        connectors = []

    powers = [p for c in connectors if isinstance(c, dict) for p in [connector_power_kw(c)] if p is not None]
    power_kw = max(powers) if powers else parse_float(first_present(rec, "Power", "power", "PowerKW"))
    stall_count = len(connectors) or None
    operator = first_present(rec, "Operator", "operator", "OwnedBy", "ownedBy")
    open_date = parse_date(first_present(rec, "Created", "created", "DateCreated", "dateCreated"))

    return {
        "id": f"nobil:{station_id or f'{lat:.6f},{lon:.6f}'}",
        "lat": lat,
        "lon": lon,
        "network": normalize_network(operator),
        "power_kw": power_kw,
        "stall_count": stall_count,
        "open_date": open_date,
        "close_date": None,
        "date_source": "authoritative_install" if open_date else "unknown",
        "country": "NOR",
        "name": first_present(rec, "Name", "name", "StationName", "stationName"),
        "kind": "fast" if power_kw is not None and power_kw >= 50 else "slow" if power_kw is not None else "unknown",
        "operator_raw": str(operator).strip() if operator else None,
    }


def unwrap_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ["chargerstations", "ChargerStations", "stations", "Stations", "data"]:
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def main() -> int:
    if not download_if_needed():
        return 2

    with RAW.open(encoding="utf-8-sig") as f:
        payload = json.load(f)

    raw_records = unwrap_payload(payload)
    if not raw_records:
        raise RuntimeError("Could not find a station list in the Nobil JSON payload")

    out: list[dict[str, Any]] = []
    skipped_geo = 0
    missing_open_date = 0
    for i, rec in enumerate(raw_records, start=1):
        if i % 10_000 == 0:
            print(f"  parsed {i:,} records | output: {len(out):,} | skipped geo: {skipped_geo:,}", file=sys.stderr)
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
    print(f"[done] wrote {len(out):,} stations to {OUT}", file=sys.stderr)
    print(f"  raw input records     : {len(raw_records):,}", file=sys.stderr)
    print(f"  skipped missing geo   : {skipped_geo:,}", file=sys.stderr)
    print(f"  fast / slow / unknown : {fast:,} / {slow:,} / {unknown:,}", file=sys.stderr)
    print(f"  no open_date output   : {missing_open_date:,}", file=sys.stderr)
    print(f"  output size           : {OUT.stat().st_size / 1024 / 1024:.1f} MiB", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

