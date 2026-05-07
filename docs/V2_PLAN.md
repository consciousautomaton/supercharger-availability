# V2 Plan

Status: locked-in vision, build starting 2026-05-07.

V1 docs (still describe the running app): [README.md](../README.md), [docs/ARCHITECTURE.md](ARCHITECTURE.md), [docs/PROJECT_STATUS.md](PROJECT_STATUS.md).

## Why V2

V1 ships a working global static-tile map showing % of population within X km of a Tesla Supercharger today (great-circle distance), with country breakdowns. It looks good and the math is correct, but it's a single-snapshot calculator without a story or comparative context.

V2 expands the project into a tool that answers a richer question:

> How did public EV charging infrastructure grow across the world, and how does that compare to EV adoption?

Audience: curious EV enthusiast spending 5–15 minutes; secondary, a Tesla recruiter use.

The shift is from "Tesla coverage today" to "EV infrastructure rollout, multi-network, with EV-adoption as a counterpart axis."

## Locked-in vision

**Shape.** Tool with dropdown-driven UI. Honest about data coverage — the `Timeline` mode is offered only for region/dataset combinations where authoritative install-date data exists. If a user picks a region without temporal data, the UI tells them so and offers alternatives ("Tesla globally", "Today snapshot").

**No autoplay/scrollytelling.** No MP4 intro. No story-mode set pieces. Tool-shape from first frame.

## Architecture

- **Globe** as the single primary visual idiom (globe.gl on Three.js). Continuous from globe-view to street-zoom; flat map is *not* a separate idiom.
- **Raw data + GPU compute.** Charger list shipped to client; per-pixel coverage computed on GPU via WebGPU. No fallback (target environment is modern Chromium / Safari on Mac).
- **Rust → WASM** for spatial indexing (KD-tree or grid hash over chargers).
- **Multi-resolution data.** 1 km global grid always loaded (~5–10 MB). 100 m regional cells streamed on demand for zoom-in. Statistics always computed at 100 m and shipped as small JSON.
- **State-aware UI.** Dropdowns disable invalid combinations and explain why.

## Data sources

Charger sources merged into a unified schema with a `date_source` attribution field per record:

| Source | Coverage | Date field | Quality |
|---|---|---|---|
| supercharge.info | Tesla, global | `open_date` | Real install dates back to ~2012 |
| Bundesnetzagentur Ladesäulenregister | Germany | `Inbetriebnahmedatum` | Authoritative; already on disk |
| IRVE (`transport.data.gouv.fr`) | France | `date_mise_en_service` | Authoritative; etalab license |
| Nobil (Enova) | Norway | `Created` | Reputed cleanest national dataset |
| NREL AFDC | US + Canada | `open_date` | Authoritative; public domain |
| UK NCR archive | UK | (per archive) | Decommissioned 2024-11-28; archive available on request |
| Open Charge Map | Global, rest-of-world | `DateCreated` | **Listing date, not install date.** Used only for `Today` mode where authoritative source absent. |

EV stock per country-year: IEA Global EV Data Explorer (annual aggregate).
Population: GHS-POP, year-matched epochs (2010 / 2015 / 2020 / 2025 / 2030), interpolated.

## Stats surfaced

- Population coverage (% within X km of fast charging) — temporal where data allows.
- Chargers per million people, over time.
- EVs per million people, over time.
- Network market share within a country.
- Power-weighted growth (kW capacity).
- Charging deserts (populated areas still > 100 km from fast charging).

## Out of scope (V2)

Documented here so we don't drift back into them:

- Road-distance routing (V1 had it, archived; inaccurate at the scale we computed).
- Story-mode autoplay or scrollytelling. No MP4 intro.
- Per-charger road-trip simulator ("if you bought an EV in 2014").
- Time-scrubber sparklines (revisit if the tool feels dry once built).
- OSM cross-validation of dates (revisit if data pipeline goes smoothly and time remains).
- Published-dataset deliverable on Zenodo (the pipeline produces a clean unified table; *publishing* it is a later decision, not blocking).

## Build order

**Phase 0 — Cleanup**
- Archive road code under `scripts/archive/road/`.
- Gitignore road data files (`data/*.npz`, `frontend/tiles_road/`, `frontend/*_road.json`).
- Leave V1 frontend running for reference.

**Phase 1 — Architecture skeleton**
- New frontend page (separate from V1 `index.html`) with globe.gl globe + dropdown control panel scaffold.
- WebGPU compute pipeline: charger buffer → per-pixel coverage texture.
- Rust crate compiled to WASM for spatial indexing.
- Multi-resolution data loading: 1 km world grid + 100 m on-demand stub.

**Phase 2 — First wired dataset (Tesla global)**
- Ingest `supercharge.info` JSON.
- Define unified schema: `lat, lon, network, power_kw, stall_count, open_date, close_date, date_source`.
- End-to-end: scrub time 2012 → today on the globe, see Tesla bloom.

**Phase 3 — National authoritative sources**
- Bundesnetzagentur (already on disk).
- IRVE.
- Nobil.
- AFDC.
- UK NCR archive (request access; not blocking).
- Per-source ingest scripts producing unified-schema records.
- Lat/lon proximity dedup against Tesla layer.

**Phase 4 — Global "Today" spine**
- Open Charge Map ingest.
- Used only when `Mode=Today` and no authoritative source covers the region.

**Phase 5 — EV adoption + year-matched population**
- IEA Global EV Data Explorer ingest (country-year EV stock).
- GHS-POP year-matched epochs, linear interpolation between 5-year anchors.
- Combined surface stats: chargers per million people + EVs per million people, both temporal.

**Phase 6 — Stats and polish**
- Country selector + comparison.
- Network share, power-weighted growth, charging deserts.
- Color/typography pass.
- Hover detail on individual chargers.
- Shareable URL state (URL hash encodes dropdown selections).

**Phase 7 — Hosting**
- Deploy to Cloudflare Pages (or GitHub Pages if total payload fits).
- Verify load times, mobile/Safari behavior.

## Status

Last updated: 2026-05-07.

- [x] **Phase 0 — Cleanup.** Road code moved to `scripts/archive/road/`. `.gitignore` updated for `data/*.npz`, `data/*.osm.pbf`, `frontend/tiles_road/`, `frontend/*_road.json`, `frontend/tiles/`, `data/npy/`. V1 frontend untouched.
- [~] **Phase 1 — Architecture skeleton.** Done: `v2/frontend/` Vite + TS + three-globe, OrbitControls, points layer, dropdown control panel, dark stats panel. **Pending: WebGPU compute pipeline (`src/compute/` is empty), Rust→WASM spatial indexing crate, multi-resolution 1 km / 100 m loading.** Currently rendering all stations as plain three-globe points.
- [x] **Phase 2 — Tesla dataset.** `v2/scripts/ingest_supercharge.py` → `chargers_tesla.json` (8,962 stations, 2.9 MB). REAL_STATUSES filter, normalized to unified schema.
- [~] **Phase 3 — National authoritative sources.**
  - [x] Bundesnetzagentur (Germany): `ingest_bnetza.py` → `chargers_bnetza.json` (105,381 stations, 36 MB). Tesla rows dropped to dedup against supercharge.info.
  - [x] IRVE (France): `ingest_irve.py` → `chargers_irve.json` (63,217 stations, 21.9 MB). Tesla rows dropped to dedup against supercharge.info.
  - [~] Nobil (Norway) — script added; blocked until API key or cached datadump is available.
  - [ ] AFDC (US/Canada) — NREL public-domain feed, `open_date`.
  - [ ] UK NCR archive — request-access; not blocking.
  - Frontend pipeline is source-agnostic: each new ingest auto-merges via `loadAllStations()` once added to the loader.
- [ ] Phase 4 — OCM "Today" spine
- [ ] Phase 5 — EV adoption + year-matched population
- [ ] Phase 6 — Stats and polish
- [ ] Phase 7 — Hosting

Schema additions made during Phase 3 wiring (already in `v2/frontend/src/data/types.ts`):

- `DatasetFilter = "fast_only" | "all_public" | "tesla_only"` — UI filter.
- `ChargerKind = "fast" | "slow" | "unknown"` — inferred from `power_kw` if not set explicitly (>= 50 kW DC = fast).
- `open_to_others?: boolean` — Tesla sites opened to non-Tesla EVs render orange vs Tesla-only red.

Each phase ends in a shippable artefact. If the week runs short, we ship at whichever phase is complete.
