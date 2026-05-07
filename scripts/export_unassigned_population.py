"""
Export population cells that do not spatially assign to a country.

This does not write individual 100 m cells. That would be too large to review
manually. Instead it aggregates unassigned cells into lon/lat bins and writes:

    frontend/unassigned_population_bins.geojson
    frontend/unassigned_population_bins.csv

Run from project root:
    .venv/Scripts/python scripts/export_unassigned_population.py

Optional bin size in degrees:
    $env:UNASSIGNED_POP_BIN_DEG = "0.1"
    .venv/Scripts/python scripts/export_unassigned_population.py
"""

import csv
import json
import os
from pathlib import Path

import numpy as np

from precompute_country_stats import (
    CHUNK,
    DISTANCES_NPZ,
    FRONTEND_DIR,
    PIXELS_NPZ,
    load_country_features,
    prepare_country_shapes,
    rasterize_sampled_country_ids,
    xy_to_row_col,
)


BIN_DEG = float(os.environ.get("UNASSIGNED_POP_BIN_DEG", "0.1"))
GEOJSON_OUT = FRONTEND_DIR / "unassigned_population_bins.geojson"
CSV_OUT = FRONTEND_DIR / "unassigned_population_bins.csv"


def main():
    features = load_country_features()
    height, width, transform, shapes = prepare_country_shapes(features)

    pixels = np.load(PIXELS_NPZ)
    distances = np.load(DISTANCES_NPZ)
    x_moll = pixels["x_moll"]
    y_moll = pixels["y_moll"]
    lons = pixels["lons"]
    lats = pixels["lats"]
    pop = distances["pop"]
    n = len(pop)

    lon_bins = int(np.ceil(360.0 / BIN_DEG))
    lat_bins = int(np.ceil(180.0 / BIN_DEG))
    n_bins = lon_bins * lat_bins

    pop_sum = np.zeros(n_bins, dtype=np.float64)
    cell_count = np.zeros(n_bins, dtype=np.uint32)

    total_pop = 0.0
    unassigned_pop = 0.0
    unassigned_cells = 0

    print(f"Finding unassigned population cells across {n:,} populated cells")
    print(f"Binning to {BIN_DEG:g} degree grid ({lon_bins:,} x {lat_bins:,})")

    for start in range(0, n, CHUNK):
        end = min(start + CHUNK, n)
        x = np.asarray(x_moll[start:end], dtype=np.float64)
        y = np.asarray(y_moll[start:end], dtype=np.float64)
        pop_chunk = np.asarray(pop[start:end], dtype=np.float64)
        total_pop += float(pop_chunk.sum())

        row, col, valid_xy = xy_to_row_col(x, y, transform, height, width)
        ids = rasterize_sampled_country_ids(row, col, valid_xy, shapes, transform)
        missing = ids == 0

        if missing.any():
            missing_pop = pop_chunk[missing]
            missing_lons = np.asarray(lons[start:end], dtype=np.float64)[missing]
            missing_lats = np.asarray(lats[start:end], dtype=np.float64)[missing]

            lon_idx = np.floor((missing_lons + 180.0) / BIN_DEG).astype(np.int64)
            lat_idx = np.floor((missing_lats + 90.0) / BIN_DEG).astype(np.int64)
            valid = (
                (lon_idx >= 0) & (lon_idx < lon_bins) &
                (lat_idx >= 0) & (lat_idx < lat_bins)
            )

            flat = lat_idx[valid] * lon_bins + lon_idx[valid]
            pop_sum += np.bincount(flat, weights=missing_pop[valid], minlength=n_bins)
            cell_count += np.bincount(flat, minlength=n_bins).astype(np.uint32)
            unassigned_pop += float(missing_pop[valid].sum())
            unassigned_cells += int(valid.sum())

        print(f"  cells {start:,}..{end:,} done")

    nonzero = np.flatnonzero(cell_count)
    order = nonzero[np.argsort(pop_sum[nonzero])[::-1]]

    FRONTEND_DIR.mkdir(parents=True, exist_ok=True)

    features_out = []
    with CSV_OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["lon", "lat", "population", "cell_count"])

        for idx in order:
            lat_idx = int(idx // lon_bins)
            lon_idx = int(idx % lon_bins)
            lon = -180.0 + (lon_idx + 0.5) * BIN_DEG
            lat = -90.0 + (lat_idx + 0.5) * BIN_DEG
            bin_pop = float(pop_sum[idx])
            count = int(cell_count[idx])

            writer.writerow([f"{lon:.6f}", f"{lat:.6f}", f"{bin_pop:.3f}", count])
            features_out.append({
                "type": "Feature",
                "properties": {
                    "population": bin_pop,
                    "cell_count": count,
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat],
                },
            })

    geojson = {
        "type": "FeatureCollection",
        "properties": {
            "bin_degrees": BIN_DEG,
            "total_population": total_pop,
            "unassigned_population": unassigned_pop,
            "unassigned_cells": unassigned_cells,
        },
        "features": features_out,
    }
    GEOJSON_OUT.write_text(json.dumps(geojson, separators=(",", ":")), encoding="utf-8")

    print(f"Unassigned population: {unassigned_pop:,.0f} / {total_pop:,.0f}")
    print(f"Unassigned cells: {unassigned_cells:,}")
    print(f"Wrote {CSV_OUT} ({CSV_OUT.stat().st_size:,} bytes)")
    print(f"Wrote {GEOJSON_OUT} ({GEOJSON_OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
