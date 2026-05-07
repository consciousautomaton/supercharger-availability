"""
Ingest Tesla Supercharger data from supercharge.info.

Source: https://supercharge.info/service/supercharge/allSites
License: community-curated; canonical Tesla historical source

Output: v2/frontend/public/data/chargers_tesla.json

Schema (unified ChargerStation, see v2/frontend/src/data/types.ts):
  id           string  - "sc:<supercharge_info_id>"
  lat, lon     number  - WGS84
  network      string  - always "Tesla" for this source
  power_kw     int|null
  stall_count  int|null
  open_date    str|null - ISO YYYY-MM-DD (real install date)
  close_date   str|null - always null (source has status but not close date)
  date_source  string  - always "authoritative_install"

Plus Tesla-specific extras carried through for UI (status, open_to_others,
stall_versions, plug_types, country, name).

Filter: include sites that exist or have existed in the real world:
  OPEN, CLOSED_PERM, CLOSED_TEMP, EXPANDING.
Drop speculative future sites (PLAN, CONSTRUCTION, PERMIT, VOTING).
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
RAW_PATH = ROOT / "v2/scripts/.supercharge_sample.json"
OUT_PATH = ROOT / "v2/frontend/public/data/chargers_tesla.json"
URL = "https://supercharge.info/service/supercharge/allSites"

REAL_STATUSES = {"OPEN", "CLOSED_PERM", "CLOSED_TEMP", "EXPANDING"}


def fetch_raw() -> list[dict]:
    if RAW_PATH.exists():
        print(f"[fetch] using cached {RAW_PATH}", file=sys.stderr)
        with RAW_PATH.open() as f:
            return json.load(f)
    print(f"[fetch] downloading {URL}", file=sys.stderr)
    req = urllib.request.Request(URL, headers={"User-Agent": "ev-rollout/0.1"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_bytes(raw)
    return json.loads(raw)


def normalize(rec: dict) -> dict | None:
    status = rec.get("status")
    if status not in REAL_STATUSES:
        return None
    gps = rec.get("gps") or {}
    lat, lon = gps.get("latitude"), gps.get("longitude")
    if lat is None or lon is None:
        return None
    addr = rec.get("address") or {}
    return {
        "id": f"sc:{rec['id']}",
        "lat": lat,
        "lon": lon,
        "network": "Tesla",
        "power_kw": rec.get("powerKilowatt") or None,
        "stall_count": rec.get("stallCount") or None,
        "open_date": rec.get("dateOpened"),
        "close_date": None,
        "date_source": "authoritative_install",
        "status": status,
        "open_to_others": bool(rec.get("otherEVs", False)),
        "stall_versions": rec.get("stalls") or {},
        "plug_types": rec.get("plugs") or {},
        "country": addr.get("country"),
        "name": rec.get("name"),
    }


def main() -> None:
    raw = fetch_raw()
    out = []
    skipped_status = 0
    skipped_missing_gps = 0
    missing_open_date = 0
    for rec in raw:
        norm = normalize(rec)
        if norm is None:
            if rec.get("status") not in REAL_STATUSES:
                skipped_status += 1
            else:
                skipped_missing_gps += 1
            continue
        if norm["open_date"] is None:
            missing_open_date += 1
        out.append(norm)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))

    print(f"[done] wrote {len(out):,} stations to {OUT_PATH}", file=sys.stderr)
    print(f"  raw input rows           : {len(raw):,}", file=sys.stderr)
    print(f"  skipped (non-real status): {skipped_status:,}", file=sys.stderr)
    print(f"  skipped (missing GPS)    : {skipped_missing_gps:,}", file=sys.stderr)
    print(f"  no open_date in output   : {missing_open_date:,}", file=sys.stderr)
    print(
        f"  output file size         : {OUT_PATH.stat().st_size / 1024:.1f} KiB",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
