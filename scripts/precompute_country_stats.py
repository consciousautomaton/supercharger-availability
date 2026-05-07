"""
Precompute exact country-level coverage stats for the static frontend.

Outputs under frontend/:
    countries.geojson      — Natural Earth Admin 0 boundaries with compact props
    country_stats.json     — per-country total pop, covered pop by radius, chargers

This script assigns population cells and chargers to countries by point center.
It rasterizes Natural Earth country polygons into bounded temporary windows of
the native GHS-POP Mollweide grid, then samples those windows using each
populated cell's stored Mollweide center coordinate. This avoids a full-world
country raster cache, which would be too large for normal laptops.

Run from project root after pixel_distances.npz exists:
    .venv/Scripts/python scripts/precompute_country_stats.py
"""

import argparse
import os
import json
import subprocess
import csv
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.windows import Window
from rasterio.features import rasterize
from distance_source import add_source_arg, distance_npz_path, frontend_path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
FRONTEND_DIR = ROOT / "frontend"

GHS_TIF = DATA_DIR / "GHS_POP_E2030_GLOBE_R2023A_54009_100_V1_0.tif"
PIXELS_NPZ = DATA_DIR / "populated_pixels.npz"
DISTANCES_NPZ = DATA_DIR / "pixel_distances.npz"
CHARGERS_NPZ = DATA_DIR / "chargers.npz"
CHARGER_OVERRIDES_CSV = DATA_DIR / "country_charger_overrides.csv"

BOUNDARY_SCALE = os.environ.get("COUNTRY_BOUNDARY_SCALE", "10m")
NATURAL_EARTH_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
    f"geojson/ne_{BOUNDARY_SCALE}_admin_0_countries.geojson"
)
BOUNDARIES_PATH = DATA_DIR / f"ne_{BOUNDARY_SCALE}_admin_0_countries.geojson"

COUNTRIES_OUT = FRONTEND_DIR / "countries.geojson"
STATS_OUT = FRONTEND_DIR / "country_stats.json"
UNASSIGNED_CHARGERS_GEOJSON = FRONTEND_DIR / "unassigned_chargers.geojson"
UNASSIGNED_CHARGERS_CSV = FRONTEND_DIR / "unassigned_chargers.csv"

RADIUS_STEP_KM = 1
RADIUS_MAX_KM = 500
N_RADII = RADIUS_MAX_KM // RADIUS_STEP_KM + 1
CHUNK = 1_000_000
MAX_WINDOW_PIXELS = 20_000_000


def download_boundaries():
    if BOUNDARIES_PATH.exists():
        return
    print(f"Downloading Natural Earth countries -> {BOUNDARIES_PATH}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["curl", "-L", "--fail", "-o", str(BOUNDARIES_PATH), NATURAL_EARTH_URL],
        check=True,
    )


def country_name(props):
    return (
        props.get("ADMIN")
        or props.get("NAME_LONG")
        or props.get("NAME")
        or props.get("SOVEREIGNT")
        or "Unknown"
    )


def country_iso(props, fallback_id):
    for key in ("ISO_A3", "ADM0_A3", "SOV_A3"):
        value = props.get(key)
        if value and value != "-99":
            return value
    return f"X{fallback_id:03d}"


def transform_ring(ring, transformer):
    arr = np.asarray(ring, dtype=np.float64)
    if arr.size == 0:
        return []
    x, y = transformer.transform(arr[:, 0], arr[:, 1])
    return np.column_stack([x, y]).tolist()


def transform_geometry(geometry, transformer):
    gtype = geometry["type"]
    coords = geometry["coordinates"]
    if gtype == "Polygon":
        return {
            "type": "Polygon",
            "coordinates": [transform_ring(ring, transformer) for ring in coords],
        }
    if gtype == "MultiPolygon":
        return {
            "type": "MultiPolygon",
            "coordinates": [
                [transform_ring(ring, transformer) for ring in polygon]
                for polygon in coords
            ],
        }
    raise ValueError(f"Unsupported geometry type: {gtype}")


def geometry_bbox(geometry):
    xs = []
    ys = []

    def walk(obj):
        if isinstance(obj, list):
            if obj and isinstance(obj[0], (int, float)):
                xs.append(float(obj[0]))
                ys.append(float(obj[1]))
            else:
                for item in obj:
                    walk(item)

    walk(geometry["coordinates"])
    return [min(xs), min(ys), max(xs), max(ys)]


def load_country_features():
    download_boundaries()
    data = json.loads(BOUNDARIES_PATH.read_text(encoding="utf-8"))
    features = []
    for idx, feature in enumerate(data["features"], start=1):
        props = feature.get("properties", {})
        name = country_name(props)
        iso = country_iso(props, idx)
        features.append({
            "id": idx,
            "iso_a3": iso,
            "name": name,
            "geometry": feature["geometry"],
            "bbox": geometry_bbox(feature["geometry"]),
        })
    features.sort(key=lambda f: f["name"])
    for idx, feature in enumerate(features, start=1):
        feature["id"] = idx
    return features


def write_frontend_countries(features):
    out = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": f["id"],
                "bbox": f["bbox"],
                "properties": {
                    "id": f["id"],
                    "iso_a3": f["iso_a3"],
                    "name": f["name"],
                },
                "geometry": f["geometry"],
            }
            for f in features
        ],
    }
    FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
    COUNTRIES_OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {COUNTRIES_OUT} ({COUNTRIES_OUT.stat().st_size:,} bytes)")


def prepare_country_shapes(features):
    with rasterio.open(GHS_TIF) as ds:
        height = ds.height
        width = ds.width
        transform = ds.transform
        target_crs = ds.crs

    to_moll = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
    shapes = [
        (transform_geometry(f["geometry"], to_moll), int(f["id"]))
        for f in features
    ]
    print(f"Prepared {len(shapes)} country polygons for windowed rasterization")
    return height, width, transform, shapes


def xy_to_row_col(x, y, transform, height, width):
    col = np.floor((x - transform.c) / transform.a).astype(np.int64)
    row = np.floor((y - transform.f) / transform.e).astype(np.int64)
    valid = (row >= 0) & (row < height) & (col >= 0) & (col < width)
    return row, col, valid


def rasterize_sampled_country_ids(rows, cols, valid_xy, shapes, transform):
    ids = np.zeros(len(rows), dtype=np.int64)
    pending = [np.flatnonzero(valid_xy)]

    while pending:
        point_idx = pending.pop()
        if len(point_idx) == 0:
            continue

        r = rows[point_idx]
        c = cols[point_idx]
        row_min = int(r.min())
        row_max = int(r.max())
        col_min = int(c.min())
        col_max = int(c.max())
        out_h = row_max - row_min + 1
        out_w = col_max - col_min + 1
        window_pixels = out_h * out_w

        if window_pixels > MAX_WINDOW_PIXELS and len(point_idx) > 1:
            if out_h >= out_w:
                split_value = (row_min + row_max) // 2
                left = point_idx[r <= split_value]
                right = point_idx[r > split_value]
            else:
                split_value = (col_min + col_max) // 2
                left = point_idx[c <= split_value]
                right = point_idx[c > split_value]

            if len(left) == 0 or len(right) == 0:
                midpoint = len(point_idx) // 2
                left = point_idx[:midpoint]
                right = point_idx[midpoint:]
            pending.append(right)
            pending.append(left)
            continue

        window = Window(col_min, row_min, out_w, out_h)
        window_transform = rasterio.windows.transform(window, transform)
        country_window = rasterize(
            shapes,
            out_shape=(out_h, out_w),
            transform=window_transform,
            fill=0,
            dtype=np.uint16,
            all_touched=False,
        )
        ids[point_idx] = country_window[r - row_min, c - col_min]

    return ids


def aggregate_population(features, height, width, transform, shapes):
    pixels = np.load(PIXELS_NPZ)
    distances = np.load(DISTANCES_NPZ)
    x_moll = pixels["x_moll"]
    y_moll = pixels["y_moll"]
    pop = distances["pop"]
    dist = distances["min_dist_km"]
    n = len(pop)
    n_countries = len(features)

    total_pop = np.zeros(n_countries + 1, dtype=np.float64)
    pop_per_radius = np.zeros((n_countries + 1, N_RADII), dtype=np.float64)
    dataset_total = 0.0

    print(f"Aggregating {n:,} population cells by country")
    for start in range(0, n, CHUNK):
        end = min(start + CHUNK, n)
        x = np.asarray(x_moll[start:end], dtype=np.float64)
        y = np.asarray(y_moll[start:end], dtype=np.float64)
        pop_chunk = np.asarray(pop[start:end], dtype=np.float64)
        dataset_total += float(pop_chunk.sum())
        row, col, valid_xy = xy_to_row_col(x, y, transform, height, width)
        ids = rasterize_sampled_country_ids(row, col, valid_xy, shapes, transform)
        valid = ids > 0
        if valid.any():
            pop_c = pop_chunk[valid]
            ids_c = ids[valid]
            total_pop += np.bincount(ids_c, weights=pop_c, minlength=n_countries + 1)

            dist_c = np.asarray(dist[start:end])[valid]
            bucket = np.ceil(np.maximum(dist_c, 0.0) / RADIUS_STEP_KM).astype(np.int64)
            in_range = bucket < N_RADII
            flat = ids_c[in_range] * N_RADII + bucket[in_range]
            hist = np.bincount(
                flat,
                weights=pop_c[in_range],
                minlength=(n_countries + 1) * N_RADII,
            )
            pop_per_radius += hist.reshape(n_countries + 1, N_RADII)
        print(f"  cells {start:,}..{end:,} done")

    covered = np.cumsum(pop_per_radius, axis=1)
    assigned = float(total_pop.sum())
    print(f"Assigned country population: {assigned:,.0f} / {dataset_total:,.0f}")
    return total_pop, covered


def write_unassigned_chargers(lats, lons, ids):
    unassigned = np.flatnonzero(ids == 0)
    features = []
    csv_lines = ["index,latitude,longitude\n"]

    for idx in unassigned:
        lat = float(lats[idx])
        lon = float(lons[idx])
        features.append({
            "type": "Feature",
            "properties": {"index": int(idx)},
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        })
        csv_lines.append(f"{int(idx)},{lat:.7f},{lon:.7f}\n")

    geojson = {"type": "FeatureCollection", "features": features}
    UNASSIGNED_CHARGERS_GEOJSON.write_text(
        json.dumps(geojson, separators=(",", ":")),
        encoding="utf-8",
    )
    UNASSIGNED_CHARGERS_CSV.write_text("".join(csv_lines), encoding="utf-8")
    print(
        f"Wrote {len(unassigned):,} unassigned chargers to "
        f"{UNASSIGNED_CHARGERS_GEOJSON} and {UNASSIGNED_CHARGERS_CSV}"
    )


def load_charger_overrides(features):
    iso_to_id = {f["iso_a3"]: int(f["id"]) for f in features}
    overrides = {}
    if not CHARGER_OVERRIDES_CSV.exists():
        return overrides

    with CHARGER_OVERRIDES_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            index_text = (row.get("charger_index") or "").strip()
            iso = (row.get("iso_a3") or "").strip().upper()
            if not index_text or not iso:
                continue
            if iso not in iso_to_id:
                print(f"  ignoring charger override with unknown ISO_A3: {iso}")
                continue
            overrides[int(index_text)] = iso_to_id[iso]

    print(f"Loaded {len(overrides):,} charger overrides from {CHARGER_OVERRIDES_CSV}")
    return overrides


def aggregate_chargers(features, height, width, transform, shapes):
    chargers = np.load(CHARGERS_NPZ)
    lats = chargers["lats"]
    lons = chargers["lons"]
    n_countries = len(features)

    with rasterio.open(GHS_TIF) as ds:
        target_crs = ds.crs
    to_moll = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
    x, y = to_moll.transform(lons, lats)
    row, col, valid = xy_to_row_col(
        np.asarray(x), np.asarray(y), transform, height, width
    )

    ids = rasterize_sampled_country_ids(row, col, valid, shapes, transform)
    overrides = load_charger_overrides(features)
    applied = 0
    for index, country_id in overrides.items():
        if 0 <= index < len(ids):
            ids[index] = country_id
            applied += 1
    if overrides:
        print(f"Applied {applied:,} charger overrides")

    counts = np.bincount(ids[ids > 0], minlength=n_countries + 1)
    print(f"Assigned chargers: {int(counts.sum()):,} / {len(lats):,}")
    write_unassigned_chargers(lats, lons, ids)
    return counts


def write_stats(features, total_pop, covered, charger_counts, source):
    countries = []
    for f in features:
        cid = int(f["id"])
        countries.append({
            "id": cid,
            "iso_a3": f["iso_a3"],
            "name": f["name"],
            "bbox": f["bbox"],
            "total_pop": float(total_pop[cid]),
            "charger_count": int(charger_counts[cid]),
            "covered": covered[cid].astype(float).tolist(),
        })
    payload = {
        "radius_step_km": RADIUS_STEP_KM,
        "radius_max_km": RADIUS_MAX_KM,
        "n_radii": N_RADII,
        "assignment": "country polygon contains source cell center",
        "boundary_source": f"Natural Earth Admin 0 Countries {BOUNDARY_SCALE}",
        "distance_source": source,
        "countries": countries,
    }
    FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
    STATS_OUT.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {STATS_OUT} ({STATS_OUT.stat().st_size:,} bytes)")


def main():
    parser = argparse.ArgumentParser()
    add_source_arg(parser)
    args = parser.parse_args()

    global DISTANCES_NPZ, STATS_OUT
    DISTANCES_NPZ = distance_npz_path(args.source)
    STATS_OUT = frontend_path(args.source, "country_stats", "json")
    if not DISTANCES_NPZ.exists():
        raise FileNotFoundError(f"missing distance input: {DISTANCES_NPZ}")
    print(f"Using distance source={args.source}: {DISTANCES_NPZ}")

    features = load_country_features()
    write_frontend_countries(features)
    height, width, transform, shapes = prepare_country_shapes(features)
    total_pop, covered = aggregate_population(features, height, width, transform, shapes)
    charger_counts = aggregate_chargers(features, height, width, transform, shapes)
    write_stats(features, total_pop, covered, charger_counts, args.source)


if __name__ == "__main__":
    main()
