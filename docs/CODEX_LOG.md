# Codex Log

## 2026-05-07 — V2 handoff initialized

What I did:
- Read the current V2 plan and source layout.
- Confirmed V2 is the only active track and older versions are not design inputs.
- Added `docs/CODEX_BRIEF.md` as the local handoff file for future Codex sessions.

Decisions:
- Treat current dirty visual/frontend files as pre-existing user/Claude work. I will not stage or modify them unless working on an explicit visual task.
- Start with Phase 3 ingest work because Tesla and BNetzA are already wired and the next missing datasets can be added in separate, testable units.

Skipped:
- No WebGPU or visual work yet. Those require a clean baseline and separate commits.

Blocked:
- Nothing yet.

## 2026-05-07 — IRVE ingest

What I did:
- Added `v2/scripts/ingest_irve.py` for the official French IRVE consolidated CSV.
- The script downloads the 2026-05-06 resource via `curl.exe`, caches it under `data/`, aggregates point-of-charge rows into station records, drops Tesla rows, and writes `v2/frontend/public/data/chargers_irve.json`.
- Wired `chargers_irve.json` into `loadAllStations()` as an optional source, so the frontend still works before the IRVE output exists.
- Ran the ingest successfully. Output: 63,217 station records from 224,577 raw rows. Dropped 9,063 Tesla rows. Fast / slow / unknown: 18,204 / 45,013 / 0. Missing open date: 22,146.

Decisions:
- Aggregated by `id_station_itinerance` because the frontend currently renders stations, not individual plugs.
- Used max `puissance_nominale` for station `power_kw`, earliest `date_mise_en_service` for station `open_date`, and distinct point IDs for `stall_count`.
- Gitignored large national JSON outputs while keeping the small Tesla output trackable.

Skipped:
- No deeper station/operator cleanup beyond light canonicalization. The raw operator fields are carried through for later inspection.

Blocked:
- Nothing.

## 2026-05-07 — Network catalog script

What I did:
- Added `v2/scripts/build_network_catalog.py` to group raw operator strings into canonical network groups for future UI filters.
- Added typed frontend loading for `network_catalog.json`.
- Ran the build successfully. Raw network labels: 11,786. Canonical entries: 11,706. The limited reduction is expected because most BNetzA labels are genuinely long-tail local operators.
- Extended the data validator to check network catalog shape and duplicate IDs.

Decisions:
- Canonicalization is heuristic and non-destructive: grouped networks keep their top raw labels for auditability.
- Long-tail raw labels remain separate `raw:*` canonical entries rather than being thrown into one opaque "Other" bucket. The UI can decide how aggressively to hide the tail.

Skipped:
- No visible network dropdown changes yet.

Blocked:
- Nothing expected.

## 2026-05-07 — Coverage prototype script

What I did:
- Added `v2/scripts/compute_coverage_prototype.py` for coarse covered-population sanity checks on the 0.25° grid.
- Smoke-tested one scenario: `fast_only`, year 2026, radius 50 km. Runtime: 4.5s. Rough result: 586.4M / 8.48B people covered, 6.92%, using 53,320 fast stations. This is prototype-only and not final coverage math.

Decisions:
- This is explicitly not the final coverage engine. It uses 2030 coarse population for every year and computes great-circle distance to currently loaded station records.
- It is intended to generate rough numbers for debugging station filters and timeline behavior before the WebGPU path exists.

Skipped:
- Did not run the full scenario matrix because it can be slower than the other data scripts and the output would be provisional.

Blocked:
- Nothing expected.

## 2026-05-07 — UI data wiring

What I did:
- Wired generated V2 data into the current frontend panel without changing the core visual direction.
- Region dropdown now populates from `country_catalog.json` and can filter visible charger points by country.
- Added `ultra_fast` dataset support.
- Reused shared station filtering logic for both stats and globe points.
- Stats panel now shows selected scope, visible station count, source station counts, fast/ultra counts, EV stock where available, and top network breakdown.

Decisions:
- Normalized station country fields in-memory after loading the country catalog, so Tesla source country names can participate in ISO-A3 country filtering.
- Kept UI scope conservative: no country fly-to, no country border rendering, no new globe layer. Those are visual tasks and should be handled separately.

Skipped:
- No WebGPU coverage overlay yet.
- No single-network dropdown yet; the network catalog is ready for it, but it needs a more deliberate UI control.

Blocked:
- Nothing.

## 2026-05-07 — Single-network filter

What I did:
- Added a `single_network` dataset filter and network dropdown.
- Added shared frontend canonical-network mapping for station filtering.
- Populated the dropdown from `network_catalog.json`, limiting visible choices to canonical non-raw networks.

Decisions:
- Kept raw long-tail operators out of the dropdown for now. They remain in `network_catalog.json` and can be exposed through search later.
- The single-network filter applies after country/year/mode filters, matching the rest of the app state model.

Skipped:
- No network logos or branded styling.

Blocked:
- Nothing.

## 2026-05-07 — URL state

What I did:
- Added URL hash persistence for `region`, `dataset`, `mode`, `year`, `distance`, and `network`.
- Added hash parsing on initial load and hashchange sync back into the controls.

Decisions:
- Used `history.replaceState` for control changes so dragging/selecting does not spam browser history.
- Kept only non-default state in the URL to keep links short.

Skipped:
- No share button yet.

Blocked:
- Nothing.

## 2026-05-07 — Country catalog script

What I did:
- Added `v2/scripts/build_country_catalog.py` to generate a compact country dropdown/catalog file from Natural Earth.
- Added typed frontend loading for `country_catalog.json`.
- Ran the build successfully. Output: 236 countries, 53 with station data, 28 with EV stock data, 89.1 KiB.
- Extended the data validator to check country catalog shape and duplicate ISO codes.

Decisions:
- Catalog includes ISO-A3, display names, continent/region labels, bbox, center point, and whether generated station / EV stock data exists.
- Bbox center is sufficient for first camera targeting; a later visual pass can improve fly-to framing.

Skipped:
- No country border rendering or dropdown UI wiring yet.

Blocked:
- Nothing expected.

## 2026-05-07 — V2 data runbook

What I did:
- Added `v2/scripts/build_v2_data.py`, an orchestrator for the safe V2 data pipeline.
- Added `docs/V2_RUNBOOK.md` with rebuild, validation, and frontend-check commands.
- Smoke-tested the orchestrator with `--skip-ingests --skip-population`. It ran EV stock, station summary, country catalog, and validation successfully in 5.1s.

Decisions:
- Nobil is skipped by default because it requires a key or cached dump. It can be included with `--include-nobil`.
- Population prototype is included by default because the current build is fast and bounded; it can be skipped with `--skip-population`.

Skipped:
- Did not run the full ingest+population orchestrator in this edit step because all individual steps were already run and validated during this session.

Blocked:
- Nothing.

## 2026-05-07 — UK NCR checked and skipped

What I did:
- Checked current UK National Chargepoint Registry availability.
- Confirmed the registry was decommissioned on 2024-11-28 and the archive is available only on request.

Decisions:
- Skipped UK NCR for this shift, matching the V2 brief's instruction not to block on manual archive access.

Skipped:
- No UK ingest script yet. A future script should be written once the user has an archive file or a replacement open-data source with install dates.

Blocked:
- Archived NCR data requires a manual request.

## 2026-05-07 — End of shift

What is working end-to-end:
- V2 handoff docs are in place.
- Tesla, BNetzA, IRVE, and AFDC are represented in the frontend loader.
- IRVE output exists locally: 63,217 France stations.
- AFDC output exists locally: 86,137 US/Canada stations.
- Frontend typecheck and build pass after adding the optional sources.

Known broken / incomplete:
- Nobil is scaffolded but has no output until the user provides `NOBIL_API_KEY`, `data/nobil_api_key.txt`, or `data/nobil_datadump.json`.
- UK NCR has no ingest because archive access requires a manual request.
- Existing visual/frontend files (`v2/frontend/src/globe/setup.ts`, `v2/frontend/src/main.ts`, `v2/frontend/src/styles.css`) were dirty before this shift and were intentionally not touched.
- WebGPU compute, population layers, EV adoption ingest, and the V2 analytical UI wiring are still pending.

What should happen next:
- Decide whether to continue with Phase 5 via IEA/OWID EV stock and GHS-POP epoch scaffolding, or first wire the expanded station sources into UI filters and network summaries.
- If Nobil matters for the next demo, get an API key or raw datadump and rerun `v2/scripts/ingest_nobil.py`.

## 2026-05-07 — EV stock ingest script

What I did:
- Added `v2/scripts/ingest_iea_ev_stock.py` for country/year electric car stock.
- Ran the ingest successfully. Output: 376 country-year rows, 30 countries, 2010-2024. Latest-year total: 56,907,780 electric cars across 30 countries in 2024.
- Added typed frontend loading via `loadEVStockCountryYear()`.

Decisions:
- Used Our World in Data's open CSV derived from IEA Global EV Outlook 2025 because the official IEA XLSX is listed as free but direct download is login-gated.
- Output keeps the planned `{bev, phev, total}` shape, but sets `bev` and `phev` to null because the OWID chart exposes total electric car stock only.
- Wrote a companion metadata JSON so the frontend can cite the data honestly later.

Skipped:
- Did not attempt browser-authenticated IEA XLSX download.

Blocked:
- BEV/PHEV stock split requires either direct IEA workbook access or a separate data source.

## 2026-05-07 — Station summary script

What I did:
- Added `v2/scripts/build_station_summary.py` to summarize loaded station datasets by country, network, power tier, and open year.
- Added TypeScript types and `loadStationSummary()` for later UI consumption.
- Ran the summary successfully. Output: 263,697 stations summarized from Tesla, BNetzA, IRVE, and AFDC. Countries: 55. Network labels: 11,786. Unmapped country records: 0. Output size: 5.7 MiB.

Decisions:
- This script summarizes station records, not connector counts or population coverage.
- It normalizes source country fields to ISO-A3 using Natural Earth names plus manual aliases for common charger-source country labels.
- It preserves long-tail network names instead of collapsing them prematurely; UI can decide how to group "Other" later.

Skipped:
- Did not wire the summary into visible UI yet because that would be a visual/layout task and should be isolated later.

Blocked:
- Nothing expected.

## 2026-05-07 — Population layer prototype script

What I did:
- Added `v2/scripts/build_population_layer.py` for a first coarse global population grid.
- Added `PopulationGridMeta` and `loadPopulationGridMeta()` for later frontend/WebGPU work.
- Ran the 2030 prototype build successfully. Output: `pop_025deg_world_2030.bin` at 1440x720 float32 cells, 4.0 MiB. Source cells: 370,272,898. Population sum: 8,480,668,252. Nonzero output cells: 156,933 / 1,036,800. Skipped source cells: 0. Runtime: 21.6s.

Decisions:
- Used existing mmap-friendly `data/npy/lons.npy`, `lats.npy`, and `pop.npy` arrays instead of rereading the 6.6 GB GeoTIFF directly.
- The current output target is a 0.25-degree global equirectangular grid for 2030. This is a WebGPU prototype payload, not the final 1 km / 100 m V2 population layer.
- Script processes in chunks and prints progress every ~25M source cells to avoid the previous black-box long-script problem.

Skipped:
- Did not implement downloads for 2010/2015/2020/2025 GHS-POP epochs yet.
- Did not generate 100 m regional streams yet.

Blocked:
- Nothing expected for the local 2030 prototype build.

## 2026-05-07 — Compute scaffolding

What I did:
- Added non-visual frontend compute/data modules:
  - `src/data/filters.ts` for reusable station filtering by dataset/mode/year.
  - `src/compute/webgpu.ts` for WebGPU support/device checks.
  - `src/compute/populationGrid.ts` for loading the 0.25° prototype population binary.
  - `src/compute/spatialIndex.ts` for a basic lat/lon charger grid index and haversine lookup.
  - `src/compute/coveragePrototype.ts` for CPU prototype coverage stats on the coarse grid.

Decisions:
- Did not wire these into UI yet, because current visual files are dirty and visual/layout changes should be isolated.
- The CPU prototype is intentionally for correctness/debug only. It is not the final WebGPU path and will be too slow for repeated interactive full-world updates.

Skipped:
- No shader or globe overlay yet.
- No Rust/WASM crate yet.

Blocked:
- Nothing.

## 2026-05-07 — V2 data validator

What I did:
- Added `v2/scripts/validate_v2_data.py` to validate generated V2 data files and print explicit statistics.
- Ran the validator successfully. Current totals: 263,697 station records, 0 validation failures. Station files: Tesla 8,962; BNetzA 105,381; IRVE 63,217; AFDC 86,137; Nobil missing optional. EV stock: 30 countries, 376 rows, 2010-2024. Station summary counts match.
- Extended the validator to check the 0.25° population grid output size and metadata population sum.

Decisions:
- Validator checks required station files, optional national files, EV stock, and station summary consistency.
- It reports row counts, country/network counts, missing open dates, duplicate IDs, kind distribution, and summary count mismatches.

Skipped:
- It does not validate future coverage/population math because those layers are not built yet.

Blocked:
- Nothing expected.

## 2026-05-07 — Nobil ingest scaffold

What I did:
- Added `v2/scripts/ingest_nobil.py` for Norway's Nobil datadump API.
- Wired `chargers_nobil.json` into the frontend loader as an optional source.

Decisions:
- The script accepts either `NOBIL_API_KEY`, `data/nobil_api_key.txt`, or a pre-downloaded `data/nobil_datadump.json`.
- It exits with a clear blocked message and no output when no key/cache is present.
- Kept parsing defensive because Nobil payloads can vary between API versions; the script handles common station/position/connector field names and logs output counters.

Skipped:
- Did not use the public documentation example API key. Nobil says users should register and accept the CC BY terms, so this should wait for the user's own key or dump.

Blocked:
- Nobil datadump requires an API key or cached JSON file.

## 2026-05-07 — AFDC ingest

What I did:
- Added `v2/scripts/ingest_afdc.py` for the AFDC/NLR Alternative Fuel Stations API.
- Wired `chargers_afdc.json` into the frontend loader as an optional source.
- Ran the ingest successfully. Output: 86,137 station records from 94,801 raw API records. Dropped 8,664 Tesla rows. Fast / slow / unknown: 14,374 / 71,762 / 1. Missing open date: 3.

Decisions:
- Used the current `developer.nlr.gov` host because NREL developer docs now redirect there and warn that the old `developer.nrel.gov` domain is being retired.
- Requested public, available electric stations in both US and Canada with `limit=all`.
- Dropped Tesla networks for dedup against supercharge.info.
- AFDC does not expose reliable station-level DC power in this endpoint, so DC fast stations are conservatively assigned `power_kw = 50` and AC stations use coarse Level 1 / Level 2 defaults.

Skipped:
- No attempt to infer higher DC power from network names or external listings. This keeps the source deterministic and avoids false precision.

Blocked:
- Nothing.
