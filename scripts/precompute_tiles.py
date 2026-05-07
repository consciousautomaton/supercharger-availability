"""
Streaming bottom-up tile pyramid precompute.

Walks z=max_zoom rows in lat-sorted order, encoding each populated tile to
PNG and propagating its 2x2 reduction into the parent's staging grid as we
go. Whenever a parent row's two child rows have both been processed, that
parent row is flushed (encoded + propagated up) and the cascade recurses.

This keeps peak memory bounded at ~one row per zoom level — a few hundred
MB even at max_zoom=11 (where the naive in-memory approach would need 40+
GB).

Reuses the one-time setup from earlier runs (data/npy/ extracted +
lat-sorted, plus pop_max.txt cache). If the cache is missing, the script
rebuilds it before rendering.

Run from project root:
    .venv/Scripts/python scripts/precompute_tiles.py [max_zoom=11]
"""

import argparse
import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

import numpy as np
from PIL import Image
from pyproj import Transformer
from distance_source import (
    add_source_arg,
    distance_npz_path,
    sorted_distance_cache_name,
    tiles_dir,
)

ROOT = Path(__file__).resolve().parents[1]
NPZ_PATH = ROOT / "data" / "pixel_distances.npz"
PIXELS_NPZ_PATH = ROOT / "data" / "populated_pixels.npz"
NPY_DIR  = ROOT / "data" / "npy"
OUT_DIR  = ROOT / "frontend" / "tiles"
DISTANCE_SOURCE = "great-circle"

TILE_SIZE = 256
MAX_RADIUS_KM = 500.0
DIST_CODE_MAX = 65535
PNG_COMPRESS_LEVEL = 1
DEFAULT_ENCODE_WORKERS = min(16, os.cpu_count() or 4)
ENCODE_WORKERS = max(1, int(os.environ.get(
    "TILE_ENCODE_WORKERS", DEFAULT_ENCODE_WORKERS
)))
MAX_PENDING_ENCODES = ENCODE_WORKERS * 32
RAW_KEYS = ("lons", "lats", "pop")
SOURCE_KEYS = ("x_moll", "y_moll")
CELL_M = 100.0
HALF_CELL_M = CELL_M / 2.0
CELL_BATCH = 500_000
CONTRIB_CHUNK = 250_000
FOOTPRINT_CACHE_BATCH = 1_000_000
ROW_INDEX_BATCH = 2_000_000
MOLLWEIDE_TO_WGS84 = Transformer.from_crs(
    "ESRI:54009", "EPSG:4326", always_xy=True
)


# ────────────────────────────── one-time setup ──────────────────────────────

def ensure_npy_extracted():
    NPY_DIR.mkdir(parents=True, exist_ok=True)

    missing_raw = [k for k in RAW_KEYS if not (NPY_DIR / f"{k}.npy").exists()]
    if missing_raw:
        print(f"Extracting {NPZ_PATH.name} to .npy files (one-time)...")
        d = np.load(NPZ_PATH)
        for k in missing_raw:
            print(f"  saving {k}.npy")
            np.save(NPY_DIR / f"{k}.npy", d[k])
        del d

    if DISTANCE_SOURCE == "great-circle" and not (NPY_DIR / "min_dist_km.npy").exists():
        print(f"Extracting min_dist_km.npy from {NPZ_PATH.name}...")
        d = np.load(NPZ_PATH)
        np.save(NPY_DIR / "min_dist_km.npy", d["min_dist_km"])
        del d

    missing_source = [
        k for k in SOURCE_KEYS if not (NPY_DIR / f"{k}.npy").exists()
    ]
    if missing_source:
        print(f"Extracting source-cell geometry from {PIXELS_NPZ_PATH.name}...")
        d = np.load(PIXELS_NPZ_PATH)
        for k in missing_source:
            print(f"  saving {k}.npy")
            np.save(NPY_DIR / f"{k}.npy", d[k])
        del d


def ensure_lat_sorted():
    out_keys = (
        "lats_sorted", "lons_sorted", "pop_sorted",
        "x_moll_sorted", "y_moll_sorted",
    )
    if all((NPY_DIR / f"{k}.npy").exists() for k in out_keys):
        return
    print("Re-sorting arrays by latitude (one-time)...")
    lats = np.load(NPY_DIR / "lats.npy")
    print(f"  argsort over {len(lats):,} cells...")
    order = np.argsort(lats, kind="stable").astype(np.int64)
    if not (NPY_DIR / "lats_sorted.npy").exists():
        print("  saving lats_sorted.npy")
        np.save(NPY_DIR / "lats_sorted.npy", lats[order])
    del lats
    for src, dst in [("lons", "lons_sorted"),
                     ("pop", "pop_sorted"),
                     ("x_moll", "x_moll_sorted"),
                     ("y_moll", "y_moll_sorted")]:
        if (NPY_DIR / f"{dst}.npy").exists():
            continue
        print(f"  saving {dst}.npy")
        a = np.load(NPY_DIR / f"{src}.npy")
        np.save(NPY_DIR / f"{dst}.npy", a[order])
        del a
    del order


def ensure_distance_sorted():
    out_path = NPY_DIR / sorted_distance_cache_name(DISTANCE_SOURCE)
    if out_path.exists() and out_path.stat().st_mtime >= NPZ_PATH.stat().st_mtime:
        return
    if out_path.exists():
        print(f"Refreshing stale sorted distance cache: {out_path.name}")
        out_path.unlink()

    print(f"Sorting {NPZ_PATH.name}:min_dist_km -> {out_path.name}...")
    lats = np.load(NPY_DIR / "lats.npy")
    order = np.argsort(lats, kind="stable").astype(np.int64)
    del lats

    if DISTANCE_SOURCE == "great-circle" and (NPY_DIR / "min_dist_km.npy").exists():
        dist = np.load(NPY_DIR / "min_dist_km.npy")
    else:
        d = np.load(NPZ_PATH)
        dist = d["min_dist_km"]
    np.save(out_path, dist[order])
    del dist, order


def ensure_pop_max():
    cache = NPY_DIR / "pop_max.txt"
    if cache.exists():
        return float(cache.read_text().strip())
    print("Scanning POP for max log1p(pop) (one-time)...")
    pop = np.load(NPY_DIR / "pop_sorted.npy", mmap_mode="r")
    m = float(np.log1p(pop.max()))
    cache.write_text(str(m))
    return m


# ────────────────────────────── tile rendering ──────────────────────────────

def global_x_pixels(lons, z):
    n = 1 << z
    return (lons + 180.0) / 360.0 * n * TILE_SIZE


def global_y_pixels(lats, z):
    n = 1 << z
    lats = np.clip(lats, -85.05112878, 85.05112878)
    sinlat = np.sin(np.radians(lats))
    return (0.5 - np.log((1 + sinlat) / (1 - sinlat)) / (4 * np.pi)) \
           * n * TILE_SIZE


def source_cell_bounds(x_moll, y_moll, z):
    """Return WebMercator pixel bboxes for exact 100m Mollweide cell corners."""
    x_left = x_moll - HALF_CELL_M
    x_right = x_moll + HALF_CELL_M
    y_top = y_moll + HALF_CELL_M
    y_bottom = y_moll - HALF_CELL_M

    corner_x = np.concatenate([x_left, x_right, x_right, x_left])
    corner_y = np.concatenate([y_top, y_top, y_bottom, y_bottom])
    corner_lon, corner_lat = MOLLWEIDE_TO_WGS84.transform(corner_x, corner_y)

    n = len(x_moll)
    px = global_x_pixels(corner_lon, z).reshape(4, n)
    py = global_y_pixels(corner_lat, z).reshape(4, n)
    finite = np.isfinite(px).all(axis=0) & np.isfinite(py).all(axis=0)
    px = np.where(np.isfinite(px), px, 0.0)
    py = np.where(np.isfinite(py), py, 0.0)
    return (
        np.floor(px.min(axis=0)).astype(np.int32),
        np.floor(px.max(axis=0)).astype(np.int32),
        np.floor(py.min(axis=0)).astype(np.int32),
        np.floor(py.max(axis=0)).astype(np.int32),
        finite,
    )


def footprint_cache_paths(max_zoom):
    prefix = NPY_DIR / f"wm_z{max_zoom}"
    return {
        "x0": prefix.with_name(prefix.name + "_x0.npy"),
        "x1": prefix.with_name(prefix.name + "_x1.npy"),
        "y0": prefix.with_name(prefix.name + "_y0.npy"),
        "y1": prefix.with_name(prefix.name + "_y1.npy"),
        "done": prefix.with_name(prefix.name + "_footprints.done"),
    }


def row_index_cache_paths(max_zoom):
    prefix = NPY_DIR / f"wm_z{max_zoom}"
    return {
        "counts": prefix.with_name(prefix.name + "_row_counts.npy"),
        "offsets": prefix.with_name(prefix.name + "_row_offsets.npy"),
        "indices": prefix.with_name(prefix.name + "_row_indices.npy"),
        "done": prefix.with_name(prefix.name + "_row_index.done"),
    }


def ensure_footprint_cache(max_zoom):
    paths = footprint_cache_paths(max_zoom)
    data_paths = [paths[k] for k in ("x0", "x1", "y0", "y1")]
    if paths["done"].exists() and all(path.exists() for path in data_paths):
        return
    for path in data_paths:
        if path.exists():
            path.unlink()

    print(f"Building exact WebMercator footprint cache for z={max_zoom}...")
    x_moll = np.load(NPY_DIR / "x_moll_sorted.npy", mmap_mode="r")
    y_moll = np.load(NPY_DIR / "y_moll_sorted.npy", mmap_mode="r")
    n = len(x_moll)
    world_px = (1 << max_zoom) * TILE_SIZE

    x0_out = np.lib.format.open_memmap(paths["x0"], mode="w+", dtype=np.int32, shape=(n,))
    x1_out = np.lib.format.open_memmap(paths["x1"], mode="w+", dtype=np.int32, shape=(n,))
    y0_out = np.lib.format.open_memmap(paths["y0"], mode="w+", dtype=np.int32, shape=(n,))
    y1_out = np.lib.format.open_memmap(paths["y1"], mode="w+", dtype=np.int32, shape=(n,))

    t0 = time.time()
    for start in range(0, n, FOOTPRINT_CACHE_BATCH):
        end = min(start + FOOTPRINT_CACHE_BATCH, n)
        x0, x1, y0, y1, finite = source_cell_bounds(
            np.array(x_moll[start:end]),
            np.array(y_moll[start:end]),
            max_zoom,
        )
        x0 = np.where(finite, np.clip(x0, 0, world_px - 1), 1)
        x1 = np.where(finite, np.clip(x1, 0, world_px - 1), 0)
        y0 = np.where(finite, np.clip(y0, 0, world_px - 1), 1)
        y1 = np.where(finite, np.clip(y1, 0, world_px - 1), 0)
        x0_out[start:end] = x0
        x1_out[start:end] = x1
        y0_out[start:end] = y0
        y1_out[start:end] = y1
        if end % (FOOTPRINT_CACHE_BATCH * 10) == 0 or end == n:
            print(f"  cached {end:,}/{n:,} footprints | {time.time() - t0:.1f}s")

    del x0_out, x1_out, y0_out, y1_out
    paths["done"].write_text("ok\n")


def ensure_row_index_cache(max_zoom):
    paths = row_index_cache_paths(max_zoom)
    data_paths = [paths[k] for k in ("counts", "offsets", "indices")]
    if paths["done"].exists() and all(path.exists() for path in data_paths):
        return
    for path in data_paths:
        if path.exists():
            path.unlink()

    print(f"Building z={max_zoom} footprint row index...")
    fp_paths = footprint_cache_paths(max_zoom)
    fp_x0 = np.load(fp_paths["x0"], mmap_mode="r")
    fp_x1 = np.load(fp_paths["x1"], mmap_mode="r")
    fp_y0 = np.load(fp_paths["y0"], mmap_mode="r")
    fp_y1 = np.load(fp_paths["y1"], mmap_mode="r")
    n_rows = 1 << max_zoom
    n = len(fp_y0)

    counts = np.zeros(n_rows, dtype=np.int64)
    t0 = time.time()
    for start in range(0, n, ROW_INDEX_BATCH):
        end = min(start + ROW_INDEX_BATCH, n)
        x0 = np.array(fp_x0[start:end], dtype=np.int32)
        x1 = np.array(fp_x1[start:end], dtype=np.int32)
        ty0 = np.array(fp_y0[start:end] // TILE_SIZE, dtype=np.int32)
        ty1 = np.array(fp_y1[start:end] // TILE_SIZE, dtype=np.int32)
        valid = (x1 >= x0) & (ty1 >= ty0) & (ty0 < n_rows) & (ty1 >= 0)
        if valid.any():
            ty0 = np.clip(ty0[valid], 0, n_rows - 1)
            ty1 = np.clip(ty1[valid], 0, n_rows - 1)
            np.add.at(counts, ty0, 1)
            end_rows = ty1 + 1
            in_bounds = end_rows < n_rows
            np.add.at(counts, end_rows[in_bounds], -1)
        if end % (ROW_INDEX_BATCH * 10) == 0 or end == n:
            print(f"  counted {end:,}/{n:,} footprints | {time.time() - t0:.1f}s")

    counts = np.cumsum(counts)
    offsets = np.empty(n_rows + 1, dtype=np.int64)
    offsets[0] = 0
    offsets[1:] = np.cumsum(counts)
    total_refs = int(offsets[-1])

    indices = np.lib.format.open_memmap(
        paths["indices"], mode="w+", dtype=np.int32, shape=(total_refs,)
    )
    cursor = offsets[:-1].copy()

    for start in range(0, n, ROW_INDEX_BATCH):
        end = min(start + ROW_INDEX_BATCH, n)
        x0 = np.array(fp_x0[start:end], dtype=np.int32)
        x1 = np.array(fp_x1[start:end], dtype=np.int32)
        ty0 = np.array(fp_y0[start:end] // TILE_SIZE, dtype=np.int32)
        ty1 = np.array(fp_y1[start:end] // TILE_SIZE, dtype=np.int32)
        valid = (x1 >= x0) & (ty1 >= ty0) & (ty0 < n_rows) & (ty1 >= 0)
        if valid.any():
            cell_indices = np.arange(start, end, dtype=np.int32)[valid]
            ty0 = np.clip(ty0[valid], 0, n_rows - 1)
            ty1 = np.clip(ty1[valid], 0, n_rows - 1)
            for row in range(int(ty0.min()), int(ty1.max()) + 1):
                mask = (ty0 <= row) & (ty1 >= row)
                n_mask = int(mask.sum())
                if n_mask == 0:
                    continue
                pos = cursor[row]
                indices[pos:pos + n_mask] = cell_indices[mask]
                cursor[row] += n_mask
        if end % (ROW_INDEX_BATCH * 10) == 0 or end == n:
            print(f"  indexed {end:,}/{n:,} footprints | {time.time() - t0:.1f}s")

    np.save(paths["counts"], counts)
    np.save(paths["offsets"], offsets)
    del indices
    paths["done"].write_text("ok\n")


def ensure_tile(tiles, x, y):
    key = (int(x), int(y))
    if key not in tiles:
        tiles[key] = (
            np.full((TILE_SIZE, TILE_SIZE), np.inf, dtype=np.float32),
            np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.float32),
        )
    return tiles[key]


def add_contributions(tiles, tile_y, tile_x, px, py, dist, pop):
    if len(px) == 0:
        return
    order = np.argsort(tile_x, kind="stable")
    tx_sorted = tile_x[order]
    unique_tx, start_pos = np.unique(tx_sorted, return_index=True)
    end_pos = np.append(start_pos[1:], len(tx_sorted))

    for ux, sp, ep in zip(unique_tx, start_pos, end_pos):
        sel = order[sp:ep]
        grid_dist, grid_pop = ensure_tile(tiles, int(ux), tile_y)
        t_px = px[sel]
        t_py = py[sel]
        np.minimum.at(grid_dist, (t_py, t_px), dist[sel])
        np.add.at(grid_pop, (t_py, t_px), pop[sel])


def emit_footprint_contributions(tiles, tile_y, row_top, x0, x1, y0, y1, dist, pop):
    """Emit footprint pixels in chunks so each cell is scanned only once."""
    widths = x1 - x0 + 1
    heights = y1 - y0 + 1
    counts = widths.astype(np.int64) * heights.astype(np.int64)
    total = int(counts.sum())
    if total == 0:
        return

    start = 0
    while start < len(x0):
        end = start
        emitted = 0
        while end < len(x0) and emitted + int(counts[end]) <= CONTRIB_CHUNK:
            emitted += int(counts[end])
            end += 1
        if end == start:
            end += 1
            emitted = int(counts[start])

        c_counts = counts[start:end]
        c_widths = widths[start:end].astype(np.int64)
        c_x0 = x0[start:end].astype(np.int64)
        c_y0 = y0[start:end].astype(np.int64)

        local = np.arange(emitted, dtype=np.int64) - np.repeat(
            np.cumsum(c_counts) - c_counts,
            c_counts,
        )
        rep_widths = np.repeat(c_widths, c_counts)
        rep_x0 = np.repeat(c_x0, c_counts)
        rep_y0 = np.repeat(c_y0, c_counts)

        gx = rep_x0 + (local % rep_widths)
        gy = rep_y0 + (local // rep_widths)
        tx = (gx // TILE_SIZE).astype(np.int32)
        px = (gx - tx.astype(np.int64) * TILE_SIZE).astype(np.int32)
        py = (gy - row_top).astype(np.int32)

        rep_dist = np.repeat(dist[start:end], c_counts)
        rep_pop = np.repeat(pop[start:end], c_counts)
        add_contributions(tiles, tile_y, tx, px, py, rep_dist, rep_pop)
        start = end


def render_max_zoom_row(tile_y, row_indices, fp_x0, fp_x1, fp_y0, fp_y1,
                        pop, dist):
    """Render every populated tile at (max_zoom, *, tile_y).

    Returns a dict (x, y) -> (dist_grid, pop_grid). Empty if no cells fall
    in this row.
    """
    row_top = tile_y * TILE_SIZE
    row_bottom = row_top + TILE_SIZE - 1
    if len(row_indices) == 0:
        return {}

    tiles = {}

    for start in range(0, len(row_indices), CELL_BATCH):
        cell_idx = row_indices[start:start + CELL_BATCH]
        r_dist = np.array(dist[cell_idx])
        r_pop  = np.array(pop[cell_idx])
        x0 = np.array(fp_x0[cell_idx])
        x1 = np.array(fp_x1[cell_idx])
        y0 = np.clip(np.array(fp_y0[cell_idx]), row_top, row_bottom)
        y1 = np.clip(np.array(fp_y1[cell_idx]), row_top, row_bottom)

        intersects = (x1 >= x0) & (y1 >= y0)
        if not intersects.any():
            continue

        emit_footprint_contributions(
            tiles, tile_y, row_top,
            x0[intersects], x1[intersects],
            y0[intersects], y1[intersects],
            r_dist[intersects], r_pop[intersects],
        )

    return tiles


def encode_tile(z, x, y, grid_dist, grid_pop, pop_max):
    """Write the tile to OUT_DIR/z/x/y.png. Returns True if written."""
    nonzero = np.isfinite(grid_dist)
    if not nonzero.any():
        return False
    rgba = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)
    safe_dist = np.where(nonzero, grid_dist, 0.0)
    dist_norm = np.clip(safe_dist / MAX_RADIUS_KM, 0.0, 1.0)
    dist_code = np.ceil(dist_norm * DIST_CODE_MAX).astype(np.uint16)
    log_pop = np.log1p(grid_pop)
    a_chan = np.clip(log_pop / pop_max * 255.0, 0, 255).astype(np.uint8)
    rgba[..., 0] = np.where(nonzero, (dist_code >> 8).astype(np.uint8), 0)
    rgba[..., 1] = np.where(nonzero, (dist_code & 255).astype(np.uint8), 0)
    rgba[..., 3] = np.where(nonzero, a_chan, 0)

    out = OUT_DIR / str(z) / str(x) / f"{y}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(
        out,
        "PNG",
        optimize=False,
        compress_level=PNG_COMPRESS_LEVEL,
    )
    return True


def encode_tile_job(z, x, y, grid_dist, grid_pop, pop_max):
    return z, encode_tile(z, x, y, grid_dist, grid_pop, pop_max)


def drain_encodes(pending, stats, wait_all=False):
    if not pending:
        return pending
    if wait_all:
        done, pending = wait(pending)
    else:
        done = {fut for fut in pending if fut.done()}
        pending -= done
        if len(pending) >= MAX_PENDING_ENCODES:
            more_done, pending = wait(pending, return_when=FIRST_COMPLETED)
            done |= more_done

    for fut in done:
        z, written = fut.result()
        if written:
            stats[z] += 1
    return pending


def submit_encode(executor, pending, z, x, y, grid_dist, grid_pop, pop_max, stats):
    pending.add(executor.submit(
        encode_tile_job, z, x, y, grid_dist, grid_pop, pop_max
    ))
    return drain_encodes(pending, stats)


def propagate_up(z, x, y, child_dist, child_pop, staging):
    """Reduce child 256x256 grid to 128x128 and write into parent quadrant."""
    if z == 0:
        return
    H = TILE_SIZE // 2
    parent_z = z - 1
    parent_x = x // 2
    parent_y = y // 2
    key = (parent_x, parent_y)
    if key not in staging[parent_z]:
        staging[parent_z][key] = (
            np.full((TILE_SIZE, TILE_SIZE), np.inf, dtype=np.float32),
            np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.float32),
        )
    pd, pp = staging[parent_z][key]
    cd_red = child_dist.reshape(H, 2, H, 2).min(axis=(1, 3))
    cp_red = child_pop.reshape(H, 2, H, 2).sum(axis=(1, 3))
    qx = (x % 2) * H
    qy = (y % 2) * H
    pd[qy:qy + H, qx:qx + H] = cd_red
    pp[qy:qy + H, qx:qx + H] = cp_red


def flush_row(z, row, staging, pop_max, stats, executor, pending):
    """Encode all tiles at (z, *, row), propagate up, then recurse if odd row."""
    if z < 0:
        return pending
    keys = [k for k in staging[z] if k[1] == row]
    for k in keys:
        x, y = k
        dist_grid, pop_grid = staging[z].pop(k)
        pending = submit_encode(executor, pending, z, x, y,
                                dist_grid, pop_grid, pop_max, stats)
        if z > 0:
            propagate_up(z, x, y, dist_grid, pop_grid, staging)
    # Cascade upward only when this row is odd — otherwise the parent's
    # second child row hasn't arrived yet.
    if z > 0 and row % 2 == 1:
        pending = flush_row(z - 1, row // 2, staging, pop_max, stats,
                            executor, pending)
    return pending


# ─────────────────────────────────── main ───────────────────────────────────

def stream_pyramid(max_zoom, fp_x0, fp_x1, fp_y0, fp_y1, row_offsets,
                   row_indices, pop, dist, pop_max):
    n_tiles = 1 << max_zoom

    staging = [dict() for _ in range(max_zoom + 1)]  # (x,y) -> (dist, pop)
    stats = [0] * (max_zoom + 1)
    pending = set()

    t0 = time.time()
    print(f"Encoding PNGs with {ENCODE_WORKERS} worker threads.")
    with ThreadPoolExecutor(max_workers=ENCODE_WORKERS) as executor:
        for tile_y in range(n_tiles):
            start = int(row_offsets[tile_y])
            end = int(row_offsets[tile_y + 1])
            row_tiles = render_max_zoom_row(
                tile_y, row_indices[start:end],
                fp_x0, fp_x1, fp_y0, fp_y1, pop, dist,
            )
            for (x, y), (gd, gp) in row_tiles.items():
                pending = submit_encode(executor, pending, max_zoom, x, y,
                                        gd, gp, pop_max, stats)
                propagate_up(max_zoom, x, y, gd, gp, staging)

            if tile_y % 2 == 1:
                pending = flush_row(max_zoom - 1, tile_y // 2, staging, pop_max,
                                    stats, executor, pending)

            if (tile_y + 1) % 32 == 0 or tile_y == n_tiles - 1:
                pending = drain_encodes(pending, stats)
                dt = time.time() - t0
                staged_total = sum(len(s) for s in staging)
                in_flight = staged_total + len(pending)
                written_summary = " ".join(
                    f"z{i}={c:,}" for i, c in enumerate(stats) if c
                ) or "(none yet)"
                print(f"  row {tile_y+1:>5}/{n_tiles} | written: {written_summary} | "
                      f"in-flight: {in_flight} | {dt:.1f}s")

        # n_tiles = 2^max_zoom, so the final max-zoom row is odd and the normal
        # cascade should flush everything down to z=0. This catches stragglers.
        leftover = sum(len(s) for s in staging)
        if leftover:
            print(f"  flushing {leftover} leftover tile(s) (unexpected sanity flush)")
            for z in range(max_zoom - 1, -1, -1):
                for k in list(staging[z].keys()):
                    x, y = k
                    gd, gp = staging[z].pop(k)
                    pending = submit_encode(executor, pending, z, x, y,
                                            gd, gp, pop_max, stats)
                    if z > 0:
                        propagate_up(z, x, y, gd, gp, staging)

        pending = drain_encodes(pending, stats, wait_all=True)

    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("max_zoom", nargs="?", type=int, default=11)
    add_source_arg(parser)
    args = parser.parse_args()

    global DISTANCE_SOURCE, NPZ_PATH, OUT_DIR
    DISTANCE_SOURCE = args.source
    NPZ_PATH = distance_npz_path(DISTANCE_SOURCE)
    OUT_DIR = tiles_dir(DISTANCE_SOURCE)
    max_zoom = args.max_zoom

    if not NPZ_PATH.exists():
        raise FileNotFoundError(f"missing distance input: {NPZ_PATH}")

    ensure_npy_extracted()
    ensure_lat_sorted()
    ensure_distance_sorted()
    ensure_footprint_cache(max_zoom)
    ensure_row_index_cache(max_zoom)
    pop_max = ensure_pop_max()
    print(f"max log1p(pop) = {pop_max:.4f}")

    footprint_paths = footprint_cache_paths(max_zoom)
    row_index_paths = row_index_cache_paths(max_zoom)
    fp_x0 = np.load(footprint_paths["x0"], mmap_mode="r")
    fp_x1 = np.load(footprint_paths["x1"], mmap_mode="r")
    fp_y0 = np.load(footprint_paths["y0"], mmap_mode="r")
    fp_y1 = np.load(footprint_paths["y1"], mmap_mode="r")
    row_offsets = np.load(row_index_paths["offsets"], mmap_mode="r")
    row_indices = np.load(row_index_paths["indices"], mmap_mode="r")
    pop  = np.load(NPY_DIR / "pop_sorted.npy",  mmap_mode="r")
    dist = np.load(NPY_DIR / sorted_distance_cache_name(DISTANCE_SOURCE), mmap_mode="r")
    print(f"Indexed {len(pop):,} cells (mmap'd), source={DISTANCE_SOURCE}.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n=== Streaming pyramid z=0..{max_zoom} ===")
    t = time.time()
    stats = stream_pyramid(max_zoom, fp_x0, fp_x1, fp_y0, fp_y1,
                           row_offsets, row_indices, pop, dist, pop_max)
    print(f"\nAll done in {time.time() - t:.1f}s.")
    for z in range(max_zoom + 1):
        print(f"  z={z}: {stats[z]:,} tiles")
    print(f"Tiles under {OUT_DIR}")


if __name__ == "__main__":
    main()
