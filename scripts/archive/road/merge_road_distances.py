"""
Merge per-region OSM road-distance outputs into a global distance file.

The base great-circle file is left untouched:
    data/pixel_distances.npz

Merged output:
    data/pixel_distances_road.npz

Finite per-region road distances replace the base distance for their
global_pixel_index. Infinite road distances are treated as "not computed /
beyond cap" and fall back to the base great-circle distance. If multiple
regions cover the same cell, the smallest finite road distance wins.
"""

from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
BASE_PATH = DATA_DIR / "pixel_distances.npz"
OUT_PATH = DATA_DIR / "pixel_distances_road.npz"
REGION_GLOB = "pixel_road_distances_*.npz"


def region_name(path):
    stem = path.stem
    return stem.removeprefix("pixel_road_distances_")


def main():
    if not BASE_PATH.exists():
        raise FileNotFoundError(f"missing base distance file: {BASE_PATH}")

    region_paths = sorted(DATA_DIR.glob(REGION_GLOB))
    if not region_paths:
        raise FileNotFoundError(f"no region road files found: {DATA_DIR / REGION_GLOB}")

    print(f"Loading base great-circle distances: {BASE_PATH}")
    base = np.load(BASE_PATH)
    required = {"lons", "lats", "pop", "min_dist_km"}
    missing = required - set(base.files)
    if missing:
        raise KeyError(f"{BASE_PATH} missing required arrays: {sorted(missing)}")

    great_circle = base["min_dist_km"]
    merged = np.array(great_circle, copy=True)
    road_mask = np.zeros(merged.shape, dtype=bool)
    n_cells = len(merged)

    print(f"Found {len(region_paths)} road region file(s)")
    for path in region_paths:
        region = region_name(path)
        data = np.load(path)
        for key in ("road_dist_km", "global_pixel_index"):
            if key not in data.files:
                raise KeyError(f"{path} missing required array: {key}")

        road_dist = np.asarray(data["road_dist_km"], dtype=np.float32)
        global_idx = np.asarray(data["global_pixel_index"], dtype=np.int64)
        finite = np.isfinite(road_dist)
        in_bounds = (global_idx >= 0) & (global_idx < n_cells)
        usable = finite & in_bounds

        if finite.any() and not in_bounds[finite].all():
            invalid = int((finite & ~in_bounds).sum())
            print(f"  {region}: ignored {invalid:,} finite rows with out-of-range global index")

        idx = global_idx[usable]
        dist = road_dist[usable]
        if len(idx) == 0:
            print(f"  {region}: 0 cells overwritten (no finite in-range road distances)")
            continue

        already_road = road_mask[idx]
        replace = (~already_road) | (dist < merged[idx])
        replace_count = int(replace.sum())
        if replace_count:
            replace_idx = idx[replace]
            merged[replace_idx] = dist[replace]
            road_mask[replace_idx] = True

        fallback_count = int((~finite).sum())
        print(
            f"  {region}: {replace_count:,} cells overwritten, "
            f"{fallback_count:,} inf rows left as great-circle fallback"
        )

    road_count = int(road_mask.sum())
    if road_count:
        ratio_mask = road_mask & np.isfinite(great_circle) & (great_circle > 0)
        ratio = merged[ratio_mask] / great_circle[ratio_mask]
        median_ratio = float(np.median(ratio)) if len(ratio) else float("nan")
    else:
        median_ratio = float("nan")

    print(f"Total cells now using road distance: {road_count:,} / {n_cells:,}")
    print(f"Median road/great-circle ratio over road subset: {median_ratio:.3f}")

    payload = {key: base[key] for key in base.files}
    payload["min_dist_km"] = merged.astype(np.float32, copy=False)
    np.savez(OUT_PATH, **payload)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
