import argparse
import json
from pathlib import Path

import numpy as np
from distance_source import add_source_arg, data_path, distance_npz_path, frontend_path


CHARGERS_NPZ = Path("data/chargers.npz")
CHARGERS_OUT = Path("frontend/chargers.json")

# Raster grid: 0.05 deg/cell, 85S-85N x 180W-180E
NCOLS = 7200
NROWS = 3400
LAT_MAX = 85.0
LAT_MIN = -85.0
LON_MIN = -180.0
LON_MAX = 180.0
MAX_KM = 500


def build(source):
    distances_npz = distance_npz_path(source)
    raster_out = data_path(source, "global_min_dist", "bin")
    meta_out = data_path(source, "global_raster_meta", "json")
    cumulative_outs = (
        data_path(source, "pop_cumulative", "json"),
        frontend_path(source, "pop_cumulative", "json"),
    )

    print(f"Loading {distances_npz}...")
    d = np.load(distances_npz)
    lons = d["lons"]          # float32 (N,)
    lats = d["lats"]          # float32 (N,)
    pop = d["pop"]            # float32 (N,)
    min_dist_km = d["min_dist_km"]   # float32 (N,)
    n = len(lons)
    print(f"Loaded {n:,} pixels, source={source}")

    # ------------------------------------------------------------------ #
    # 1. pop_cumulative.json                                             #
    # ------------------------------------------------------------------ #
    print("\nBuilding pop_cumulative.json...")

    dist_bucket = np.ceil(np.maximum(min_dist_km, 0.0)).astype(np.int32)
    in_range = dist_bucket <= MAX_KM
    hist = np.bincount(
        dist_bucket[in_range],
        weights=pop[in_range],
        minlength=MAX_KM + 1,
    )
    cumulative = np.cumsum(hist)   # shape (501,), index r means <= r km
    pop_max_path = Path("data/npy/pop_max.txt")
    pop_max_log1p = (
        float(pop_max_path.read_text().strip()) if pop_max_path.exists() else 0.0
    )

    total_pop = float(pop.sum())
    print(f"  Total population in dataset : {total_pop/1e9:.3f} B")
    print(f"  Within 10 km  : {cumulative[10]/1e9:.3f} B  ({cumulative[10]/total_pop*100:.2f}%)")
    print(f"  Within 50 km  : {cumulative[50]/1e9:.3f} B  ({cumulative[50]/total_pop*100:.2f}%)")
    print(f"  Within 100 km : {cumulative[100]/1e9:.3f} B  ({cumulative[100]/total_pop*100:.2f}%)")
    print(f"  Within 500 km : {cumulative[500]/1e9:.3f} B  ({cumulative[500]/total_pop*100:.2f}%)")

    for out_path in cumulative_outs:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "radius_step_km": 1,
                "radius_max_km": MAX_KM,
                "total_pop": total_pop,
                "pop_max_log1p": pop_max_log1p,
                "distance_source": source,
                "cumulative": cumulative.tolist(),
            }, f)
        print(f"  Saved {out_path}")

    # ------------------------------------------------------------------ #
    # 2. global_min_dist.bin                                             #
    # ------------------------------------------------------------------ #
    print("\nBuilding global_min_dist.bin...")

    col_idx = ((lons - LON_MIN) / (LON_MAX - LON_MIN) * NCOLS).astype(np.int32)
    row_idx = ((LAT_MAX - lats) / (LAT_MAX - LAT_MIN) * NROWS).astype(np.int32)

    valid = (
        (col_idx >= 0) & (col_idx < NCOLS) &
        (row_idx >= 0) & (row_idx < NROWS)
    )
    print(f"  Valid pixels: {valid.sum():,} / {n:,}")

    flat_idx = (
        row_idx[valid].astype(np.int64) * NCOLS +
        col_idx[valid].astype(np.int64)
    )
    dist_u16 = np.clip(min_dist_km[valid], 0, 65534).astype(np.uint16)

    raster = np.full(NROWS * NCOLS, 65535, dtype=np.uint16)
    print("  Scattering min distances into raster...")
    np.minimum.at(raster, flat_idx, dist_u16)

    covered = (raster < 65535).sum()
    print(f"  Raster cells with data: {covered:,} / {NROWS*NCOLS:,}")

    raster_out.parent.mkdir(parents=True, exist_ok=True)
    raster.reshape(NROWS, NCOLS).tofile(raster_out)
    print(f"  Saved {raster_out}")

    meta = {
        "ncols": NCOLS,
        "nrows": NROWS,
        "lat_max": LAT_MAX,
        "lat_min": LAT_MIN,
        "lon_min": LON_MIN,
        "lon_max": LON_MAX,
        "dtype": "uint16",
        "nodata": 65535,
        "distance_source": source,
    }
    with open(meta_out, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"  Saved {meta_out}")

    # ------------------------------------------------------------------ #
    # 3. chargers.json                                                   #
    # ------------------------------------------------------------------ #
    print("\nBuilding chargers.json...")
    c = np.load(CHARGERS_NPZ)
    c_lats = c["lats"]
    c_lons = c["lons"]
    positions = []
    for lon, lat in zip(c_lons, c_lats):
        positions.append(round(float(lon), 5))
        positions.append(round(float(lat), 5))
    CHARGERS_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(CHARGERS_OUT, "w", encoding="utf-8") as f:
        json.dump({"positions": positions}, f, separators=(",", ":"))
    print(
        f"  {len(c_lats):,} chargers -> {CHARGERS_OUT} "
        f"({CHARGERS_OUT.stat().st_size // 1024} KB)"
    )

    print("\nDone.")


def main():
    parser = argparse.ArgumentParser()
    add_source_arg(parser)
    args = parser.parse_args()
    build(args.source)


if __name__ == "__main__":
    main()
