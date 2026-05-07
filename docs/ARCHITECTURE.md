# Architecture

This document describes the current static tile architecture for the Tesla Supercharger coverage map.

The core idea is simple:

1. Start with a global 100 m population raster.
2. Keep only cells with population.
3. Compute each populated cell's distance to the nearest open Tesla Supercharger.
4. Rasterize those populated source cells into WebMercator map tiles.
5. Let the browser threshold the precomputed distance tiles instantly as the radius slider moves.

## High-Level Data Flow

```text
GHS-POP TIFF
  -> scripts/extract_pixels.py
  -> data/populated_pixels.npz

Tesla scrape JSON
  -> scripts/extract_chargers.py
  -> data/chargers.npz

populated_pixels.npz + chargers.npz
  -> scripts/compute_distances.py
  -> data/pixel_distances.npz

OSM PBF region + pixel_distances.npz + chargers.npz
  -> scripts/compute_road_distances_gpu.py
  -> data/pixel_road_distances_<region>.npz

pixel_distances.npz + pixel_road_distances_*.npz
  -> scripts/merge_road_distances.py
  -> data/pixel_distances_road.npz

pixel_distances.npz + populated_pixels.npz
  -> scripts/precompute_tiles.py
  -> frontend/tiles/{z}/{x}/{y}.png

pixel_road_distances_<region>.npz + populated_pixels.npz
  -> scripts/precompute_road_region_tiles.py --region <region>
  -> frontend/tiles_road/{z}/{x}/{y}.png sparse override tiles

pixel_distances.npz
  -> scripts/build_outputs.py
  -> frontend/pop_cumulative.json

data/npy sorted arrays
  -> scripts/precompute_viewport_stats.py
  -> frontend/viewport_*.bin + frontend/viewport_manifest.json

Natural Earth Admin 0 countries + populated_pixels.npz + pixel_distances.npz + chargers.npz
  -> scripts/precompute_country_stats.py
  -> frontend/countries.geojson + frontend/country_stats.json

frontend/index.html
  -> MapLibre basemap + custom WebGL coverage layer
```

## Source Population Data

The population source is the GHS-POP 2030 raster:

```text
data/GHS_POP_E2030_GLOBE_R2023A_54009_100_V1_0.tif
```

Important properties:

- CRS: Mollweide, `ESRI:54009`.
- Resolution: nominal 100 m cells.
- Values: population count per source cell.
- Nodata values are ignored.
- Cells with `pop < 1.0` are ignored by `extract_pixels.py`.

The project does not first resample the whole TIFF into WebMercator. Instead, it keeps the source-cell center and source-cell Mollweide coordinates. Later, `precompute_tiles.py` projects each source cell's four true Mollweide corners into WebMercator and rasterizes the exact projected footprint.

That choice matters: the rendered cells are tied to the original raster cells, not to an arbitrary WebMercator resampling grid.

## Charger Extraction

`scripts/extract_chargers.py` reads:

```text
data/tesla_scrape.json
```

It writes:

```text
data/chargers.npz
```

The output contains:

| Array | Type | Meaning |
| --- | --- | --- |
| `lats` | float64 | Charger latitude |
| `lons` | float64 | Charger longitude |

Tesla's location data is not perfectly uniform. Some real open Superchargers are not tagged with the obvious `location_type`. The script therefore:

- Trusts `supercharger_function.site_status == "open"` when `supercharger_function` exists.
- Falls back to `location_type` for entries without `supercharger_function`, such as some China locations.

## Pixel Extraction

`scripts/extract_pixels.py` reads the TIFF in 4096 by 4096 windows across multiple CPU workers.

It writes:

```text
data/populated_pixels.npz
```

Schema:

| Array | Type | Meaning |
| --- | --- | --- |
| `lons` | float32 | WGS84 longitude of source-cell center |
| `lats` | float32 | WGS84 latitude of source-cell center |
| `pop` | float32 | Population in the source cell |
| `x_moll` | float32 | Mollweide x coordinate of source-cell center, meters |
| `y_moll` | float32 | Mollweide y coordinate of source-cell center, meters |

The `x_moll` and `y_moll` arrays are important for exact tile rendering. A center point alone is not enough to draw the true source-cell footprint.

## Distance Computation

`scripts/compute_distances.py` reads:

```text
data/populated_pixels.npz
data/chargers.npz
```

It writes:

```text
data/pixel_distances.npz
```

Schema:

| Array | Type | Meaning |
| --- | --- | --- |
| `lons` | float32 | WGS84 longitude of source-cell center |
| `lats` | float32 | WGS84 latitude of source-cell center |
| `pop` | float32 | Population in the source cell |
| `min_dist_km` | float32 | Distance to nearest open Supercharger, km |

The script uses CuPy and a custom CUDA kernel. Each GPU thread handles one populated cell and loops over all chargers to find the nearest distance.

The distance formula is a float32 Vincenty-style ellipsoidal calculation. This is more accurate than a spherical haversine distance, especially at high latitudes, while still being practical on a consumer NVIDIA GPU.

## Road Distance Pipeline

The road-distance path is an optional overlay on top of the global great-circle file. It is computed per region because a whole-world routable OSM graph is too large for the target 16 GB GPU workflow.

Current region outputs:

```text
data/pixel_road_distances_DEU.npz
```

The GPU road script is:

```powershell
.venv/Scripts/python scripts/compute_road_distances_gpu.py --help
```

It parses OSM PBF extracts with `pyosmium`, not `pyrosm`. `pyosmium` is used because `pyrosm` currently depends on `pyrobuf`, which is broken with modern setuptools in this environment.

The script builds a driveable-road graph from the PBF, snaps chargers and population cells to graph nodes, and runs a multi-source shortest-path relaxation on the GPU. The CUDA path uses a CuPy `RawKernel` and atomic minimum updates over float distances. Outputs are region-scoped:

```text
data/pixel_road_distances_<region>.npz
```

Expected arrays include:

| Array | Meaning |
| --- | --- |
| `road_dist_km` | OSM driving-network distance to the nearest charger, capped by the region run |
| `gc_dist_km` | Great-circle comparison distance for the same region cells |
| `global_pixel_index` | Index into `data/pixel_distances.npz` |
| `lons`, `lats`, `pop` | Region cell coordinates and population |
| `region` | Region identifier |

Merge region outputs into a global road-distance source with:

```powershell
.venv/Scripts/python scripts/merge_road_distances.py
```

This writes:

```text
data/pixel_distances_road.npz
```

Merge semantics:

- finite `road_dist_km` values replace `min_dist_km` at `global_pixel_index`,
- infinite road distances are treated as outside the computed/capped road region and fall back to the original great-circle distance,
- if multiple region outputs cover the same global cell, the smallest finite road distance wins,
- `data/pixel_distances.npz` is never modified in place.

Downstream scripts accept a road source without clobbering the great-circle outputs:

```powershell
.venv/Scripts/python scripts/precompute_road_region_tiles.py --region DEU --workers 1 --max-pending 2
.venv/Scripts/python scripts/build_outputs.py --source road
.venv/Scripts/python scripts/precompute_viewport_stats.py --source road
.venv/Scripts/python scripts/precompute_country_stats.py --source road
.venv/Scripts/python scripts/validate_outputs.py --source road
```

Equivalent environment-variable form:

```powershell
$env:DISTANCE_SOURCE = "road"
```

Road outputs are suffixed, for example:

```text
frontend/tiles_road/
frontend/pop_cumulative_road.json
frontend/country_stats_road.json
frontend/viewport_manifest_road.json
```

Road tiles are intentionally sparse regional overrides, not a full duplicate of the global tile pyramid. In road mode, the custom WebGL layer loads the existing great-circle tile as the base and optionally samples `frontend/tiles_road/`. If the road override pixel has alpha, it replaces the base pixel; otherwise the shader uses the base great-circle pixel. This keeps Germany road-distance visuals correct without rebuilding unchanged global tiles.

Do not run a full `scripts/precompute_tiles.py 11 --source road` on the 16 GB laptop for the current Germany-only dataset. It duplicates almost all global tiles and can exhaust memory/IO.

### Distance Limitations

The nearest-charger distance is a distance from the population cell center to the charger coordinate.

Consequences:

- The visual footprint is based on the source cell area.
- The distance value is based on the source cell center.
- At a very small radius, a cell can be partially within radius while its center is outside, or vice versa.
- This uncertainty is bounded by roughly half a source-cell diagonal, plus source data/projection precision.

For a nominal 100 m cell, that center-vs-area uncertainty is small relative to normal radii like 5 km, 50 km, or 500 km. It matters most near `0 km` and `1 km`.

## Tile Precomputation

`scripts/precompute_tiles.py` is the main static-map renderer.

It reads:

```text
data/pixel_distances.npz
data/populated_pixels.npz
```

It writes:

```text
frontend/tiles/{z}/{x}/{y}.png
data/npy/*.npy
```

The `data/npy/` files are reusable caches. They avoid extracting, sorting, and reprojecting hundreds of millions of cells every time the PNG format changes.

### One-Time Extracted Arrays

`ensure_npy_extracted()` writes raw `.npy` files from `.npz` inputs:

```text
data/npy/lons.npy
data/npy/lats.npy
data/npy/pop.npy
data/npy/min_dist_km.npy
data/npy/x_moll.npy
data/npy/y_moll.npy
```

### Latitude-Sorted Arrays

`ensure_lat_sorted()` creates sorted versions:

```text
data/npy/lats_sorted.npy
data/npy/lons_sorted.npy
data/npy/pop_sorted.npy
data/npy/dist_sorted.npy
data/npy/x_moll_sorted.npy
data/npy/y_moll_sorted.npy
```

The tile renderer now mostly uses the footprint row index, but the sorted layout is still the base cached data layout.

### Exact WebMercator Footprint Cache

For max native zoom `z=11`, the script projects every source cell's four Mollweide corners into WebMercator pixel coordinates.

It writes:

```text
data/npy/wm_z11_x0.npy
data/npy/wm_z11_x1.npy
data/npy/wm_z11_y0.npy
data/npy/wm_z11_y1.npy
data/npy/wm_z11_footprints.done
```

Each index stores the bounding pixel footprint of one source cell at z11.

This cache is what fixed the janky/misaligned grid problem. Earlier approaches used approximate center-point quads or tile-space assumptions. The current approach rasterizes the actual projected source-cell footprint.

### Row Index Cache

To avoid scanning all 370M cells for every tile row, the script builds a row index:

```text
data/npy/wm_z11_row_counts.npy
data/npy/wm_z11_row_offsets.npy
data/npy/wm_z11_row_indices.npy
data/npy/wm_z11_row_index.done
```

For each z11 tile row, `row_indices` gives only the cells whose footprint intersects that tile row.

This changed empty rows from very expensive to essentially free. It also explains the S-curve timing:

- Early polar/ocean rows have little or no populated data.
- Dense inhabited latitude bands are expensive.
- Once the dense band is past, later rows become fast again.

### Tile Pyramid

The script streams z11 rows and builds lower zooms bottom-up.

For each tile pixel:

- `grid_dist` stores the nearest distance among source cells contributing to that pixel.
- `grid_pop` stores summed population for source cells contributing to that pixel.

Parent tiles are built by reducing child tiles:

- distance: minimum over the child 2 by 2 pixels.
- population: sum over the child 2 by 2 pixels.

That means lower-zoom tiles are summaries of the same exact z11 source rasterization.

## Coverage Tile Encoding

Current tile format:

```text
R/G = 16-bit distance code
B   = reserved, currently 0
A   = population alpha
```

Distance code:

```text
code = ceil(clamp(distance_km / 500, 0, 1) * 65535)
```

Decode:

```text
code = R * 256 + G
distance_km approx code / 65535 * 500
```

Radius threshold in the browser:

```text
radius_code = ceil(radius_km / 500 * 65535)
covered if pixel_code <= radius_code
```

This is intentionally integer-based. It avoids normalized float comparison surprises and fixes the previous 8-bit problem where any distance below about 1.96 km encoded to zero.

### Why `ceil`

Using `ceil` means any positive distance gets a positive code.

So:

- True zero distance -> code `0`.
- Positive distance, however tiny -> code at least `1`.

This is required for `0 km` to mean what the UI says.

### Alpha Channel

Alpha is:

```text
alpha = log1p(pop) / max(log1p(pop)) * 255
```

The log transform keeps dense places visible without making low-population cells disappear entirely.

Alpha is visual intensity, not a population-stat calculation. Stats are computed from separate population arrays.

## Frontend Rendering

`frontend/index.html` uses MapLibre GL JS.

The coverage layer is a MapLibre custom WebGL layer. It:

1. Computes visible tile coordinates for the current map view.
2. Loads PNG coverage tiles from `frontend/tiles/`.
3. Uploads each PNG as a WebGL texture.
4. Draws each tile as a quad in MapLibre's projection.
5. In the fragment shader, decodes RG16 distance and compares it to the current slider radius.

The slider is instant because moving the slider does not fetch new data. It only updates a shader uniform.

The current frontend is organized into three UI areas:

- interaction panel: search, country selector, distance slider/input, display mode,
- results panel: selected-country access, Supercharger count/density, and global context,
- legend panel: map symbols and boundary/source notes.

Search uses the MapLibre geocoder control with a Nominatim-backed custom API. It is embedded inside the interaction panel rather than using the default MapLibre corner control.

### Display Modes

The UI has two modes:

| Mode | Behavior |
| --- | --- |
| `Exact` | Always shows raw precomputed raster cells. |
| `Auto` | Smooths only at lower/mid zooms and only beyond tiny radii. |

`Auto` is a display-only effect. It samples neighboring texels after thresholding and blends alpha. It does not change the underlying distance tiles or stats.

Smoothing fades out as the user zooms in so close inspection remains faithful to the source cells.

## Global Stats

`scripts/build_outputs.py` writes:

```text
frontend/pop_cumulative.json
data/pop_cumulative.json
```

Current JSON format:

```json
{
  "radius_step_km": 1,
  "radius_max_km": 500,
  "total_pop": 8480668160.0,
  "cumulative": [0.0, 123.0, "..."]
}
```

The `cumulative` array has 501 entries. Index `r` means:

```text
population with distance <= r km
```

Bucketing uses:

```text
bucket = ceil(distance_km)
```

So index `0` only contains exact zero-distance cells.

## Viewport Stats

`scripts/precompute_viewport_stats.py` writes:

```text
frontend/viewport_pop_total.bin
frontend/viewport_pop_covered.bin
frontend/viewport_manifest.json
```

The viewport stats are approximate but fast.

Spatial bins:

```text
180 latitude bins x 360 longitude bins
```

Each bin is 1 degree by 1 degree. The frontend sums all bins touched by the current viewport.

Radius bins:

```text
0, 1, 2, ..., 500 km
```

`viewport_pop_covered.bin` shape:

```text
(180, 360, 501)
```

Stored as float32 in row-major order.

### Viewport Stat Limitations

The viewport stat is a 1 degree bin approximation.

Consequences:

- It is fast and static.
- It can overcount or undercount at viewport edges.
- It is best interpreted as a live approximate contextual number.
- It should not be used as a precise clipped polygon statistic.

Global stats are more exact because they sum all cells directly by distance bucket.

## Country Stats

`scripts/precompute_country_stats.py` writes:

```text
frontend/countries.geojson
frontend/country_stats.json
```

It uses Natural Earth Admin 0 boundaries. The default scale is `10m`:

```powershell
.venv/Scripts/python scripts/precompute_country_stats.py
```

The scale can be changed:

```powershell
$env:COUNTRY_BOUNDARY_SCALE = "50m"
.venv/Scripts/python scripts/precompute_country_stats.py
```

### Country Assignment Method

Population cells are assigned by center point:

```text
source-cell Mollweide center -> temporary rasterized country window -> country id
```

The script does not rasterize a full global 100 m country grid. That was rejected because it creates a roughly 130 GB country-id raster. The current method rasterizes bounded temporary windows and discards them after sampling.

Charger points are assigned using the same country-window sampling method.

### Charger Overrides

Spatial assignment misses some coastal, island, and boundary chargers. Manual overrides are read from:

```text
data/country_charger_overrides.csv
```

Expected columns:

```csv
charger_index,iso_a3,country_name
```

Review/export helper:

```powershell
.venv/Scripts/python scripts/export_unassigned_chargers.py
```

Then open:

```text
http://127.0.0.1:8001/review_unassigned_chargers.html
```

The review page supports:

- click individual charger and assign country,
- assign visible chargers to a country,
- draw rectangle, preview count, confirm/cancel,
- edit/export override CSV.

The current processed data has all chargers assigned after overrides:

```text
Assigned chargers: 7,854 / 7,854
```

### Country Stats Format

`frontend/country_stats.json` contains:

```json
{
  "radius_step_km": 1,
  "radius_max_km": 500,
  "n_radii": 501,
  "assignment": "country polygon contains source cell center",
  "boundary_source": "Natural Earth Admin 0 Countries 10m",
  "countries": [
    {
      "id": 1,
      "iso_a3": "AFG",
      "name": "Afghanistan",
      "bbox": [60.48, 29.38, 74.89, 38.47],
      "total_pop": 49914857.5,
      "charger_count": 0,
      "covered": [0.0, "... 501 entries ..."]
    }
  ]
}
```

### Country Boundary Limits

Natural Earth Admin 0 is a map boundary dataset, not a national statistical office dataset.

Consequences:

- country stats are sovereign/admin-boundary approximations,
- some countries include overseas territories,
- some coastal/island/border population cells do not assign to any country polygon,
- country values should be explained as Natural Earth country/territory stats.

Current country population assignment:

```text
Assigned country population: 8,404,231,351 / 8,480,668,252
```

Unassigned population review exports:

```powershell
.venv/Scripts/python scripts/export_unassigned_population.py
```

Outputs:

```text
frontend/unassigned_population_bins.csv
frontend/unassigned_population_bins.geojson
```

These are binned review files, not currently applied as overrides.

## Correctness Limits

### Population Resolution

The source is a 100 m raster. The map cannot know sub-cell population distribution.

At high zoom, showing each source-cell footprint is the most faithful representation. Smoothing can improve overview aesthetics, but it should not be interpreted as new spatial detail.

### WebMercator Distortion

The source data is in Mollweide. The map is WebMercator.

The current renderer projects every source cell's corners into WebMercator before rasterization. This makes the visual map alignment accurate for display. The cell shapes may look stretched or rotated in some places because that is the projection doing its job, not tile misalignment.

### Distance Precision

Distances are float32 nearest-charger distances from cell center to charger coordinate.

Practical limits:

- Charger coordinates have their own source precision.
- Population cell centers are approximations of population distribution inside cells.
- Float32 Vincenty is accurate enough for this use case, but not a legal/surveying distance model.

### Max Native Zoom

Current native tile max is z11.

Zooming beyond z11 enlarges z11 textures. Exact cells remain true to the source raster, but the screen can look blocky because the data itself is cell-based and the browser is magnifying it.

Generating z12/z13 would make texture sampling finer, but it would not invent sub-100 m population detail. For this project, shader-side overview smoothing is a better first step than higher native zooms.

## Validation

After rebuilding tiles and stats, run:

```powershell
.venv/Scripts/python scripts/validate_outputs.py
```

It checks:

- `frontend/pop_cumulative.json` uses the current structured format.
- Global stats use 1 km radius metadata.
- Viewport manifest uses 501 radius buckets.
- Viewport binary sizes match the manifest.
- Sampled z11 tiles decode as RG16 distance tiles.
- Sampled zero-code and 0-1 km pixels are reported for sanity checking.

Manual checks:

1. Start the static server:

   ```powershell
   .venv/Scripts/python -m http.server 8001 --directory frontend
   ```

2. Open:

   ```text
   http://127.0.0.1:8001/index.html
   ```

3. Hard refresh.

4. Verify:

   - `0 km` does not show broad green blobs.
   - `1 km`, `2 km`, and `5 km` grow gradually.
   - `Exact` mode shows crisp source-cell footprints.
   - `Auto` mode looks smoother when zoomed out.
   - Tile boundaries do not show seams or duplicate strips.

## Common Rebuild Cases

### Frontend-only change

No data rebuild required. Restart or refresh the static server page.

### Shader or tile encoding change

Rerun:

```powershell
$env:TILE_ENCODE_WORKERS = "16"
.venv/Scripts/python scripts/precompute_tiles.py 11
```

If stats semantics also changed, rerun stats too.

### Stats semantic change

Rerun:

```powershell
.venv/Scripts/python scripts/build_outputs.py
.venv/Scripts/python scripts/precompute_viewport_stats.py
.venv/Scripts/python scripts/validate_outputs.py
```

### New charger data

Rerun from charger extraction onward:

```powershell
.venv/Scripts/python scripts/extract_chargers.py
.venv/Scripts/python scripts/compute_distances.py
$env:TILE_ENCODE_WORKERS = "16"
.venv/Scripts/python scripts/precompute_tiles.py 11
.venv/Scripts/python scripts/build_outputs.py
.venv/Scripts/python scripts/precompute_viewport_stats.py
.venv/Scripts/python scripts/validate_outputs.py
```

### New population TIFF

Rerun the full pipeline:

```powershell
.venv/Scripts/python scripts/extract_pixels.py
.venv/Scripts/python scripts/extract_chargers.py
.venv/Scripts/python scripts/compute_distances.py
$env:TILE_ENCODE_WORKERS = "16"
.venv/Scripts/python scripts/precompute_tiles.py 11
.venv/Scripts/python scripts/build_outputs.py
.venv/Scripts/python scripts/precompute_viewport_stats.py
.venv/Scripts/python scripts/validate_outputs.py
```

## Troubleshooting

### Map shows no coverage layer

Likely causes:

- Tiles were not generated.
- Browser is loading stale cached assets.
- `TILE_VERSION` in `frontend/index.html` does not match the current tile format.
- WebGL shader failed to compile.

Check browser DevTools console and the status line in the panel.

### `0 km` shows broad blobs

Likely causes:

- Browser is still loading old 8-bit tiles.
- `precompute_tiles.py` has not been rerun after the RG16 change.
- Cache-busting did not take effect.

Fix:

```powershell
$env:TILE_ENCODE_WORKERS = "16"
.venv/Scripts/python scripts/precompute_tiles.py 11
```

Then hard refresh.

### Stats disagree with visual layer at low radius

Likely causes:

- `build_outputs.py` or `precompute_viewport_stats.py` has not been rerun.
- Frontend is serving stale `pop_cumulative.json` or viewport binaries.

Fix:

```powershell
.venv/Scripts/python scripts/build_outputs.py
.venv/Scripts/python scripts/precompute_viewport_stats.py
.venv/Scripts/python scripts/validate_outputs.py
```

### Tile generation is slow

The expensive phase is rasterizing dense population rows and encoding hundreds of thousands of PNGs. Geometry caches avoid repeated reprojection work, but they do not avoid rewriting PNGs after an encoding change.

Expected behavior:

- Empty/low-population rows are fast.
- Dense latitude bands are slow.
- Later sparse rows taper hard and finish quickly.

### Older backend code seems contradictory

Some API/deck.gl code may still exist in the repo from an earlier implementation. The current working app path is the static MapLibre custom-layer path documented here.
