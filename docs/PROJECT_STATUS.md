# Project Status

Last updated: 2026-05-02

## Current Product State

The project is a static MapLibre web app for exploring access to Tesla's Supercharger network.

The app can currently answer:

- what share of the world's population is within a selected distance of a Tesla Supercharger,
- what share of a selected country's population is within that distance,
- how many Tesla Superchargers are in the selected country,
- rough Supercharger density as people per Supercharger,
- where the underlying 100 m population cells are on the map.

The current UI is split into:

- interaction panel: search, country selector, distance slider/input, display mode,
- results panel: country access, country Supercharger stats, world access,
- map legend panel: population-cell colors and data notes.

## Current Data State

Generated country stats are present:

```text
frontend/countries.geojson
frontend/country_stats.json
```

Road-distance data has shipped for Germany:

```text
data/pixel_road_distances_DEU.npz
data/pixel_distances_road.npz
frontend/tiles_road/
frontend/pop_cumulative_road.json
frontend/viewport_manifest_road.json
frontend/country_stats_road.json
```

The merged road-distance source is generated with:

```powershell
.venv/Scripts/python scripts/merge_road_distances.py
```

Road visual tiles are sparse regional overrides, generated with:

```powershell
.venv/Scripts/python scripts/precompute_road_region_tiles.py --region DEU --workers 1 --max-pending 2
```

The frontend includes a Great-circle / Road toggle; the road option is labeled Germany-only until more regional OSM extracts are computed. In road mode, `frontend/tiles_road/` overrides Germany pixels and the app falls back to `frontend/tiles/` elsewhere.

Do not run the full global `scripts/precompute_tiles.py 11 --source road` path for the current Germany-only road dataset on the 16 GB laptop. It can exhaust memory/IO because it duplicates the global tile pyramid.

Latest Germany road override tile build:

```text
Region rows: 7,047,197
Finite road rows: 6,555,795
Great-circle fallback rows in region file: 491,402
Active z11 tile rows: 647..717 (71 rows)
Override tiles written:
  z0=1, z1=1, z2=1, z3=1, z4=1, z5=4, z6=6,
  z7=20, z8=66, z9=248, z10=891, z11=3,415
```

Country boundaries use Natural Earth Admin 0 at `10m` detail.

Manual charger overrides have been applied successfully:

```text
Loaded 140 charger overrides
Applied 140 charger overrides
Assigned chargers: 7,854 / 7,854
```

This means every extracted Tesla Supercharger is assigned to a country in the current country stats.

Unassigned population remains:

```text
Assigned country population: 8,404,231,351 / 8,480,668,252
```

That leaves about 76.4M people unassigned to a country polygon. The missing population is mostly coastal, island, border, and territory geometry mismatch. Review exports exist:

```text
frontend/unassigned_population_bins.csv
frontend/unassigned_population_bins.geojson
```

These are binned review files, not final stats inputs.

## Important Interpretation Notes

Country stats use Natural Earth sovereign/admin boundaries. Some countries include overseas territories. For example, Natural Earth `France` includes overseas territories, so France may not reach 100% at 500 km even though metropolitan France is dense with Superchargers.

The current UI should be interpreted as:

```text
sovereign-country/territory geometry according to Natural Earth
```

not necessarily:

```text
mainland-only country
```

This is acceptable for now, but should be documented in the UI and README.

## Current Pipeline

Full pipeline:

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

Country-only rebuild:

```powershell
.venv/Scripts/python scripts/precompute_country_stats.py
```

Charger override review:

```powershell
.venv/Scripts/python scripts/export_unassigned_chargers.py
.venv/Scripts/python -m http.server 8001 --directory frontend
```

Open:

```text
http://127.0.0.1:8001/review_unassigned_chargers.html
```

Population unassigned-bin export:

```powershell
.venv/Scripts/python scripts/export_unassigned_population.py
```

Optional bin size:

```powershell
$env:UNASSIGNED_POP_BIN_DEG = "0.05"
.venv/Scripts/python scripts/export_unassigned_population.py
```

## Files Added Recently

```text
scripts/precompute_country_stats.py
scripts/export_unassigned_chargers.py
scripts/export_unassigned_population.py
frontend/review_unassigned_chargers.html
frontend/countries.geojson
frontend/country_stats.json
frontend/unassigned_chargers.csv
frontend/unassigned_chargers.geojson
frontend/unassigned_population_bins.csv
frontend/unassigned_population_bins.geojson
```

## Known Technical Limits

- Country population assignment is by source-cell center.
- Road distance is currently region-scoped. Germany has road distances; cells outside computed road regions fall back to great-circle distance.
- Country boundaries are Natural Earth polygons, not official national statistical boundaries.
- Some country features include overseas territories.
- About 76M people remain unassigned to any country polygon in the current country stats.
- Charger country assignment uses spatial polygons plus manual overrides.
- Country stats do not currently support mainland-only variants.
- The UI no longer displays viewport stats, though viewport stat files/scripts still exist.
- Native coverage tile max is z11; zooming beyond z11 magnifies z11 textures.

## Good Next Steps

High-impact next work:

1. Compute additional road-distance regions and rerun the merge/build pipeline.
2. Add country ranking/table view:
   - highest access at 50 km,
   - lowest access,
   - people per Supercharger,
   - Superchargers per million people.
3. Add optional mainland/territory variants for countries like France.
4. Add reproducible population override rules if the remaining unassigned population matters.
5. Plan hosting/package strategy, because the tile directories may be too large for plain GitHub Pages depending on final size.

Deferred aesthetic work:

- stable population heatmap channel in tile blue channel,
- native z12/z13 tile experiments,
- refined color palettes,
- richer hover details.

Deferred data-expansion work:

- non-Tesla chargers,
- charger power levels,
- historical Supercharger snapshots.
