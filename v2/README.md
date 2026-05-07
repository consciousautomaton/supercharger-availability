# V2 — EV Charging Rollout

Build directory for the V2 rewrite. See [docs/V2_PLAN.md](../docs/V2_PLAN.md) for vision, architecture, and phased build order.

## Layout

```
v2/
  frontend/      Vite + TypeScript + Three.js + three-globe
    src/
      main.ts            entry point
      globe/setup.ts     three-globe scene
      ui/controls.ts     dropdown bindings
      data/types.ts      unified schema
      compute/           (Phase 2+) WebGPU compute pipelines
    public/data/         (Phase 2+) ingested charger data
  scripts/       (Phase 2+) Python ingest scripts producing unified schema
  rust/          (Phase 1.5+) WASM crate for spatial indexing
```

V1 (the running tile-pyramid app) lives under `frontend/` and `scripts/` at the repo root and is left untouched until V2 reaches feature parity.

## Run

```powershell
cd v2/frontend
npm install
npm run dev
```

Open <http://127.0.0.1:5173>.

## Phase status

See checklist in [docs/V2_PLAN.md](../docs/V2_PLAN.md#status).
