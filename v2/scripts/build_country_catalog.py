"""
Build a compact country catalog for V2 dropdowns and camera targeting.

Inputs:
  data/ne_10m_admin_0_countries.geojson
  v2/frontend/public/data/station_summary.json (optional)
  v2/frontend/public/data/ev_stock_country_year.json (optional)

Output:
  v2/frontend/public/data/country_catalog.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent.parent
COUNTRIES = ROOT / "data/ne_10m_admin_0_countries.geojson"
DATA_DIR = ROOT / "v2/frontend/public/data"
STATION_SUMMARY = DATA_DIR / "station_summary.json"
EV_STOCK = DATA_DIR / "ev_stock_country_year.json"
OUT = DATA_DIR / "country_catalog.json"


def load_optional_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def iter_lonlat_pairs(coords: Any) -> Iterable[tuple[float, float]]:
    if (
        isinstance(coords, list)
        and len(coords) >= 2
        and isinstance(coords[0], (int, float))
        and isinstance(coords[1], (int, float))
    ):
        yield float(coords[0]), float(coords[1])
        return
    if isinstance(coords, list):
        for item in coords:
            yield from iter_lonlat_pairs(item)


def geometry_bbox(geometry: dict[str, Any]) -> list[float] | None:
    pairs = list(iter_lonlat_pairs(geometry.get("coordinates")))
    if not pairs:
        return None
    lons = [p[0] for p in pairs]
    lats = [p[1] for p in pairs]
    return [min(lons), min(lats), max(lons), max(lats)]


def bbox_center(bbox: list[float]) -> dict[str, float]:
    west, south, east, north = bbox
    return {"lon": (west + east) / 2, "lat": (south + north) / 2}


def main() -> None:
    if not COUNTRIES.exists():
        raise FileNotFoundError(COUNTRIES)
    with COUNTRIES.open(encoding="utf-8") as f:
        geo = json.load(f)

    station_summary = load_optional_json(STATION_SUMMARY) or {}
    station_countries = (station_summary.get("countries") or {}) if isinstance(station_summary, dict) else {}
    ev_stock = load_optional_json(EV_STOCK) or {}
    ev_countries = ev_stock if isinstance(ev_stock, dict) else {}

    rows: list[dict[str, Any]] = []
    skipped_no_iso = 0
    skipped_no_bbox = 0
    for feature in geo.get("features", []):
        props = feature.get("properties") or {}
        iso = props.get("ISO_A3") or props.get("ADM0_A3")
        if not iso or iso == "-99":
            skipped_no_iso += 1
            continue
        bbox = geometry_bbox(feature.get("geometry") or {})
        if bbox is None:
            skipped_no_bbox += 1
            continue

        station_bucket = station_countries.get(iso) or {}
        ev_years = ev_countries.get(iso) or {}
        rows.append(
            {
                "iso_a3": iso,
                "name": props.get("NAME_LONG") or props.get("ADMIN") or props.get("NAME") or iso,
                "name_short": props.get("NAME") or props.get("ADMIN") or iso,
                "continent": props.get("CONTINENT") or None,
                "region_un": props.get("REGION_UN") or None,
                "subregion": props.get("SUBREGION") or None,
                "bbox": bbox,
                "center": bbox_center(bbox),
                "station_count": int(station_bucket.get("station_count") or 0),
                "dc_fast_count": int(station_bucket.get("dc_fast_count") or 0),
                "ultra_count": int(station_bucket.get("ultra_count") or 0),
                "has_station_data": bool(station_bucket),
                "has_ev_stock_data": bool(ev_years),
                "ev_stock_year_min": min((int(y) for y in ev_years.keys()), default=None),
                "ev_stock_year_max": max((int(y) for y in ev_years.keys()), default=None),
            }
        )

    rows.sort(key=lambda row: (str(row["name"]).lower(), row["iso_a3"]))
    out = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "Natural Earth 10m Admin 0 countries plus V2 generated data availability",
            "country_count": len(rows),
            "with_station_data": sum(1 for row in rows if row["has_station_data"]),
            "with_ev_stock_data": sum(1 for row in rows if row["has_ev_stock_data"]),
            "skipped_no_iso": skipped_no_iso,
            "skipped_no_bbox": skipped_no_bbox,
        },
        "countries": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)

    print(f"[done] wrote {OUT}", file=sys.stderr)
    print(f"  countries             : {len(rows):,}", file=sys.stderr)
    print(f"  with station data     : {out['meta']['with_station_data']:,}", file=sys.stderr)
    print(f"  with EV stock data    : {out['meta']['with_ev_stock_data']:,}", file=sys.stderr)
    print(f"  skipped no ISO        : {skipped_no_iso:,}", file=sys.stderr)
    print(f"  skipped no bbox       : {skipped_no_bbox:,}", file=sys.stderr)
    print(f"  output size           : {OUT.stat().st_size / 1024:.1f} KiB", file=sys.stderr)


if __name__ == "__main__":
    main()

