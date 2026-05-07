"""
Sample coverage tiles and compare PNG/WebP compression options.

This script is safe for the real tile pyramid: it reads frontend/tiles/*.png,
encodes sampled tiles in memory, verifies exact round-trips where relevant,
and writes only a small JSON report.

Run from project root:
    .venv/Scripts/python scripts/experiment_tile_compression.py

Useful options:
    --sample-per-zoom 20
    --include-lossy
    --report data/tile_compression_experiment.json
"""

import argparse
import io
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image, features

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TILES = ROOT / "frontend" / "tiles"
DEFAULT_REPORT = ROOT / "data" / "tile_compression_experiment.json"


def evenly_sample(items, n):
    if len(items) <= n:
        return items
    if n <= 1:
        return [items[len(items) // 2]]
    step = (len(items) - 1) / (n - 1)
    return [items[round(i * step)] for i in range(n)]


def encode_png(img, compress_level, optimize):
    out = io.BytesIO()
    img.save(out, "PNG", compress_level=compress_level, optimize=optimize)
    return out.getvalue()


def encode_webp_lossless(img, method):
    out = io.BytesIO()
    img.save(out, "WEBP", lossless=True, quality=100, method=method, exact=True)
    return out.getvalue()


def encode_webp_lossy(img, quality, method):
    out = io.BytesIO()
    img.save(out, "WEBP", lossless=False, quality=quality, method=method)
    return out.getvalue()


def decode_bytes(data):
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))


def empty_stats():
    return {
        "count": 0,
        "original": 0,
        "png_l9_opt": 0,
        "webp_lossless": 0,
        "webp_q90": 0,
        "webp_q80": 0,
        "webp_lossless_exact": True,
        "webp_q90_exact": True,
        "webp_q80_exact": True,
        "webp_q90_max_abs_diff": 0,
        "webp_q80_max_abs_diff": 0,
    }


def add_size(stats, key, size):
    stats[key] += int(size)


def pct(new_size, old_size):
    if old_size == 0:
        return None
    return round(100.0 * new_size / old_size, 1)


def summarize(stats):
    original = stats["original"]
    return {
        "count": stats["count"],
        "original_bytes": original,
        "png_l9_opt_bytes": stats["png_l9_opt"],
        "png_l9_opt_pct_of_original": pct(stats["png_l9_opt"], original),
        "webp_lossless_bytes": stats["webp_lossless"],
        "webp_lossless_pct_of_original": pct(stats["webp_lossless"], original),
        "webp_lossless_exact": stats["webp_lossless_exact"],
        "webp_q90_bytes": stats["webp_q90"],
        "webp_q90_pct_of_original": pct(stats["webp_q90"], original),
        "webp_q90_exact": stats["webp_q90_exact"],
        "webp_q90_max_abs_diff": stats["webp_q90_max_abs_diff"],
        "webp_q80_bytes": stats["webp_q80"],
        "webp_q80_pct_of_original": pct(stats["webp_q80"], original),
        "webp_q80_exact": stats["webp_q80_exact"],
        "webp_q80_max_abs_diff": stats["webp_q80_max_abs_diff"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tiles", type=Path, default=DEFAULT_TILES)
    parser.add_argument("--sample-per-zoom", type=int, default=8)
    parser.add_argument("--webp-method", type=int, default=4)
    parser.add_argument("--include-lossy", action="store_true")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    if not args.tiles.exists():
        raise SystemExit(f"Tile directory not found: {args.tiles}")

    webp_available = features.check("webp")
    if not webp_available:
        raise SystemExit("This Pillow build does not support WebP.")

    by_zoom = {}
    for zoom_dir in sorted(args.tiles.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else -1):
        if not zoom_dir.is_dir() or not zoom_dir.name.isdigit():
            continue
        tiles = sorted(zoom_dir.glob("*/*.png"))
        if tiles:
            by_zoom[int(zoom_dir.name)] = evenly_sample(tiles, args.sample_per_zoom)

    if not by_zoom:
        raise SystemExit(f"No PNG tiles found under {args.tiles}")

    overall = empty_stats()
    zoom_results = {}
    t0 = time.time()

    for zoom, paths in by_zoom.items():
        stats = empty_stats()
        for path in paths:
            original_bytes = path.read_bytes()
            img = Image.open(io.BytesIO(original_bytes)).convert("RGBA")
            original_rgba = np.asarray(img)

            png_l9 = encode_png(img, compress_level=9, optimize=True)
            webp_lossless = encode_webp_lossless(img, method=args.webp_method)

            lossless_rgba = decode_bytes(webp_lossless)

            lossless_exact = bool(np.array_equal(original_rgba, lossless_rgba))

            stats["count"] += 1
            add_size(stats, "original", len(original_bytes))
            add_size(stats, "png_l9_opt", len(png_l9))
            add_size(stats, "webp_lossless", len(webp_lossless))
            stats["webp_lossless_exact"] = stats["webp_lossless_exact"] and lossless_exact

            if args.include_lossy:
                webp_q90 = encode_webp_lossy(img, quality=90, method=args.webp_method)
                webp_q80 = encode_webp_lossy(img, quality=80, method=args.webp_method)
                q90_rgba = decode_bytes(webp_q90)
                q80_rgba = decode_bytes(webp_q80)
                q90_diff = int(np.max(np.abs(original_rgba.astype(np.int16) - q90_rgba.astype(np.int16))))
                q80_diff = int(np.max(np.abs(original_rgba.astype(np.int16) - q80_rgba.astype(np.int16))))

                add_size(stats, "webp_q90", len(webp_q90))
                add_size(stats, "webp_q80", len(webp_q80))
                stats["webp_q90_exact"] = False
                stats["webp_q80_exact"] = False
                stats["webp_q90_max_abs_diff"] = max(stats["webp_q90_max_abs_diff"], q90_diff)
                stats["webp_q80_max_abs_diff"] = max(stats["webp_q80_max_abs_diff"], q80_diff)

        zoom_results[str(zoom)] = summarize(stats)
        print(f"z{zoom}: sampled {stats['count']} tiles")
        for key in overall:
            if key in ("webp_lossless_exact", "webp_q90_exact", "webp_q80_exact"):
                overall[key] = overall[key] and stats[key]
            elif key.endswith("_max_abs_diff"):
                overall[key] = max(overall[key], stats[key])
            else:
                overall[key] += stats[key]

    report = {
        "tiles_dir": str(args.tiles),
        "sample_per_zoom": args.sample_per_zoom,
        "webp_method": args.webp_method,
        "include_lossy": args.include_lossy,
        "elapsed_seconds": round(time.time() - t0, 1),
        "overall": summarize(overall),
        "by_zoom": zoom_results,
        "notes": {
            "png_l9_opt": "Exact PNG recompression; safe if savings justify rebuild time.",
            "webp_lossless": "Safe only if exact is true.",
            "webp_q90_q80": "Lossy; included to show size potential, not valid for RG16 data tiles.",
        },
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2))

    o = report["overall"]
    print(f"Sampled {o['count']:,} tiles in {report['elapsed_seconds']}s")
    print(f"Original:       {o['original_bytes'] / 1024 / 1024:.1f} MB")
    print(f"PNG l9 opt:     {o['png_l9_opt_bytes'] / 1024 / 1024:.1f} MB ({o['png_l9_opt_pct_of_original']}%)")
    print(f"WebP lossless:  {o['webp_lossless_bytes'] / 1024 / 1024:.1f} MB ({o['webp_lossless_pct_of_original']}%), exact={o['webp_lossless_exact']}")
    if args.include_lossy:
        print(f"WebP q90:       {o['webp_q90_bytes'] / 1024 / 1024:.1f} MB ({o['webp_q90_pct_of_original']}%), max_abs_diff={o['webp_q90_max_abs_diff']}")
        print(f"WebP q80:       {o['webp_q80_bytes'] / 1024 / 1024:.1f} MB ({o['webp_q80_pct_of_original']}%), max_abs_diff={o['webp_q80_max_abs_diff']}")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
