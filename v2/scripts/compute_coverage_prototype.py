"""
Compute coarse V2 coverage prototype stats.

This is NOT the final WebGPU coverage engine. It is a small, deterministic
sanity-check over the 0.25-degree 2030 population grid, useful for verifying
that station filters, years, and population weights produce plausible numbers.

Output:
  v2/frontend/public/data/coverage_prototype_summary.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "v2/frontend/public/data"

STATION_FILES = [
    DATA_DIR / "chargers_tesla.json",
    DATA_DIR / "chargers_bnetza.json",
    DATA_DIR / "chargers_irve.json",
    DATA_DIR / "chargers_nobil.json",
    DATA_DIR / "chargers_afdc.json",
]

EARTH_RADIUS_KM = 6371.0088


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radii", default="10,50,100", help="Comma-separated radii in km.")
    ap.add_argument("--years", default="2012,2016,2020,2024,2026", help="Comma-separated timeline years.")
    ap.add_argument("--datasets", default="fast_only,all_public,tesla_only", help="Comma-separated dataset filters.")
    ap.add_argument("--index-cell-deg", type=float, default=1.0, help="Spatial index cell size in degrees.")
    return ap.parse_args()


def parse_csv_numbers(raw: str, cast: type) -> list[Any]:
    out = []
    for item in raw.split(","):
        item = item.strip()
        if item:
            out.append(cast(item))
    return out


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def open_year(station: dict[str, Any]) -> int | None:
    raw = station.get("open_date")
    if not raw:
        return None
    try:
        return int(str(raw)[:4])
    except ValueError:
        return None


def is_open_today(station: dict[str, Any]) -> bool:
    if station.get("network") == "Tesla":
        return station.get("status") in {"OPEN", "EXPANDING"}
    return station.get("close_date") is None


def station_matches(station: dict[str, Any], dataset: str, year: int) -> bool:
    if dataset == "fast_only" and station.get("kind") != "fast":
        return False
    if dataset == "tesla_only" and station.get("network") != "Tesla":
        return False
    y = open_year(station)
    if y is None or y > year:
        return False
    if year >= 2026 and not is_open_today(station):
        return False
    return True


def load_stations() -> list[dict[str, Any]]:
    stations: list[dict[str, Any]] = []
    for path in STATION_FILES:
        if not path.exists():
            print(f"[skip] missing station file {path}", file=sys.stderr)
            continue
        data = load_json(path)
        if not isinstance(data, list):
            raise RuntimeError(f"{path} did not contain a station list")
        stations.extend(row for row in data if isinstance(row, dict))
    return stations


def load_population_cells() -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    meta = load_json(DATA_DIR / "pop_025deg_world_2030_meta.json")
    values = np.fromfile(DATA_DIR / "pop_025deg_world_2030.bin", dtype=np.float32)
    width = int(meta["width"])
    cell_deg = float(meta["cell_degrees"])
    nonzero = np.flatnonzero(values > 0)
    pop = values[nonzero].astype(np.float64)
    x = nonzero % width
    y = nonzero // width
    lon = float(meta["bounds"]["west"]) + (x.astype(np.float64) + 0.5) * cell_deg
    lat = float(meta["bounds"]["north"]) - (y.astype(np.float64) + 0.5) * cell_deg
    return lat, lon, pop, meta


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class SpatialIndex:
    def __init__(self, stations: list[dict[str, Any]], cell_deg: float) -> None:
        self.cell_deg = cell_deg
        self.buckets: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
        for station in stations:
            lat = station.get("lat")
            lon = station.get("lon")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            self.buckets[self.key(float(lat), float(lon))].append((float(lat), float(lon)))

    def key(self, lat: float, lon: float) -> tuple[int, int]:
        return (
            math.floor((lon + 180.0) / self.cell_deg),
            math.floor((lat + 90.0) / self.cell_deg),
        )

    def within(self, lat: float, lon: float, radius_km: float) -> bool:
        cx, cy = self.key(lat, lon)
        lat_cells = math.ceil(radius_km / 111.0 / self.cell_deg) + 1
        cos_lat = max(0.05, math.cos(math.radians(lat)))
        lon_cells = math.ceil(radius_km / (111.0 * cos_lat) / self.cell_deg) + 1
        for dy in range(-lat_cells, lat_cells + 1):
            for dx in range(-lon_cells, lon_cells + 1):
                for slat, slon in self.buckets.get((cx + dx, cy + dy), []):
                    if haversine_km(lat, lon, slat, slon) <= radius_km:
                        return True
        return False


def compute_one(
    lat: np.ndarray,
    lon: np.ndarray,
    pop: np.ndarray,
    index: SpatialIndex,
    radius_km: float,
) -> tuple[float, int]:
    covered_pop = 0.0
    covered_cells = 0
    n = len(pop)
    for i in range(n):
        if index.within(float(lat[i]), float(lon[i]), radius_km):
            covered_pop += float(pop[i])
            covered_cells += 1
        if (i + 1) % 50_000 == 0:
            print(f"    checked {i + 1:,}/{n:,} populated cells", file=sys.stderr)
    return covered_pop, covered_cells


def main() -> None:
    args = parse_args()
    radii = parse_csv_numbers(args.radii, float)
    years = parse_csv_numbers(args.years, int)
    datasets = parse_csv_numbers(args.datasets, str)

    lat, lon, pop, meta = load_population_cells()
    stations = load_stations()
    total_pop = float(pop.sum())
    print(f"[load] populated cells: {len(pop):,}; pop sum={total_pop:,.0f}", file=sys.stderr)
    print(f"[load] station records: {len(stations):,}", file=sys.stderr)

    results: list[dict[str, Any]] = []
    start_all = time.time()
    for dataset in datasets:
        for year in years:
            visible = [s for s in stations if station_matches(s, dataset, year)]
            index = SpatialIndex(visible, args.index_cell_deg)
            print(
                f"\n[scenario] dataset={dataset} year={year} stations={len(visible):,}",
                file=sys.stderr,
            )
            for radius in radii:
                start = time.time()
                covered_pop, covered_cells = compute_one(lat, lon, pop, index, radius)
                pct = covered_pop / total_pop * 100 if total_pop else 0.0
                elapsed = time.time() - start
                print(
                    f"  radius={radius:g}km covered_pop={covered_pop:,.0f} ({pct:.2f}%) "
                    f"covered_cells={covered_cells:,} elapsed={elapsed:.1f}s",
                    file=sys.stderr,
                )
                results.append(
                    {
                        "dataset": dataset,
                        "year": year,
                        "radius_km": radius,
                        "station_count": len(visible),
                        "covered_population": covered_pop,
                        "total_population": total_pop,
                        "covered_percent": pct,
                        "covered_cells": covered_cells,
                        "total_nonzero_cells": int(len(pop)),
                    }
                )

    out = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "Coarse prototype computed from pop_025deg_world_2030 and station JSONs",
            "population_grid": {
                "epoch": meta["epoch"],
                "cell_degrees": meta["cell_degrees"],
                "nonzero_cells": int(len(pop)),
            },
            "notes": [
                "Prototype only. Final V2 coverage must use WebGPU and higher-resolution population data.",
                "Population is fixed to 2030 in this prototype, even for historical charger years.",
            ],
        },
        "results": results,
    }
    out_path = DATA_DIR / "coverage_prototype_summary.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)
    print(f"\n[done] wrote {out_path}", file=sys.stderr)
    print(f"  scenarios: {len(results):,}", file=sys.stderr)
    print(f"  elapsed  : {time.time() - start_all:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()

