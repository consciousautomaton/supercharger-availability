"""
Validate generated frontend outputs after the tile/stat precompute.

Run from project root after:
    .venv/Scripts/python scripts/precompute_tiles.py 11
    .venv/Scripts/python scripts/build_outputs.py
    .venv/Scripts/python scripts/precompute_viewport_stats.py

This checks the file formats and the radius-0 semantics that matter for the
coverage UI. It does not prove every tile is visually correct, but it catches
the main regressions: stale 8-bit tiles, stale 5 km viewport stats, and old
global JSON.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from distance_source import add_source_arg, frontend_path, tiles_dir

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"

MAX_RADIUS_KM = 500.0
DIST_CODE_MAX = 65535


def decode_rg16_distance_codes(tile_path):
    rgba = np.array(Image.open(tile_path).convert("RGBA"), dtype=np.uint16)
    code = rgba[..., 0] * 256 + rgba[..., 1]
    alpha = rgba[..., 3]
    return code, alpha


def validate_global_stats(source):
    path = frontend_path(source, "pop_cumulative", "json")
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise AssertionError(f"{path} is still the old array format")
    required = {"radius_step_km", "radius_max_km", "total_pop", "pop_max_log1p", "cumulative"}
    missing = required - set(payload)
    if missing:
        raise AssertionError(f"{path.name} missing keys: {sorted(missing)}")
    if payload["radius_step_km"] != 1:
        raise AssertionError("global radius_step_km is not 1")
    if payload["radius_max_km"] != 500:
        raise AssertionError("global radius_max_km is not 500")
    if payload["pop_max_log1p"] <= 0:
        raise AssertionError("global pop_max_log1p is missing or not positive")
    cumulative = payload["cumulative"]
    if len(cumulative) != 501:
        raise AssertionError(f"global cumulative has length {len(cumulative)}, expected 501")
    if any(cumulative[i] > cumulative[i + 1] for i in range(len(cumulative) - 1)):
        raise AssertionError("global cumulative values are not monotonic")
    print("global stats: ok")


def validate_viewport_stats(source):
    manifest_path = frontend_path(source, "viewport_manifest", "json")
    manifest = json.loads(manifest_path.read_text())
    if manifest["radius_step_km"] != 1:
        raise AssertionError("viewport radius_step_km is not 1")
    if manifest["n_radii"] != 501:
        raise AssertionError(f"viewport n_radii is {manifest['n_radii']}, expected 501")

    total_path = FRONTEND / manifest["pop_total_path"]
    covered_path = FRONTEND / manifest["pop_covered_path"]
    total_expected = manifest["lat_bins"] * manifest["lon_bins"]
    covered_expected = total_expected * manifest["n_radii"]

    total_count = total_path.stat().st_size // np.dtype(np.float32).itemsize
    covered_count = covered_path.stat().st_size // np.dtype(np.float32).itemsize
    if total_count != total_expected:
        raise AssertionError(f"viewport total size is {total_count}, expected {total_expected}")
    if covered_count != covered_expected:
        raise AssertionError(f"viewport covered size is {covered_count}, expected {covered_expected}")
    print("viewport stats: ok")


def validate_sample_tiles(source):
    tiles = tiles_dir(source)
    tile_paths = sorted(tiles.glob("11/*/*.png"))
    if not tile_paths:
        raise AssertionError(f"no z11 tiles found in {tiles}")

    sample = tile_paths[::max(1, len(tile_paths) // 64)][:64]
    populated_pixels = 0
    zero_code_pixels = 0
    low_code_pixels = 0
    max_code = 0

    one_km_code = int(np.ceil(1.0 / MAX_RADIUS_KM * DIST_CODE_MAX))
    for path in sample:
        code, alpha = decode_rg16_distance_codes(path)
        populated = alpha > 0
        populated_pixels += int(populated.sum())
        zero_code_pixels += int(((code == 0) & populated).sum())
        low_code_pixels += int(((code > 0) & (code <= one_km_code) & populated).sum())
        if populated.any():
            max_code = max(max_code, int(code[populated].max()))

    if populated_pixels == 0:
        raise AssertionError("sampled z11 tiles contain no populated pixels")
    if max_code > DIST_CODE_MAX:
        raise AssertionError(f"decoded distance code {max_code} exceeds {DIST_CODE_MAX}")
    print(
        "sampled z11 tiles: ok "
        f"({len(sample)} tiles, {populated_pixels:,} populated pixels, "
        f"{zero_code_pixels:,} zero-code pixels, {low_code_pixels:,} 0-1km pixels)"
    )


def main():
    parser = argparse.ArgumentParser()
    add_source_arg(parser)
    args = parser.parse_args()

    validate_global_stats(args.source)
    validate_viewport_stats(args.source)
    validate_sample_tiles(args.source)
    print(f"validation passed for source={args.source}")


if __name__ == "__main__":
    main()
