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
