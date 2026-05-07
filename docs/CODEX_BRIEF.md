# Codex Brief — V2

Last updated: 2026-05-07.

This file is the working handoff for Codex sessions on V2. Older versions are abandoned: V1, the H3 Germany prototype, old road-distance work, and V1-era Claude plans are historical only. Do not use old frontend/backend/scripts as design guidance unless this file names a reusable raw data artifact explicitly.

## Goal

V2 is a globe-first tool for exploring how public EV charging infrastructure grew over time and how that compares with EV adoption. Tesla is one network among many, not the protagonist.

Primary audience:
- EV enthusiast: 5-15 minutes, analytical exploration.
- Secondary: Tesla recruiter: 30-90 seconds, clear emotional impact.

## Locked Decisions

- Globe-only experience. No flat-map fallback.
- Raw data plus WebGPU compute. No precomputed tile pyramid.
- No WebGPU fallback. Show an explicit unsupported-browser state instead.
- Multi-resolution data: 1 km global layer always loaded; 100 m regional data streamed on zoom-in.
- Stats should use the best available resolution, not visual approximations.
- Rust/WASM spatial index is planned for charger lookup acceleration.
- Vite + TypeScript + Three.js + three-globe. No React or Vue.
- Controls are dropdown-driven.
- Mode is `Today` / `Timeline`. Timeline is disabled when authoritative dates are unavailable.
- Country selection is via dropdown, not click-on-globe.
- Region filter: World / continent / country.
- Dataset filter: All public / DC fast >= 50 kW / Ultra-fast >= 150 kW / single network.
- Honest UX: missing data is explained directly instead of hidden.

## Visual Direction

Do not carry over V1's green/red coverage language or V1 panel layout. V2 uses a light, analytical globe design.

Core palette:
- Background: `#f4f5f8`
- Land: `#cfd2d8`
- Ocean: `#f4f5f8`
- Accent: `#1f7ff0`
- Atmosphere: `#7fb3ff`

Coverage:
- Today: covered cells get a soft blue tint with alpha driven by population density.
- Timeline: covered cells interpolate from pale blue for old coverage to saturated blue for recent coverage.
- Uncovered populated cells stay invisible. Empty space is the message.

Avoid:
- Green/red coverage palette.
- Country choropleth coverage fills.
- Autoplay/scrollytelling.
- Starfield, spinning globe, dark mode, Tesla logo, network logos.

Visual changes must be isolated in their own commits. Commit a clean baseline before changing CSS, colors, layout HTML, three-globe materials, shader code, or palette files.

## Current Status

Done:
- Phase 0 cleanup.
- Phase 1 skeleton: `v2/frontend/` Vite + TS + three-globe scaffold.
- Phase 2 Tesla ingest: `v2/scripts/ingest_supercharge.py`.
- Phase 3 partial: BNetzA ingest: `v2/scripts/ingest_bnetza.py`.

Pending:
- Phase 3 finish: IRVE, Nobil, AFDC. UK NCR is optional/request-gated.
- Phase 5: GHS-POP year-matched epochs, IEA EV stock, country/year population tables.
- Phase 1 leftover: WebGPU compute pipeline and Rust/WASM spatial index.
- Multi-resolution data layer.
- UI/UX wiring for region, dataset, mode, year, country, tooltips, and URL state.
- Documentation updates.

Skipped for now:
- Open Charge Map, because no API key is available.
- Hosting/deployment.
- Road-distance routing.

## Data Sources

Existing:
- `v2/frontend/public/data/chargers_tesla.json`
- `v2/frontend/public/data/chargers_bnetza.json`
- `data/GHS_POP_E2030_GLOBE_R2023A_54009_100_V1_0.tif`
- `data/ne_10m_admin_0_countries.geojson`

Planned:
- IRVE France: `transport.data.gouv.fr`, `date_mise_en_service`.
- Nobil Norway: `https://nobil.no/api/server/datadump.php`, registration may block.
- AFDC US/Canada: NREL API, `open_date`.
- GHS-POP epochs: 2010, 2015, 2020, 2025, 2030.
- IEA Global EV Data Explorer.

## Engineering Rules

- Append entries to `docs/CODEX_LOG.md` for every major task.
- Commit regularly with Conventional Commit messages and:
  `Co-Authored-By: Codex <noreply@openai.com>`
- Stage only files changed for the current task. The working tree may contain user changes.
- No remote pushes.
- No hosting setup.
- Scripts must print progress/statistics for long-running work.
- If a dataset field is missing, store `null`; do not infer false precision.
- If a malformed row is skipped, count it.
- Use plain Python scripts, not notebooks.

## Verification

Before marking a functional phase complete:
- Run the relevant ingest/build script successfully.
- Run `v2/frontend` typecheck/build when frontend code changes.
- Validate generated JSON shape against `ChargerStation` assumptions.
- Update `docs/V2_PLAN.md` status and add a `CODEX_LOG` entry.

