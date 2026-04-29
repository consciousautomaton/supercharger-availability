# Tesla Supercharger Global Coverage

What fraction of humanity lives within X km of a Tesla supercharger?

End goal: GitHub Pages static site with an interactive radius slider showing the coverage map and live population stat.

## Hardware

RTX 4050 6GB VRAM, i7 13th gen (14 cores), 16GB RAM, Windows 11, CUDA 13.1.
Python venv: `.venv/` — activate with `.venv/Scripts/Activate.ps1`.
Key packages: `cupy-cuda13x`, `rasterio`, `pyproj`, `numpy`, `folium`, `reverse_geocoder`.

## Data

| File | What it is |
|------|-----------|
| `cuaapi.json` | Tesla API scrape — 21,553 global locations, 5,936 open superchargers |
| `GHS_POP_E2030_GLOBE_R2023A_54009_100_V1_0.tif` | GHS-POP 2030, 100m global raster, Mollweide (ESRI:54009), nodata=-200 |
| `GHS_POP_GLOBE_R2023A_input_metadata.xlsx` | Metadata for the TIF |

## Key outputs (generated)

| File | What it is |
|------|-----------|
| `pop_histogram.npy` | float64[3000] — world population per integer km distance bucket |
| `pop_cumulative.npy` | Cumulative sum of histogram — direct input to web slider |
| `min_dist.bin` | EU float32 distance raster, 4000×4000, bounds in `raster_meta.json` |
| `global_min_dist.bin` | Global uint16 distance raster, 7200×3400 (85°S–85°N, 180°W–180°E) |
| `pop_cumulative.json` | JSON array[501] — cumulative population per km radius (0–500 km) |
| `global_raster_meta.json` | Bounds/resolution metadata for `global_min_dist.bin` |

## Scripts

**`global_coverage.py`** — main analysis. Streams the TIF in 4096px tiles across 10 CPU threads, runs CUDA haversine kernel in 40M-point batches. Runtime ~3 min.

```
.venv/Scripts/python global_coverage.py
```

**`eu_coverage.ipynb`** — EU-only coverage map. Generates `min_dist.bin` and `coverage_map.html`. Run in Jupyter/VS Code.

**`make_notebook.py`** — helper that regenerates `global_coverage.ipynb` from Python source. Not needed unless the notebook needs updating.

## Architecture

**Distance computation:** WGS84 haversine via a single CUDA `RawKernel` — one thread per grid point, inner C loop over all chargers. Single kernel launch, no Python overhead in hot path.

**Population sampling:** Population raster stays in its native Mollweide CRS. Pixel centres converted to WGS84 via pyproj for haversine. No reprojection of the raster (avoids interpolation error across 65B pixels).

**Web frontend:** `index.html` — Leaflet + custom WebGL layer. Loads `global_min_dist.bin` (R16UI texture, ~49 MB) and `pop_cumulative.json` via `fetch()`. WebGL fragment shader does Mercator→equirectangular unproject + bilinear interp + threshold against slider uniform. Instant on any device, no backend needed, GitHub Pages compatible.

To preview locally: `.venv/Scripts/python -m http.server 8000` then open `http://localhost:8000`.

## Key results

| Radius | World population covered |
|--------|------------------------|
| 10 km  | 9.99%  (~854M people)  |
| 25 km  | 15.32% |
| 50 km  | 18.96% |
| 100 km | 22.80% |
| 200 km | 28.13% |
| 500 km | 46.81% |

~53% of humanity (4.5B people) live more than 500 km from any Tesla supercharger.

## Next steps

1. Deploy to GitHub Pages (repo needed, then push `index.html`, `global_min_dist.bin`, `pop_cumulative.json`)
2. Per-country coverage breakdown (needs Natural Earth country polygons)
3. Clean up stale files (`Untitled-1.*`, `desup.*`, etc.)