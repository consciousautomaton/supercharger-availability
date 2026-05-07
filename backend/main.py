import io
import json
import math
import numpy as np
from PIL import Image
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

print("Loading pixel_distances.npz...")
d        = np.load("data/pixel_distances.npz")
LONS     = d["lons"].copy()
LATS     = d["lats"].copy()
POP      = d["pop"].copy()
MIN_DIST = d["min_dist_km"].copy()
print(f"Loaded {len(LONS):,} pixels.")

print("Building lat index (int32)...")
_order      = np.argsort(LATS, kind="stable").astype(np.int32)
SORTED_LATS = LATS[_order]
LAT_ORDER   = _order
del _order
print("Ready.")

with open("data/pop_cumulative.json") as f:
    POP_CUMULATIVE = json.load(f)
TOTAL_POP = 8_480_668_160.0

TILE_SIZE = 256


def lat_band(lat_min, lat_max):
    lo = int(np.searchsorted(SORTED_LATS, lat_min, side="left"))
    hi = int(np.searchsorted(SORTED_LATS, lat_max, side="right"))
    return LAT_ORDER[lo:hi]


def lon_wrap(lon_min: float, lon_max: float):
    lon_min = max(lon_min, -180.0)
    lon_max = min(lon_max,  180.0)
    if lon_min > lon_max:
        return [(-180.0, lon_max), (lon_min, 180.0)]
    return [(lon_min, lon_max)]


def tile_bounds(z: int, x: int, y: int):
    n = 2 ** z
    lon_min = x       / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0
    lat_max = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 *  y      / n))))
    lat_min = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return lat_min, lat_max, lon_min, lon_max


def lonlat_to_tile_px(lons, lats, z, x, y):
    n = 2 ** z
    px = (lons + 180.0) / 360.0 * n * TILE_SIZE - x * TILE_SIZE
    sinlat = np.sin(np.radians(lats))
    py = (0.5 - np.log((1 + sinlat) / (1 - sinlat)) / (4 * np.pi)) * n * TILE_SIZE - y * TILE_SIZE
    return px.astype(np.int32), py.astype(np.int32)


@app.get("/coverage")
def coverage(radius: int = Query(ge=0, le=500)):
    covered = POP_CUMULATIVE[radius]
    return {"radius_km": radius, "covered": covered, "total": TOTAL_POP, "fraction": covered / TOTAL_POP}


@app.get("/viewport_stats")
def viewport_stats(
    lat_min: float = Query(...),
    lat_max: float = Query(...),
    lon_min: float = Query(...),
    lon_max: float = Query(...),
    radius:  float = Query(..., ge=0, le=2000),
):
    lon_segments = lon_wrap(lon_min, lon_max)
    band = lat_band(lat_min, lat_max)
    b_lons = LONS[band]; b_dist = MIN_DIST[band]; b_pop = POP[band]

    lon_mask = np.zeros(len(b_lons), dtype=bool)
    for lo, hi in lon_segments:
        lon_mask |= (b_lons >= lo) & (b_lons <= hi)

    lit_mask    = lon_mask & (b_dist <= radius)
    pop_total   = float(b_pop[lon_mask].sum())
    pop_covered = float(b_pop[lit_mask].sum())
    return {
        "pop_total":   pop_total,
        "pop_covered": pop_covered,
        "fraction":    pop_covered / pop_total if pop_total > 0 else 0,
    }


@app.get("/tile/{z}/{x}/{y}.png")
def tile(z: int, x: int, y: int, radius: float = Query(..., ge=0, le=2000)):
    lat_min, lat_max, lon_min, lon_max = tile_bounds(z, x, y)
    band = lat_band(lat_min, lat_max)

    b_lons = LONS[band]
    b_lats = LATS[band]
    b_dist = MIN_DIST[band]
    b_pop  = POP[band]

    mask = (b_lons >= lon_min) & (b_lons <= lon_max) & (b_dist <= radius)

    if not mask.any():
        img = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
    else:
        sel_lons = b_lons[mask]
        sel_lats = b_lats[mask]
        sel_pop  = b_pop[mask]

        px, py = lonlat_to_tile_px(sel_lons, sel_lats, z, x, y)
        in_tile = (px >= 0) & (px < TILE_SIZE) & (py >= 0) & (py < TILE_SIZE)
        px = px[in_tile]; py = py[in_tile]; pop = sel_pop[in_tile]

        grid = np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.float32)
        np.add.at(grid, (py, px), pop)

        nonzero = grid > 0
        if nonzero.any():
            log_pop = np.log1p(grid)
            t = log_pop / log_pop.max()
            rgba = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)
            rgba[..., 0] = (t * 80).astype(np.uint8)
            rgba[..., 1] = (100 + t * 155).astype(np.uint8)
            rgba[..., 2] = (t * 40).astype(np.uint8)
            rgba[..., 3] = np.where(nonzero, (80 + t * 175).astype(np.uint8), 0)
            img = Image.fromarray(rgba, "RGBA")
        else:
            img = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False)
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )