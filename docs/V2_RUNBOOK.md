# V2 Runbook

Last updated: 2026-05-07.

## Rebuild Data

Run the safe V2 data pipeline:

```powershell
.venv/Scripts/python v2/scripts/build_v2_data.py
```

This runs:
- Tesla ingest
- BNetzA ingest
- IRVE ingest
- AFDC ingest
- EV stock ingest
- station summary
- 0.25-degree 2030 population prototype grid
- country catalog
- data validator

Nobil is skipped by default because it requires an API key or cached dump:

```powershell
$env:NOBIL_API_KEY = "..."
.venv/Scripts/python v2/scripts/build_v2_data.py --include-nobil
```

For a fast derived-data rebuild without re-running source ingests:

```powershell
.venv/Scripts/python v2/scripts/build_v2_data.py --skip-ingests
```

For a fast metadata-only rebuild without the population grid:

```powershell
.venv/Scripts/python v2/scripts/build_v2_data.py --skip-population
```

## Validate Data

```powershell
.venv/Scripts/python v2/scripts/validate_v2_data.py
```

Current expected result:
- 263,697 station records.
- 0 validation failures.
- Nobil missing as optional.

## Frontend Checks

```powershell
cd v2/frontend
npm run typecheck
npm run build
```

The build currently emits a chunk-size warning because Three.js / three-globe are large. That warning is expected until code splitting is added.

## Coarse Coverage Prototype

Run one bounded scenario:

```powershell
.venv/Scripts/python v2/scripts/compute_coverage_prototype.py --datasets fast_only --years 2026 --radii 50
```

This uses the 0.25° 2030 population prototype grid. It is only for sanity-checking filters and rough magnitudes before the WebGPU coverage engine exists.
