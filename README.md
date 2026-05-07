# Tesla Supercharger Access

Interactive static web map for answering:

> What fraction of the world's population lives within X km of an open Tesla Supercharger?

The app renders populated 100 m source cells from GHS-POP 2030 and colors the cells that are within the selected radius of the nearest open Supercharger. It shows global access, country-level access, Supercharger counts by country, and charger density metrics.

## Current Architecture

The current app is a static frontend served from `frontend/`.

- Map: MapLibre GL JS with a CARTO Voyager raster basemap.
- Coverage layer: MapLibre custom WebGL layer.
- Coverage tiles: precomputed PNG pyramid under `frontend/tiles/{z}/{x}/{y}.png`.
- Optional road-distance tiles: sparse regional override PNGs under `frontend/tiles_road/{z}/{x}/{y}.png`.
- Global stats: `frontend/pop_cumulative.json`.
- Country stats: `frontend/country_stats.json` and `frontend/countries.geojson`.
- Charger markers: `frontend/chargers.json`.
- Viewport stats still exist as generated files, but the current UI no longer shows them.

The old FastAPI/deck.gl path may still exist in the repo, but the current working path is the static tile pipeline.

## Data Sources

Required raw inputs under `data/`:

- `GHS_POP_E2030_GLOBE_R2023A_54009_100_V1_0.tif`
  - GHS-POP 2030 global population raster.
  - Native CRS: Mollweide / `ESRI:54009`.
  - Nominal resolution: 100 m source cells.
- `tesla_scrape.json`
  - Raw Tesla location scrape.
  - The charger extraction script keeps open Superchargers, including entries Tesla tags in non-obvious ways.

Generated intermediate data:

- `data/populated_pixels.npz`
  - Populated source cells only.
  - Arrays: `lons`, `lats`, `pop`, `x_moll`, `y_moll`.
- `data/chargers.npz`
  - Open Supercharger positions.
  - Arrays: `lats`, `lons`.
- `data/pixel_distances.npz`
  - Population cells plus nearest-charger distance.
  - Arrays: `lons`, `lats`, `pop`, `min_dist_km`.

Generated frontend data:

- `frontend/tiles/`
  - Coverage raster tile pyramid.
- `frontend/pop_cumulative.json`
  - Global covered population by integer radius.
- `frontend/viewport_pop_total.bin`
  - Total population per 1 degree lat/lon bin.
- `frontend/viewport_pop_covered.bin`
  - Covered population per 1 degree lat/lon bin and 1 km radius bucket.
- `frontend/viewport_manifest.json`
  - Shape and encoding metadata for viewport stat binaries.
- `frontend/countries.geojson`
  - Natural Earth Admin 0 country boundaries used for country selection.
- `frontend/country_stats.json`
  - Country-level total population, covered population by radius, and Supercharger counts.
- `frontend/unassigned_chargers.*`
  - Review exports for chargers that could not be spatially assigned to a country before manual overrides.
- `frontend/unassigned_population_bins.*`
  - Binned review exports for population cells that could not be spatially assigned to a country.

## Build Pipeline

Run from the project root in PowerShell.

```powershell
.venv/Scripts/python scripts/extract_pixels.py
.venv/Scripts/python scripts/extract_chargers.py
.venv/Scripts/python scripts/compute_distances.py
$env:TILE_ENCODE_WORKERS = "16"
.venv/Scripts/python scripts/precompute_tiles.py 11
.venv/Scripts/python scripts/build_outputs.py
.venv/Scripts/python scripts/precompute_viewport_stats.py
.venv/Scripts/python scripts/precompute_country_stats.py
.venv/Scripts/python scripts/validate_outputs.py
```

Road-distance outputs are built side-by-side after per-region road files exist:

```powershell
.venv/Scripts/python scripts/merge_road_distances.py
.venv/Scripts/python scripts/precompute_road_region_tiles.py --region DEU --workers 1 --max-pending 2
.venv/Scripts/python scripts/build_outputs.py --source road
.venv/Scripts/python scripts/precompute_viewport_stats.py --source road
.venv/Scripts/python scripts/precompute_country_stats.py --source road
.venv/Scripts/python scripts/validate_outputs.py --source road
```

The current road dataset covers Germany. The frontend uses sparse road override tiles from `frontend/tiles_road/` and falls back to the existing `frontend/tiles/` great-circle tile wherever no road override pixel exists. Do not run a full global `precompute_tiles.py 11 --source road` on a 16 GB laptop; it duplicates almost the whole world tile pyramid and can exhaust memory/IO.

Most rebuilds do not need the full pipeline. If `pixel_distances.npz` already exists and only the tile encoding or frontend changed, rerun:

```powershell
$env:TILE_ENCODE_WORKERS = "16"
.venv/Scripts/python scripts/precompute_tiles.py 11
.venv/Scripts/python scripts/build_outputs.py
.venv/Scripts/python scripts/precompute_viewport_stats.py
.venv/Scripts/python scripts/precompute_country_stats.py
.venv/Scripts/python scripts/validate_outputs.py
```

`precompute_tiles.py` reuses geometry caches in `data/npy/`, so later tile rebuilds skip the expensive exact-footprint setup.

Country stats use Natural Earth Admin 0 boundaries. The script defaults to `10m` detail:

```powershell
.venv/Scripts/python scripts/precompute_country_stats.py
```

To use another Natural Earth scale:

```powershell
$env:COUNTRY_BOUNDARY_SCALE = "50m"
.venv/Scripts/python scripts/precompute_country_stats.py
```

Manual charger country corrections are read from:

```text
data/country_charger_overrides.csv
```

Expected columns:

```csv
charger_index,iso_a3,country_name
```

To create/review those overrides:

```powershell
.venv/Scripts/python scripts/export_unassigned_chargers.py
```

Then open:

```text
http://127.0.0.1:8001/review_unassigned_chargers.html
```

## Run The App

```powershell
.venv/Scripts/python -m http.server 8001 --directory frontend
```

Open:

```text
http://127.0.0.1:8001/index.html
```

Hard refresh after rebuilding tiles or frontend assets.

## Frontend Controls

- Search box: jump to a place using Nominatim.
- Country dropdown or map click: select a country and show country-level stats.
- Radius slider and numeric input: `0` to `500` km in 1 km steps.
- `Auto`: smooths the coverage layer only at lower/mid zooms, and only once radius is large enough that smoothing is visually appropriate.
- `Exact`: shows raw rasterized source-cell footprints without shader smoothing.
- Distance source: `Great-circle` or `Road (Germany only)`.

At high zoom, both modes preserve exact 100 m source-cell footprints.

The results panel currently shows:

- share of the selected country's population within the selected distance,
- number of people within/farther than the selected distance,
- Tesla Supercharger count in the selected country,
- people per Supercharger,
- global population access at the same distance.

## Important Semantics

Radius buckets use `ceil(distance_km)`.

That means:

- Radius `0 km` includes only cells whose nearest-charger distance is exactly zero.
- Radius `1 km` includes cells with `0 < distance <= 1`.
- Radius `50 km` includes cells with `distance <= 50`.

Tiles use 16-bit distance encoding:

- Red/green channels store a 16-bit distance code.
- Alpha stores population intensity.
- Blue is reserved.

This avoids the old 8-bit bug where distances under about 2 km collapsed to zero and made the `0 km` view show broad false coverage.

## More Detail

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the detailed design notes, file formats, cache behavior, correctness limits, and troubleshooting checklist.

See [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) for the current implementation status and near-term todo list.
