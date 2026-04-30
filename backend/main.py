import json
import numpy as np
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

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

CELL_M = 100.0


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


def aggregate_pixels(band, lon_segments, radius, agg):
    b_lons = LONS[band]
    b_lats = LATS[band]
    b_dist = MIN_DIST[band]
    b_pop  = POP[band]

    lon_mask = np.zeros(len(b_lons), dtype=bool)
    for lo, hi in lon_segments:
        lon_mask |= (b_lons >= lo) & (b_lons <= hi)

    bbox_mask = lon_mask
    lit_mask  = bbox_mask & (b_dist <= radius)

    viewport_total   = float(b_pop[bbox_mask].sum())
    viewport_covered = float(b_pop[lit_mask].sum())

    if not bbox_mask.any():
        return None, viewport_total, viewport_covered

    sel_lats = b_lats[bbox_mask]
    sel_lons = b_lons[bbox_mask]
    sel_pop  = b_pop[bbox_mask]
    sel_dist = b_dist[bbox_mask]

    DEG_PER_100M_LAT = 100.0 / 110_540.0
    bin_deg_lat = agg * DEG_PER_100M_LAT

    mid_lat = float((sel_lats.min() + sel_lats.max()) / 2)
    DEG_PER_100M_LON = 100.0 / (111_320.0 * np.cos(np.radians(mid_lat)))
    bin_deg_lon = agg * DEG_PER_100M_LON

    lat_bin = np.floor(sel_lats / bin_deg_lat).astype(np.int32)
    lon_bin = np.floor(sel_lons / bin_deg_lon).astype(np.int32)
    lat_bin -= lat_bin.min()
    lon_bin -= lon_bin.min()

    n_lon_bins = int(lon_bin.max()) + 1
    flat = (lat_bin.astype(np.int64) * n_lon_bins + lon_bin).astype(np.int64)
    n_bins = int(flat.max()) + 1

    pop_total   = np.zeros(n_bins, dtype=np.float64)
    pop_covered = np.zeros(n_bins, dtype=np.float64)
    lat_wsum    = np.zeros(n_bins, dtype=np.float64)
    lon_wsum    = np.zeros(n_bins, dtype=np.float64)

    np.add.at(pop_total, flat, sel_pop)
    np.add.at(lat_wsum,  flat, sel_lats * sel_pop)
    np.add.at(lon_wsum,  flat, sel_lons * sel_pop)

    lit_sel = sel_dist <= radius
    np.add.at(pop_covered, flat[lit_sel], sel_pop[lit_sel])

    nonempty    = pop_total > 0
    pop_total   = pop_total[nonempty]
    pop_covered = pop_covered[nonempty]
    lat_c       = lat_wsum[nonempty] / pop_total
    lon_c       = lon_wsum[nonempty] / pop_total
    fraction    = pop_covered / pop_total

    return {
        "lat_c":       lat_c,
        "lon_c":       lon_c,
        "pop_total":   pop_total,
        "pop_covered": pop_covered,
        "fraction":    fraction,
        "cell_m":      agg * CELL_M,
    }, viewport_total, viewport_covered


@app.get("/coverage")
def coverage(radius: int = Query(ge=0, le=500)):
    covered = POP_CUMULATIVE[radius]
    return {"radius_km": radius, "covered": covered, "total": TOTAL_POP, "fraction": covered / TOTAL_POP}


@app.get("/pixels")
def pixels(
    lat_min: float = Query(...),
    lat_max: float = Query(...),
    lon_min: float = Query(...),
    lon_max: float = Query(...),
    radius:  float = Query(..., ge=0, le=2000),
    zoom:    float = Query(default=14.0, ge=0, le=22),
    max_px:  int   = Query(default=50_000, ge=1, le=1_000_000),
):
    lon_segments = lon_wrap(lon_min, lon_max)
    band = lat_band(lat_min - 0.01, lat_max + 0.01)
    agg = 1

    if agg == 1:
        # ── Native path (zoom ≥ 12) ──────────────────────────────────────
        b_lons = LONS[band]
        b_dist = MIN_DIST[band]
        b_pop  = POP[band]

        lon_mask = np.zeros(len(b_lons), dtype=bool)
        for lo, hi in lon_segments:
            lon_mask |= (b_lons >= lo) & (b_lons <= hi)

        bbox_mask = lon_mask
        lit_mask  = bbox_mask & (b_dist <= radius)

        viewport_total   = float(b_pop[bbox_mask].sum())
        viewport_covered = float(b_pop[lit_mask].sum())

        rel_idx   = np.where(lit_mask)[0]
        truncated = len(rel_idx) > max_px
        if truncated:
            rel_idx = rel_idx[:max_px]

        abs_idx = band[rel_idx]
        lats_px = LATS[abs_idx].tolist()
        lons_px = LONS[abs_idx].tolist()
        pops_px = POP[abs_idx].tolist()

        pixels_out = [
            {"lat": lats_px[i], "lon": lons_px[i], "pop": pops_px[i]}
            for i in range(len(abs_idx))
        ]
        return {
            "mode":      "native",
            "cell_m":    100,
            "pixels":    pixels_out,
            "count":     len(pixels_out),
            "truncated": truncated,
            "viewport": {
                "pop_covered": viewport_covered,
                "pop_total":   viewport_total,
                "fraction":    viewport_covered / viewport_total if viewport_total > 0 else 0,
            },
        }

    else:
        # ── Aggregated path (zoom < 12) ──────────────────────────────────
        result, viewport_total, viewport_covered = aggregate_pixels(
            band, lon_segments, radius, agg
        )

        if result is None:
            return {
                "mode": "aggregated", "cell_m": agg * 100,
                "pixels": [], "count": 0, "truncated": False,
                "viewport": {"pop_covered": 0, "pop_total": 0, "fraction": 0},
            }

        lat_c     = result["lat_c"]
        lon_c     = result["lon_c"]
        pop_total = result["pop_total"]
        fraction  = result["fraction"]
        cell_m    = result["cell_m"]

        n         = len(lat_c)
        truncated = n > max_px
        if truncated:
            order   = np.argsort(pop_total)[::-1][:max_px]
            lat_c   = lat_c[order]
            lon_c   = lon_c[order]
            pop_total = pop_total[order]
            fraction  = fraction[order]

        pixels_out = [
            {"lat": float(lat_c[i]), "lon": float(lon_c[i]),
             "pop": float(pop_total[i]), "fraction": float(fraction[i])}
            for i in range(len(lat_c))
        ]
        return {
            "mode":      "aggregated",
            "cell_m":    float(cell_m),
            "pixels":    pixels_out,
            "count":     len(pixels_out),
            "truncated": truncated,
            "viewport": {
                "pop_covered": viewport_covered,
                "pop_total":   viewport_total,
                "fraction":    viewport_covered / viewport_total if viewport_total > 0 else 0,
            },
        }
