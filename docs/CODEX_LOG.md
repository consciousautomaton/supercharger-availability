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
