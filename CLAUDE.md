# Tesla Supercharger Coverage

**Question:** What fraction of humanity lives within X km of a Tesla supercharger?

**Goal:** Interactive web app — a map where you set a radius and see the world's populated areas light up near superchargers, with live population stats globally and for the current viewport.

## Hardware & Environment

- RTX 4050 6GB VRAM, i7 13th gen (14 cores), 16GB RAM, Windows 11, CUDA 13.1
- Python venv: `.venv/` — activate with `.venv/Scripts/Activate.ps1`
- Key packages: `cupy-cuda13x`, `rasterio`, `pyproj`, `numpy`, `fastapi`, `uvicorn`
- Repo: https://github.com/consciousautomaton/supercharger-availability

---

## Running the app

**Backend** (from project root):
```powershell
.venv/Scripts/uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

**Frontend** (from project root):
```powershell
.venv/Scripts/python -m http.server 8001 --directory frontend
```

Then open `http://127.0.0.1:8001/index.html`.

---

## Data Pipeline (scripts run in order)

### 1. `scripts/extract_pixels.py` → `data/populated_pixels.npz`
Reads the GHS-POP 2030 TIF in 4096×4096 tiles across 14 CPU threads. Filters for population ≥ 1.0. Converts each pixel centre from Mollweide to WGS84 via pyproj. Saves 5 arrays: `lons`, `lats`, `pop`, `x_moll`, `y_moll`.

Runtime: ~2-3 min. Output: 1.99 GB.

### 2. `scripts/extract_chargers.py` → `data/chargers.npz`
Parses `data/tesla_scrape.json`. Key insight: include any entry with `supercharger_function.site_status == "open"` regardless of `location_type` — Tesla tags some stations as "party" instead of "supercharger" but they're real open stations. China locations have no `supercharger_function` field and are included if tagged as a supercharger type.

Result: **7,854 open superchargers** (not 5,792 — the difference is the "party"-tagged stations).

### 3. `scripts/compute_distances.py` → `data/pixel_distances.npz`
Loads both intermediate files. Runs a float32 Vincenty CUDA kernel (one thread per pixel, inner loop over all 7,854 chargers) in batches of 100M pixels. Saves results merged into one file.

- **Why Vincenty over haversine:** Vincenty models the WGS84 ellipsoid — more accurate near the poles. float32 precision gives ~1-10m error vs haversine's systematic ~0.3% (300m at 100km).
- **Why float32 not float64:** RTX 4050 has severely throttled FP64 (~140 GFLOPS vs ~9 TFLOPS FP32). float64 Vincenty would take ~2 hours; float32 takes ~46 min.
- **Batch size 100M:** CuPy's pinned memory allocator fails at 200M × 2 arrays simultaneously on 16GB RAM.

Runtime: ~46 min. Output: 3.68 GB.

### 4. `scripts/build_outputs.py` → `data/pop_cumulative.json`
Post-processes `pixel_distances.npz`. Builds a 501-entry JSON array where `array[r]` = total world population within `r` km of a charger. Used by the `/coverage` endpoint for instant global stat lookups.

Runtime: a few minutes. **Already run — do not re-run unless charger data changes.**

---

## Data Files

| File | Size | Contents |
|------|------|----------|
| `GHS_POP_E2030_GLOBE_R2023A_54009_100_V1_0.tif` | 6.6 GB | Source: GHS-POP 2030, 100m global raster, Mollweide (ESRI:54009), nodata=-200 |
| `tesla_scrape.json` | 13 MB | Raw Tesla API scrape, 21,553 locations. Scraped April 28 2026. |
| `chargers.npz` | 109 KB | Arrays: `lats`, `lons` (float64). 7,854 entries. |
| `populated_pixels.npz` | 2.51 GB | Arrays: `lons`, `lats`, `pop`, `x_moll`, `y_moll` (all float32). 370,272,898 entries. |
| `pixel_distances.npz` | 3.68 GB | All of the above plus `min_dist_km` (float32). Single source of truth for the backend. |
| `pop_cumulative.json` | ~2 KB | 501-entry array. `array[r]` = world population within r km of a charger. |

### pixel_distances.npz schema
Each index = one 100m × 100m populated cell:

| Array | Type | Description |
|-------|------|-------------|
| `lons` | float32 | WGS84 longitude, degrees |
| `lats` | float32 | WGS84 latitude, degrees |
| `pop` | float32 | Population count (GHS-POP 2030 projection) |
| `min_dist_km` | float32 | Distance to nearest open supercharger, km |
| `x_moll` | float32 | Mollweide x coordinate, metres |
| `y_moll` | float32 | Mollweide y coordinate, metres |

**Note:** The backend only loads 4 arrays (`lons`, `lats`, `pop`, `min_dist_km`). `x_moll`/`y_moll` are not loaded — cell corners are approximated client-side from lat/lon arithmetic (sub-metre error, imperceptible).

---

## Architecture

### Backend (`backend/main.py`) — FastAPI

Loads 4 arrays from `pixel_distances.npz` into RAM (~6 GB total including the lat sort index). Builds a lat-sort index at startup for fast spatial queries.

**Spatial index:** `np.argsort(LATS)` → `SORTED_LATS` + `LAT_ORDER` (int32). Binary search with `np.searchsorted` narrows to the viewport latitude band instantly; then filter by lon + distance within that band.

**Endpoints:**

```
GET /coverage?radius=50
```
Returns global population covered at that radius. Instant lookup from `pop_cumulative.json`.

```
GET /pixels?lat_min=&lat_max=&lon_min=&lon_max=&radius=&zoom=&max_px=
```
Returns pixels in the viewport within `radius` km of a charger. Always returns native 100m cells (`agg=1` hardcoded). Has `lon_wrap()` to handle MapLibre bounding boxes that exceed ±180°. Response:
```json
{
  "mode": "native",
  "cell_m": 100,
  "pixels": [{"lat": ..., "lon": ..., "pop": ...}, ...],
  "count": 12345,
  "truncated": false,
  "viewport": {"pop_covered": ..., "pop_total": ..., "fraction": ...}
}
```

The backend also has a fully implemented `aggregate_pixels()` function (for low-zoom supercell merging) but it is currently disabled — `agg` is hardcoded to 1 because aggregated cells looked bad visually.

### Frontend (`frontend/index.html`)

**Stack:** MapLibre GL JS (basemap) + deck.gl standalone (data layer).

**Current rendering approach:** Two separate WebGL canvases stacked — MapLibre renders the dark CartoDB basemap, deck.gl renders the population cells on top. The browser compositor blends them. This works but has GPU compositor overhead.

**Planned improvement:** Move deck.gl into MapLibre's WebGL context via `map.addLayer({ type: 'custom' })` — one canvas, no compositor overhead, better iGPU performance.

**Cell rendering:** `deck.SolidPolygonLayer` with `SolidPolygonLayer`. Each cell is a 4-corner quad. Corners computed client-side:
- `dlat = (cell_m/2) / 110540`
- `dlon = (cell_m/2) / (111320 * cos(lat))`

**Colour:** `sqrt(pop/maxPop)` → green intensity (denser = brighter).

**Viewport sync:** `map.on('move', syncViewState)` fires at 60fps during pan to keep deck.gl's camera aligned with MapLibre. `map.on('resize', syncSize)` updates canvas dimensions.

**Data fetch:** On `moveend`/`zoomend` (400ms debounce), fetches `/pixels` for the current viewport. Currently requests up to 1,000,000 cells.

---

## Key Technical Decisions

| Decision | Why |
|----------|-----|
| float32 Vincenty (not haversine) | More accurate near poles; float32 fast enough on consumer GPU |
| Backend loads only 4 of 6 arrays | x_moll/y_moll not needed — corners approximated client-side, saves ~3 GB RAM |
| int32 sort index | Saves ~1.5 GB RAM vs int64 — 370M entries × 4 bytes vs 8 bytes |
| Lat-sort + binary search spatial index | Fast, no extra RAM beyond the sorted index. KD-tree rejected (too much RAM). |
| "party" type included in chargers | 2,062 valid open stations tagged this way in Tesla's API |
| Native 100m cells only (no aggregation) | Aggregated cells at 200m–400m looked bad visually. Aggregation code exists but disabled. |
| Two-canvas approach (MapLibre + deck.gl) | Works, but compositor overhead hits iGPU. Planned fix: shared GL context via MapLibre custom layer. |
| lon_wrap() on bbox | MapLibre getBounds() can return longitudes outside ±180 at world-wrap boundaries |

---

## Next Steps

1. **Shared GL context** — move deck.gl into MapLibre's WebGL context via `map.addLayer({ type: 'custom' })` to eliminate compositor overhead
2. **Binary data format** — replace JSON response with raw `Float32Array` bytes; ~10x faster serialization + parsing
3. **Global zoom** — re-enable aggregation at low zoom, but with a minimum agg factor of 4 (400m cells) so the visual quality is acceptable. Skip the 200m step entirely.