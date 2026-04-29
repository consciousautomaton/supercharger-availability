# Tesla Supercharger Coverage

**Question:** What fraction of humanity lives within X km of a Tesla supercharger?

**Goal:** Interactive web app — a map where you set a radius and see the world's populated areas light up near superchargers, with live population stats globally, per country, and for the current viewport.

## Hardware & Environment

- RTX 4050 6GB VRAM, i7 13th gen (14 cores), 16GB RAM, Windows 11, CUDA 13.1
- Python venv: `.venv/` — activate with `.venv/Scripts/Activate.ps1`
- Key packages: `cupy-cuda13x`, `rasterio`, `pyproj`, `numpy`, `fastapi`, `uvicorn`
- Repo: https://github.com/consciousautomaton/supercharger-availability

---

## Data Pipeline (scripts run in order)

### 1. `scripts/extract_pixels.py` → `data/populated_pixels.npz`
Reads the GHS-POP 2030 TIF in 4096×4096 tiles across 14 CPU threads. Filters for population ≥ 1.0. Converts each pixel centre from Mollweide to WGS84 via pyproj. Saves 5 arrays.

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

### 4. `scripts/build_outputs.py` → `data/pop_cumulative.json` + `data/global_min_dist.bin`
Post-processes `pixel_distances.npz` into frontend-ready formats. **Not yet run** — waiting to finalise frontend architecture first.

---

## Data Files

| File | Size | Contents |
|------|------|----------|
| `GHS_POP_E2030_GLOBE_R2023A_54009_100_V1_0.tif` | 6.6 GB | Source: GHS-POP 2030, 100m global raster, Mollweide (ESRI:54009), nodata=-200 |
| `tesla_scrape.json` | 13 MB | Raw Tesla API scrape, 21,553 locations. Scraped April 28 2026. |
| `chargers.npz` | 109 KB | Arrays: `lats`, `lons` (float64). 7,854 entries. |
| `populated_pixels.npz` | 2.51 GB | Arrays: `lons`, `lats`, `pop`, `x_moll`, `y_moll` (all float32). 370,272,898 entries. |
| `pixel_distances.npz` | 3.68 GB | All of the above plus `min_dist_km` (float32). The single source of truth for the backend. |

### pixel_distances.npz schema
Each index corresponds to one 100m × 100m populated cell:

| Array | Type | Description |
|-------|------|-------------|
| `lons` | float32 | WGS84 longitude, degrees |
| `lats` | float32 | WGS84 latitude, degrees |
| `pop` | float32 | Population count (GHS-POP 2030 projection) |
| `min_dist_km` | float32 | Distance to nearest open supercharger, km |
| `x_moll` | float32 | Mollweide x coordinate, metres |
| `y_moll` | float32 | Mollweide y coordinate, metres |

`x_moll` and `y_moll` enable exact cell corner computation: corners are simply `(x±50, y±50)` in Mollweide space, projected to WGS84 via pyproj.

---

## Architecture

### Visualisation approach
Each GHS-POP cell is rendered as its actual 100m × 100m square on the map. This is the natural unit — no resampling, no tile alignment issues. At zoom level 13 (~5km view), each cell is ~5 screen pixels. At zoom 15 (~1km view), each cell is ~20 screen pixels.

The cells tile perfectly with no gaps (Mollweide→WGS84 is a bijection), but they are not rectangles in WGS84 — they are slightly curved quadrilaterals. The deviation from a rectangle is sub-metre, but we use exact corner computation anyway.

### Frontend
Leaflet map. For the current viewport, requests lit pixels from the backend. Renders each pixel as a coloured rectangle at its exact WGS84 position. Colour = population density. Visibility = `min_dist_km <= slider_radius`.

### Backend (FastAPI)
Serves spatial queries against `pixel_distances.npz`. Key endpoint:

```
GET /pixels?lat_min=52.3&lat_max=52.7&lon_min=13.1&lon_max=13.8&radius=20
```

Returns: list of `(lon, lat, pop, min_dist_km, x_moll, y_moll)` for pixels in bounds where `min_dist_km <= radius`. Frontend draws each as a quadrilateral.

Also serves:
- `GET /coverage?radius=X` → global population covered (from `pop_cumulative.json`, instant lookup)
- `GET /coverage/bbox?...&radius=X` → population covered in current viewport

### Population stat
`pop_cumulative.json` — 501-entry JSON array. `array[50]` = total world population within 50km of a charger. Computed once by `build_outputs.py`. Slider reads this directly, no backend call needed.

---

## Development Plan

**Phase 1 (current): Berlin prototype**
- Extract Berlin pixels from `pixel_distances.npz` (lat 52.3–52.7, lon 13.1–13.8)
- FastAPI backend serving Berlin data
- Leaflet frontend with radius slider, cell rendering, local population stat

**Phase 2: Global**
- Spatial index (KD-tree or PostGIS) over full 370M pixels
- Backend serves any viewport at full 100m resolution
- Global stat from `pop_cumulative.json`

**Phase 3: Features**
- Per-country coverage breakdown (Natural Earth polygons)
- "Zoom to location" search
- Population density layer always visible, coverage layer toggled by slider

---

## Key Technical Decisions

| Decision | Why |
|----------|-----|
| float32 Vincenty (not haversine) | More accurate near poles; float32 fast enough on consumer GPU |
| Mollweide coordinates saved in npz | Enables exact cell corner projection without round-trip |
| GHS-POP cells as render unit (not map tiles) | No resampling; 100m accuracy preserved at any zoom |
| "party" type included in chargers | 2,062 valid open stations tagged this way in Tesla's API |
| Backend spatial query (not static texture) | Enables true 100m resolution at any zoom level |