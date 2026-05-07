"""
Build sparse road-distance override tiles for one computed road region.

This intentionally does not rebuild the global tile pyramid. It writes only
the cells present in data/pixel_road_distances_<REGION>.npz to:

    frontend/tiles_road/

The frontend samples these override tiles first in road mode. Pixels with no
override alpha fall back to the existing frontend/tiles/ great-circle tile.

Run from project root:
    .venv/Scripts/python scripts/precompute_road_region_tiles.py --region DEU
"""

import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

import precompute_tiles as tilelib


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
NPY_DIR = DATA_DIR / "npy"
OUT_DIR = ROOT / "frontend" / "tiles_road"

TILE_SIZE = tilelib.TILE_SIZE
DEFAULT_WORKERS = min(2, os.cpu_count() or 1)


def require(path):
    if not path.exists():
        raise FileNotFoundError(f"missing required file: {path}")
    return path


def load_region(region):
    path = require(DATA_DIR / f"pixel_road_distances_{region}.npz")
    data = np.load(path)
    required = {"road_dist_km", "gc_dist_km", "global_pixel_index", "pop"}
    missing = required - set(data.files)
    if missing:
        raise KeyError(f"{path} missing required arrays: {sorted(missing)}")

    global_idx = np.asarray(data["global_pixel_index"], dtype=np.int64)
    road_dist = np.asarray(data["road_dist_km"], dtype=np.float32)
    gc_dist = np.asarray(data["gc_dist_km"], dtype=np.float32)
    pop = np.asarray(data["pop"], dtype=np.float32)

    dist = np.where(np.isfinite(road_dist), road_dist, gc_dist).astype(np.float32)
    usable = (global_idx >= 0) & np.isfinite(dist) & (pop > 0)

    road_count = int(np.isfinite(road_dist).sum())
    fallback_count = int((~np.isfinite(road_dist) & np.isfinite(gc_dist)).sum())
    print(f"Region file: {path.name}")
    print(f"  rows: {len(global_idx):,}")
    print(f"  finite road rows: {road_count:,}")
    print(f"  great-circle fallback rows inside region file: {fallback_count:,}")
    print(f"  usable override rows: {int(usable.sum()):,}")

    return global_idx[usable], dist[usable], pop[usable]


def build_footprints(global_idx, max_zoom, batch):
    x_path = require(NPY_DIR / "x_moll.npy")
    y_path = require(NPY_DIR / "y_moll.npy")
    x_moll = np.load(x_path, mmap_mode="r")
    y_moll = np.load(y_path, mmap_mode="r")
    n = len(global_idx)
    world_px = (1 << max_zoom) * TILE_SIZE

    x0_all = np.empty(n, dtype=np.int32)
    x1_all = np.empty(n, dtype=np.int32)
    y0_all = np.empty(n, dtype=np.int32)
    y1_all = np.empty(n, dtype=np.int32)

    t0 = time.time()
    print(f"Computing exact z={max_zoom} WebMercator footprints for {n:,} region cells...")
    for start in range(0, n, batch):
        end = min(start + batch, n)
        x0, x1, y0, y1, finite = tilelib.source_cell_bounds(
            np.array(x_moll[global_idx[start:end]]),
            np.array(y_moll[global_idx[start:end]]),
            max_zoom,
        )
        x0_all[start:end] = np.where(finite, np.clip(x0, 0, world_px - 1), 1)
        x1_all[start:end] = np.where(finite, np.clip(x1, 0, world_px - 1), 0)
        y0_all[start:end] = np.where(finite, np.clip(y0, 0, world_px - 1), 1)
        y1_all[start:end] = np.where(finite, np.clip(y1, 0, world_px - 1), 0)
        print(f"  footprints {end:,}/{n:,} | {time.time() - t0:.1f}s")

    valid = (x1_all >= x0_all) & (y1_all >= y0_all)
    print(f"  valid footprints: {int(valid.sum()):,} / {n:,}")
    return x0_all, x1_all, y0_all, y1_all, valid


def build_row_index(y0, y1, valid, max_zoom, batch):
    n_rows = 1 << max_zoom
    n = len(y0)
    ty0_all = np.clip(y0 // TILE_SIZE, 0, n_rows - 1)
    ty1_all = np.clip(y1 // TILE_SIZE, 0, n_rows - 1)
    valid = valid & (ty1_all >= ty0_all)

    counts_delta = np.zeros(n_rows + 1, dtype=np.int64)
    t0 = time.time()
    for start in range(0, n, batch):
        end = min(start + batch, n)
        v = valid[start:end]
        if v.any():
            ty0 = ty0_all[start:end][v]
            ty1 = ty1_all[start:end][v]
            np.add.at(counts_delta, ty0, 1)
            np.add.at(counts_delta, ty1 + 1, -1)
        print(f"  row counts {end:,}/{n:,} | {time.time() - t0:.1f}s")

    counts = np.cumsum(counts_delta[:-1])
    offsets = np.empty(n_rows + 1, dtype=np.int64)
    offsets[0] = 0
    offsets[1:] = np.cumsum(counts)
    total_refs = int(offsets[-1])
    row_indices = np.empty(total_refs, dtype=np.int32)
    cursor = offsets[:-1].copy()

    active_rows = np.flatnonzero(counts)
    if len(active_rows):
        print(f"  active z{max_zoom} rows: {int(active_rows[0])}..{int(active_rows[-1])} ({len(active_rows):,} rows)")
    print(f"  row references: {total_refs:,}")

    for start in range(0, n, batch):
        end = min(start + batch, n)
        v = valid[start:end]
        if v.any():
            local = np.arange(start, end, dtype=np.int32)[v]
            ty0 = ty0_all[start:end][v]
            ty1 = ty1_all[start:end][v]
            for row in range(int(ty0.min()), int(ty1.max()) + 1):
                mask = (ty0 <= row) & (ty1 >= row)
                n_mask = int(mask.sum())
                if n_mask == 0:
                    continue
                pos = int(cursor[row])
                row_indices[pos:pos + n_mask] = local[mask]
                cursor[row] += n_mask
        print(f"  row index {end:,}/{n:,} | {time.time() - t0:.1f}s")

    return offsets, row_indices, active_rows


def stream_region_pyramid(max_zoom, fp_x0, fp_x1, fp_y0, fp_y1, row_offsets,
                          row_indices, pop, dist, pop_max, workers,
                          max_pending):
    tilelib.OUT_DIR = OUT_DIR
    tilelib.ENCODE_WORKERS = workers
    tilelib.MAX_PENDING_ENCODES = max_pending
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    n_tiles = 1 << max_zoom
    staging = [dict() for _ in range(max_zoom + 1)]
    stats = [0] * (max_zoom + 1)
    pending = set()
    t0 = time.time()
    rows_with_refs = 0
    max_row_refs = 0

    print(f"Encoding sparse override pyramid z=0..{max_zoom}")
    print(f"  output: {OUT_DIR}")
    print(f"  workers: {workers}")
    print(f"  max pending encodes: {max_pending}")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for tile_y in range(n_tiles):
            start = int(row_offsets[tile_y])
            end = int(row_offsets[tile_y + 1])
            refs = end - start
            if refs:
                rows_with_refs += 1
                max_row_refs = max(max_row_refs, refs)
                row_tiles = tilelib.render_max_zoom_row(
                    tile_y, row_indices[start:end],
                    fp_x0, fp_x1, fp_y0, fp_y1, pop, dist,
                )
                for (x, y), (gd, gp) in row_tiles.items():
                    pending = tilelib.submit_encode(
                        executor, pending, max_zoom, x, y, gd, gp,
                        pop_max, stats,
                    )
                    tilelib.propagate_up(max_zoom, x, y, gd, gp, staging)

            if tile_y % 2 == 1:
                pending = tilelib.flush_row(
                    max_zoom - 1, tile_y // 2, staging, pop_max,
                    stats, executor, pending,
                )

            should_print = refs or (tile_y + 1) % 128 == 0 or tile_y == n_tiles - 1
            if should_print:
                pending = tilelib.drain_encodes(pending, stats)
                staged_total = sum(len(s) for s in staging)
                written_summary = " ".join(
                    f"z{i}={c:,}" for i, c in enumerate(stats) if c
                ) or "(none yet)"
                print(
                    f"  row {tile_y + 1:>5}/{n_tiles} | refs: {refs:,} | "
                    f"written: {written_summary} | in-flight: {staged_total + len(pending)} | "
                    f"{time.time() - t0:.1f}s"
                )

        leftover = sum(len(s) for s in staging)
        if leftover:
            print(f"  flushing {leftover:,} staged parent tile(s)")
            for z in range(max_zoom - 1, -1, -1):
                for key in list(staging[z].keys()):
                    x, y = key
                    gd, gp = staging[z].pop(key)
                    pending = tilelib.submit_encode(
                        executor, pending, z, x, y, gd, gp, pop_max, stats,
                    )
                    if z > 0:
                        tilelib.propagate_up(z, x, y, gd, gp, staging)

        tilelib.drain_encodes(pending, stats, wait_all=True)

    print(f"Rows with region references: {rows_with_refs:,}")
    print(f"Max refs in one z{max_zoom} row: {max_row_refs:,}")
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="DEU")
    parser.add_argument("--max-zoom", type=int, default=11)
    parser.add_argument("--batch", type=int, default=500_000)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--max-pending", type=int, default=None)
    args = parser.parse_args()

    workers = max(1, args.workers)
    max_pending = args.max_pending or max(2, workers * 4)

    for name in ("pop_max.txt",):
        require(NPY_DIR / name)

    global_idx, dist, pop = load_region(args.region)
    pop_max = float((NPY_DIR / "pop_max.txt").read_text().strip())
    print(f"max log1p(pop) = {pop_max:.4f}")

    fp_x0, fp_x1, fp_y0, fp_y1, valid = build_footprints(
        global_idx, args.max_zoom, args.batch,
    )
    dist = dist[valid]
    pop = pop[valid]
    fp_x0 = fp_x0[valid]
    fp_x1 = fp_x1[valid]
    fp_y0 = fp_y0[valid]
    fp_y1 = fp_y1[valid]
    valid = np.ones(len(pop), dtype=bool)

    offsets, row_indices, active_rows = build_row_index(
        fp_y0, fp_y1, valid, args.max_zoom, args.batch,
    )

    if len(active_rows) == 0:
        print("No active rows; nothing to encode.")
        return

    t0 = time.time()
    stats = stream_region_pyramid(
        args.max_zoom,
        fp_x0, fp_x1, fp_y0, fp_y1,
        offsets, row_indices,
        pop, dist, pop_max,
        workers, max_pending,
    )
    print(f"\nAll done in {time.time() - t0:.1f}s encoding time.")
    for z, count in enumerate(stats):
        print(f"  z={z}: {count:,} override tiles")


if __name__ == "__main__":
    main()
