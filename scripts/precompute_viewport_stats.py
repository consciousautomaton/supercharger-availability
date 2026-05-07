"""
Precompute the per-bin × per-radius pop tables for fast viewport stats.

Output (under frontend/):
    viewport_pop_total.bin    — float32, shape (180, 360),
                                 total population per 1°×1° lat/lon bin
    viewport_pop_covered.bin  — float32, shape (180, 360, 501),
                                 cumulative population covered at radius
                                 r = 0, 1, 2, ..., 500 km
    viewport_manifest.json    — shape and encoding info for the frontend

Run from project root after precompute_tiles.py (which extracts the .npy
arrays this script reads):
    .venv/Scripts/python scripts/precompute_viewport_stats.py
"""

import argparse
import json
from pathlib import Path

import numpy as np
from distance_source import add_source_arg, frontend_path, sorted_distance_cache_name

ROOT = Path(__file__).resolve().parents[1]
NPY_DIR = ROOT / "data" / "npy"
OUT_DIR = ROOT / "frontend"

LAT_BINS = 180
LON_BINS = 360
RADIUS_STEP = 1
RADIUS_MAX = 500
N_RADII = RADIUS_MAX // RADIUS_STEP + 1
N_BINS = LAT_BINS * LON_BINS

CHUNK = 50_000_000


def main():
    parser = argparse.ArgumentParser()
    add_source_arg(parser)
    args = parser.parse_args()
    source = args.source

    lats = np.load(NPY_DIR / "lats_sorted.npy", mmap_mode="r")
    lons = np.load(NPY_DIR / "lons_sorted.npy", mmap_mode="r")
    pop  = np.load(NPY_DIR / "pop_sorted.npy",  mmap_mode="r")
    dist_path = NPY_DIR / sorted_distance_cache_name(source)
    if not dist_path.exists():
        raise FileNotFoundError(
            f"missing sorted distance cache: {dist_path}. "
            "Run scripts/precompute_tiles.py with the same --source first."
        )
    dist = np.load(dist_path, mmap_mode="r")
    n = len(lats)
    print(f"{n:,} cells across {n // CHUNK + 1} chunks of {CHUNK:,}, source={source}")

    pop_total      = np.zeros(N_BINS,            dtype=np.float64)
    pop_cov_per_r  = np.zeros((N_BINS, N_RADII), dtype=np.float64)

    for start in range(0, n, CHUNK):
        end = min(start + CHUNK, n)
        lats_c = np.array(lats[start:end])
        lons_c = np.array(lons[start:end])
        pop_c  = np.array(pop[start:end],  dtype=np.float64)
        dist_c = np.array(dist[start:end])

        bin_lat = np.clip(np.floor(lats_c + 90.0 ).astype(np.int32), 0, LAT_BINS - 1)
        bin_lon = np.clip(np.floor(lons_c + 180.0).astype(np.int32), 0, LON_BINS - 1)
        bin_idx = bin_lat * LON_BINS + bin_lon
        del bin_lat, bin_lon, lats_c, lons_c

        pop_total += np.bincount(bin_idx, weights=pop_c, minlength=N_BINS)

        dist_bucket = np.ceil(dist_c / RADIUS_STEP).astype(np.int32)
        dist_bucket = np.maximum(dist_bucket, 0)
        in_range = dist_bucket < N_RADII
        flat = bin_idx[in_range].astype(np.int64) * N_RADII + dist_bucket[in_range]
        cov = np.bincount(flat, weights=pop_c[in_range],
                          minlength=N_BINS * N_RADII)
        pop_cov_per_r += cov.reshape(N_BINS, N_RADII)

        print(f"  chunk {start:,}..{end:,} done")

    print("Cumsum across radius axis...")
    pop_covered = np.cumsum(pop_cov_per_r, axis=1).astype(np.float32)
    pop_covered = pop_covered.reshape(LAT_BINS, LON_BINS, N_RADII)
    pop_total = pop_total.astype(np.float32).reshape(LAT_BINS, LON_BINS)

    print(f"pop_total sum         = {pop_total.sum():,.0f}")
    print(f"pop_covered[..., 50]  = {pop_covered[..., 50].sum():,.0f}  (radius=50km)")
    print(f"pop_covered[..., 500] = {pop_covered[..., 500].sum():,.0f}  (radius=500km)")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pt_path = frontend_path(source, "viewport_pop_total", "bin")
    pc_path = frontend_path(source, "viewport_pop_covered", "bin")
    pop_total.tofile(pt_path)
    pop_covered.tofile(pc_path)
    print(f"Wrote {pt_path.name} ({pt_path.stat().st_size:,} B)")
    print(f"Wrote {pc_path.name} ({pc_path.stat().st_size:,} B)")

    manifest = {
        "lat_bins":         LAT_BINS,
        "lon_bins":         LON_BINS,
        "radius_step_km":   RADIUS_STEP,
        "n_radii":          N_RADII,
        "radius_max_km":    RADIUS_MAX,
        "dtype":            "float32",
        "pop_total_path":   pt_path.name,
        "pop_covered_path": pc_path.name,
        "distance_source":   source,
    }
    frontend_path(source, "viewport_manifest", "json").write_text(
        json.dumps(manifest, indent=2)
    )


if __name__ == "__main__":
    main()
