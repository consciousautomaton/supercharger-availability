"""
Build V2 population layer artifacts.

Current implemented target:
  0.25 degree global population grid for the 2030 GHS-POP epoch.

Inputs:
  data/npy/lons.npy
  data/npy/lats.npy
  data/npy/pop.npy

Outputs:
  v2/frontend/public/data/pop_025deg_world_2030.bin
  v2/frontend/public/data/pop_025deg_world_2030_meta.json

Why this first:
  The final V2 target is multi-resolution 1 km global + 100 m regional stream.
  That is a larger pipeline. This script creates a small, deterministic global
  grid immediately usable for WebGPU prototyping without reading the 6.6 GB TIFF
  directly or loading 370M cells into RAM.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
NPY_DIR = ROOT / "data/npy"
OUT_DIR = ROOT / "v2/frontend/public/data"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epoch", type=int, default=2030, help="Population epoch. Currently only 2030 local arrays are supported.")
    ap.add_argument("--grid-deg", type=float, default=0.25, help="Output lat/lon grid cell size in degrees.")
    ap.add_argument("--chunk-size", type=int, default=5_000_000, help="Input cells per processing chunk.")
    return ap.parse_args()


def require_file(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def output_paths(grid_deg: float, epoch: int) -> tuple[Path, Path]:
    label = f"{str(grid_deg).replace('.', '')}deg"
    return (
        OUT_DIR / f"pop_{label}_world_{epoch}.bin",
        OUT_DIR / f"pop_{label}_world_{epoch}_meta.json",
    )


def main() -> None:
    args = parse_args()
    if args.epoch != 2030:
        raise SystemExit("Only the existing local 2030 populated-cell arrays are supported right now.")
    if args.grid_deg <= 0 or 360 / args.grid_deg > 100_000:
        raise SystemExit("--grid-deg is outside the supported range")

    lons_path = require_file(NPY_DIR / "lons.npy")
    lats_path = require_file(NPY_DIR / "lats.npy")
    pop_path = require_file(NPY_DIR / "pop.npy")

    lons = np.load(lons_path, mmap_mode="r")
    lats = np.load(lats_path, mmap_mode="r")
    pop = np.load(pop_path, mmap_mode="r")
    if not (len(lons) == len(lats) == len(pop)):
        raise RuntimeError("lons/lats/pop arrays have different lengths")

    width = int(round(360.0 / args.grid_deg))
    height = int(round(180.0 / args.grid_deg))
    if not math.isclose(width * args.grid_deg, 360.0) or not math.isclose(height * args.grid_deg, 180.0):
        raise SystemExit("--grid-deg must divide 360 and 180 cleanly")

    out_grid = np.zeros(width * height, dtype=np.float64)
    total_pop = 0.0
    skipped = 0
    start_time = time.time()

    print(
        f"[build] epoch={args.epoch} grid={args.grid_deg} deg -> {width}x{height} ({width * height:,} cells)",
        file=sys.stderr,
    )
    print(f"[build] streaming {len(pop):,} populated source cells in chunks of {args.chunk_size:,}", file=sys.stderr)

    n = len(pop)
    for start in range(0, n, args.chunk_size):
        end = min(start + args.chunk_size, n)
        lon_chunk = np.asarray(lons[start:end], dtype=np.float64)
        lat_chunk = np.asarray(lats[start:end], dtype=np.float64)
        pop_chunk = np.asarray(pop[start:end], dtype=np.float64)

        valid = (
            np.isfinite(lon_chunk)
            & np.isfinite(lat_chunk)
            & np.isfinite(pop_chunk)
            & (pop_chunk > 0)
            & (lon_chunk >= -180.0)
            & (lon_chunk <= 180.0)
            & (lat_chunk >= -90.0)
            & (lat_chunk <= 90.0)
        )
        skipped += int((~valid).sum())
        if not valid.any():
            continue

        x = np.floor((lon_chunk[valid] + 180.0) / args.grid_deg).astype(np.int64)
        y = np.floor((90.0 - lat_chunk[valid]) / args.grid_deg).astype(np.int64)
        x = np.clip(x, 0, width - 1)
        y = np.clip(y, 0, height - 1)
        flat = y * width + x
        weights = pop_chunk[valid]
        out_grid += np.bincount(flat, weights=weights, minlength=width * height)
        total_pop += float(weights.sum())

        if end == n or end % 25_000_000 < args.chunk_size:
            elapsed = time.time() - start_time
            nonzero = int(np.count_nonzero(out_grid))
            print(
                f"  cells {end:,}/{n:,} | pop={total_pop:,.0f} | nonzero output={nonzero:,} | skipped={skipped:,} | {elapsed:.1f}s",
                file=sys.stderr,
            )

    bin_path, meta_path = output_paths(args.grid_deg, args.epoch)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_float32 = out_grid.astype(np.float32)
    out_float32.tofile(bin_path)

    meta: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "GHS-POP 2030 populated-cell extract from data/npy",
        "epoch": args.epoch,
        "grid": "equirectangular_lonlat",
        "cell_degrees": args.grid_deg,
        "width": width,
        "height": height,
        "dtype": "float32",
        "byte_order": "little_endian",
        "bounds": {"west": -180, "south": -90, "east": 180, "north": 90},
        "layout": "row_major_north_to_south",
        "population_sum": total_pop,
        "nonzero_cells": int(np.count_nonzero(out_float32)),
        "source_populated_cells": int(n),
        "skipped_source_cells": skipped,
        "notes": [
            "This is a coarse global prototype grid, not the final 1 km / 100 m V2 population pipeline.",
            "Values are summed population per output lon/lat cell.",
        ],
    }
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, sort_keys=True)

    elapsed = time.time() - start_time
    print(f"[done] wrote {bin_path}", file=sys.stderr)
    print(f"  metadata          : {meta_path}", file=sys.stderr)
    print(f"  population sum    : {total_pop:,.0f}", file=sys.stderr)
    print(f"  nonzero cells     : {meta['nonzero_cells']:,} / {width * height:,}", file=sys.stderr)
    print(f"  skipped cells     : {skipped:,}", file=sys.stderr)
    print(f"  output size       : {bin_path.stat().st_size / 1024 / 1024:.1f} MiB", file=sys.stderr)
    print(f"  elapsed           : {elapsed:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()

