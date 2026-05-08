import { createGlobe } from "./globe/setup";
import { attachPoints } from "./globe/points";
import {
  bindControls,
  populateNetworkOptions,
  populateRegionOptions,
} from "./ui/controls";
import {
  loadAllStations,
  loadCountryCatalog,
  loadEVStockCountryYear,
  loadNetworkCatalog,
  loadStationSummary,
} from "./data/loader";
import { filterStationsForState } from "./data/filters";
import {
  createCoverageGPU,
  type CoverageDispatchResult,
  type CoverageGPU,
} from "./compute/coverageGPU";
import { loadPopulationGrid2030 } from "./compute/populationGrid";
import {
  createCoverageLayer,
  type CoverageLayer,
} from "./globe/coverageLayer";
import type {
  AppState,
  ChargerStation,
  CountryCatalog,
  CountryCatalogEntry,
  EVStockCountryYear,
  StationSummary,
} from "./data/types";

const defaultState: AppState = {
  region: "world",
  dataset: "fast_only",
  mode: "today",
  year: 2026,
  distance_km: 50,
  network_id: null,
};

const container = document.getElementById("globe-container");
if (!container) throw new Error("#globe-container missing");

const { globe } = createGlobe(container);
const statsEl = document.querySelector<HTMLElement>("#stats-panel");

let currentState: AppState = readStateFromHash(defaultState);
let updatePoints: ((state: AppState) => number) | null = null;
let allStations: ChargerStation[] = [];
let totalCount = 0;
let countryCatalog: CountryCatalog | null = null;
let stationSummary: StationSummary | null = null;
let evStock: EVStockCountryYear | null = null;
let coveragePipelinePromise: Promise<CoverageGPU | null> | null = null;
let coverageLayer: CoverageLayer | null = null;
let latestCoverage: CoverageDispatchResult | null = null;
let coverageLoading = false;
let coverageDispatchSeq = 0;
let coverageError: string | null = null;
const controls = bindControls(currentState, applyState);
syncControlsFromState(currentState);

function fmtNumber(n: number): string {
  return n.toLocaleString();
}

function networkBreakdown(state: AppState): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const s of filterStationsForState(allStations, state)) {
    counts.set(s.network, (counts.get(s.network) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

function renderStats(state: AppState, visibleCount: number): void {
  if (!statsEl) return;
  const yearText = state.mode === "timeline" ? ` in ${state.year}` : "";
  const top = networkBreakdown(state).slice(0, 8);
  const rows = top
    .map(([net, n]) => {
      const pct = visibleCount ? ((n / visibleCount) * 100).toFixed(1) : "0.0";
      return `<tr><td>${escapeHtml(net)}</td><td>${fmtNumber(n)}</td><td>${pct}%</td></tr>`;
    })
    .join("");
  const selectedCountry = getSelectedCountry(state.region);
  const scopeLabel = selectedCountry ? selectedCountry.name.toUpperCase() : "WORLD";
  const summaryBucket =
    state.region === "world"
      ? stationSummary?.global
      : stationSummary?.countries[state.region];
  const ev = latestEVStock(state.region);
  const fastCount = summaryBucket
    ? summaryBucket.dc_fast_count + summaryBucket.ultra_count
    : null;
  const allCount = summaryBucket?.station_count ?? null;
  const dataLine = [
    allCount != null ? `${fmtNumber(allCount)} station records` : null,
    fastCount != null ? `${fmtNumber(fastCount)} DC fast or ultra-fast` : null,
    ev ? `${fmtNumber(ev.total)} electric cars (${ev.year})` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const timelineKnown = filterStationsForState(allStations, {
    ...state,
    mode: "today",
  }).filter((s) => s.open_date).length;
  const timelineNote =
    state.mode === "timeline" && timelineKnown === 0
      ? `<p class="data-note">Install dates are not available for this selection. Showing zero timeline stations until dated data exists.</p>`
      : "";
  statsEl.innerHTML = `
    <section class="stat-block">
      <h2>${scopeLabel}</h2>
      ${renderCoverageLine(state)}
      <p><strong>${fmtNumber(visibleCount)}</strong> visible charging stations${yearText}</p>
      <p class="placeholder">${dataLine || `${fmtNumber(totalCount)} loaded across all sources`}</p>
      ${timelineNote}
    </section>
    <table class="stats-table">
      <thead><tr><th>Network</th><th>Sites</th><th>Share</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderCoverageLine(state: AppState): string {
  if (state.region !== "world") {
    return `<p class="coverage-line coverage-pending">Country-level coverage stat coming after country masking lands.</p>`;
  }
  if (coverageError) {
    return `<p class="coverage-line coverage-pending">Coverage unavailable: ${escapeHtml(coverageError)}</p>`;
  }
  if (coverageLoading || !latestCoverage) {
    return `<p class="coverage-line coverage-pending">Computing coverage on GPU…</p>`;
  }
  const pct = (latestCoverage.fractionCovered * 100).toFixed(1);
  const coveredM = (latestCoverage.coveredPop / 1_000_000).toFixed(0);
  const totalM = (latestCoverage.totalPop / 1_000_000).toFixed(0);
  return `<p class="coverage-line"><strong>${pct}%</strong> of world population within ${state.distance_km} km of a charger<span class="coverage-detail">${coveredM} M of ${totalM} M people · GPU ${latestCoverage.dispatchMs.toFixed(0)} ms</span></p>`;
}

async function getCoveragePipeline(): Promise<CoverageGPU | null> {
  if (!coveragePipelinePromise) {
    coveragePipelinePromise = (async () => {
      try {
        const pop = await loadPopulationGrid2030();
        const pipeline = await createCoverageGPU(pop, allStations);
        coverageLayer = createCoverageLayer(pipeline.width, pipeline.height);
        globe.add(coverageLayer.mesh);
        return pipeline;
      } catch (err) {
        coverageError = err instanceof Error ? err.message : String(err);
        console.warn("[v2] coverage pipeline init failed:", err);
        return null;
      }
    })();
  }
  return coveragePipelinePromise;
}

async function refreshCoverage(state: AppState): Promise<void> {
  if (state.region !== "world") {
    latestCoverage = null;
    coverageLoading = false;
    return;
  }
  if (allStations.length === 0) return;
  const pipeline = await getCoveragePipeline();
  if (!pipeline) {
    rerenderCurrentStats();
    return;
  }
  const seq = ++coverageDispatchSeq;
  coverageLoading = true;
  rerenderCurrentStats();
  try {
    const result = await pipeline.dispatch(state);
    if (seq !== coverageDispatchSeq) return; // superseded
    latestCoverage = result;
    coverageLoading = false;
    if (coverageLayer) coverageLayer.setMask(result.coveredMask);
    rerenderCurrentStats();
  } catch (err) {
    if (seq !== coverageDispatchSeq) return;
    coverageError = err instanceof Error ? err.message : String(err);
    coverageLoading = false;
    rerenderCurrentStats();
  }
}

function rerenderCurrentStats(): void {
  if (!updatePoints) return;
  const visible = filterStationsForState(allStations, currentState).length;
  renderStats(currentState, visible);
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function applyState(state: AppState): void {
  const previous = currentState;
  currentState = state;
  writeStateToHash(state);
  if (updatePoints) {
    const visible = updatePoints(state);
    renderStats(state, visible);
  }
  if (allStations.length > 0 && coverageInputsChanged(previous, state)) {
    void refreshCoverage(state);
  }
}

function coverageInputsChanged(a: AppState, b: AppState): boolean {
  return (
    a.region !== b.region ||
    a.dataset !== b.dataset ||
    a.mode !== b.mode ||
    a.year !== b.year ||
    a.distance_km !== b.distance_km ||
    a.network_id !== b.network_id
  );
}

Promise.all([
  loadAllStations(),
  loadCountryCatalog(),
  loadStationSummary(),
  loadEVStockCountryYear(),
  loadNetworkCatalog(),
])
  .then(([stations, countries, summary, ev, networks]) => {
    countryCatalog = countries;
    stationSummary = summary;
    evStock = ev;
    if (countryCatalog) {
      normalizeStationCountries(stations, countryCatalog);
      populateRegionOptions(controls, countryCatalog.countries);
    }
    if (networks) {
      populateNetworkOptions(controls, networks.networks);
      if (currentState.network_id == null && controls.network.value) {
        currentState = { ...currentState, network_id: controls.network.value };
      }
    }
    allStations = stations;
    totalCount = stations.length;
    updatePoints = attachPoints(globe, stations);
    applyState(currentState);
    void refreshCoverage(currentState);
    console.info(`[v2] loaded ${stations.length.toLocaleString()} stations`);
  })
  .catch((err) => {
    console.error("[v2] failed to load data:", err);
    if (statsEl) {
      statsEl.innerHTML = `<p style="color:#c63131">Data load failed: ${err instanceof Error ? err.message : String(err)}</p>`;
    }
  });

console.info("[v2] phase 3 mounted");

window.addEventListener("hashchange", () => {
  const next = readStateFromHash(currentState);
  syncControlsFromState(next);
  applyState(next);
});

function getSelectedCountry(region: string): CountryCatalogEntry | null {
  if (!countryCatalog || region === "world") return null;
  return countryCatalog.countries.find((country) => country.iso_a3 === region) ?? null;
}

function latestEVStock(region: string): { year: string; total: number } | null {
  if (!evStock) return null;
  if (region !== "world") {
    const byYear = evStock[region];
    if (!byYear) return null;
    const year = Object.keys(byYear).sort().at(-1);
    const total = year ? byYear[year]?.total : null;
    return year && typeof total === "number" ? { year, total } : null;
  }
  let latestYear = "";
  for (const byYear of Object.values(evStock)) {
    const year = Object.keys(byYear).sort().at(-1);
    if (year && year > latestYear) latestYear = year;
  }
  if (!latestYear) return null;
  let total = 0;
  for (const byYear of Object.values(evStock)) {
    const value = byYear[latestYear]?.total;
    if (typeof value === "number") total += value;
  }
  return { year: latestYear, total };
}

function normalizeStationCountries(
  stations: ChargerStation[],
  catalog: CountryCatalog,
): void {
  const byName = new Map<string, string>();
  for (const country of catalog.countries) {
    byName.set(country.iso_a3.toLowerCase(), country.iso_a3);
    byName.set(country.name.toLowerCase(), country.iso_a3);
    byName.set(country.name_short.toLowerCase(), country.iso_a3);
  }
  for (const station of stations) {
    if (!station.country) continue;
    const raw = String(station.country).trim();
    const iso = byName.get(raw.toLowerCase());
    if (iso) station.country = iso;
  }
}

function readStateFromHash(base: AppState): AppState {
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));
  const dataset = params.get("dataset") as AppState["dataset"] | null;
  const mode = params.get("mode") as AppState["mode"] | null;
  const year = Number.parseInt(params.get("year") ?? "", 10);
  const distance = Number.parseInt(params.get("distance") ?? "", 10);
  return {
    region: params.get("region") || base.region,
    dataset:
      dataset &&
      ["fast_only", "ultra_fast", "all_public", "tesla_only", "single_network"].includes(
        dataset,
      )
        ? dataset
        : base.dataset,
    mode: mode && ["today", "timeline"].includes(mode) ? mode : base.mode,
    year: Number.isFinite(year) ? clamp(year, 2012, 2026) : base.year,
    distance_km: Number.isFinite(distance) ? distance : base.distance_km,
    network_id: params.get("network") || base.network_id,
  };
}

function writeStateToHash(state: AppState): void {
  const params = new URLSearchParams();
  if (state.region !== defaultState.region) params.set("region", state.region);
  if (state.dataset !== defaultState.dataset) params.set("dataset", state.dataset);
  if (state.mode !== defaultState.mode) params.set("mode", state.mode);
  if (state.year !== defaultState.year) params.set("year", String(state.year));
  if (state.distance_km !== defaultState.distance_km) {
    params.set("distance", String(state.distance_km));
  }
  if (state.network_id) params.set("network", state.network_id);
  const nextHash = params.toString();
  const currentHash = location.hash.replace(/^#/, "");
  if (nextHash !== currentHash) {
    history.replaceState(
      null,
      "",
      nextHash ? `#${nextHash}` : `${location.pathname}${location.search}`,
    );
  }
}

function syncControlsFromState(state: AppState): void {
  controls.region.value = state.region;
  controls.dataset.value = state.dataset;
  controls.mode.value = state.mode;
  controls.yearSlider.value = String(state.year);
  controls.yearValue.textContent = String(state.year);
  controls.distance.value = String(state.distance_km);
  if (state.network_id) controls.network.value = state.network_id;
  controls.yearControl.hidden = state.mode !== "timeline";
  controls.networkControl.hidden = state.dataset !== "single_network";
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

// Console smoke-test: window.coverageSmokeTest() runs the WebGPU pipeline and
// the CPU prototype against the same state and logs both results so we can
// verify GPU output matches CPU semantics before wiring it into the UI.
declare global {
  interface Window {
    coverageSmokeTest: (
      stateOverride?: Partial<AppState>,
    ) => Promise<void>;
  }
}

window.coverageSmokeTest = async (stateOverride = {}) => {
  const { loadPopulationGrid2030 } = await import("./compute/populationGrid");
  const { createCoverageGPU } = await import("./compute/coverageGPU");
  const { computeCoveragePrototype } = await import(
    "./compute/coveragePrototype"
  );
  if (allStations.length === 0) {
    console.warn("[smoketest] stations not loaded yet");
    return;
  }
  const state: AppState = { ...currentState, ...stateOverride };
  console.info("[smoketest] state", JSON.stringify(state));
  const filtered = filterStationsForState(allStations, state);
  console.info(
    `[smoketest] CPU-filter station count = ${filtered.length} / ${allStations.length}`,
  );
  const t0 = performance.now();
  const population = await loadPopulationGrid2030();
  const t1 = performance.now();
  console.info(
    `[smoketest] pop grid loaded (${population.values.length} cells, ${(t1 - t0).toFixed(0)}ms)`,
  );
  const pipeline = await createCoverageGPU(population, allStations);
  const gpu = await pipeline.dispatch(state);
  console.info(
    `[smoketest] gpu coveredPop=${gpu.coveredPop} totalPop=${gpu.totalPop} ` +
      `fraction=${(gpu.fractionCovered * 100).toFixed(4)}% ` +
      `stations=${gpu.stationCount} dispatch=${gpu.dispatchMs.toFixed(1)}ms`,
  );
  const tCPU = performance.now();
  const cpu = computeCoveragePrototype(population, allStations, state);
  const cpuMs = performance.now() - tCPU;
  const cpuFraction =
    cpu.totalPopulation > 0 ? cpu.coveredPopulation / cpu.totalPopulation : 0;
  console.info(
    `[smoketest] cpu coveredPop=${cpu.coveredPopulation.toFixed(0)} ` +
      `totalPop=${cpu.totalPopulation.toFixed(0)} ` +
      `fraction=${(cpuFraction * 100).toFixed(4)}% ` +
      `coveredCells=${cpu.coveredCells}/${cpu.totalNonzeroCells} ` +
      `stations=${cpu.stationCount} (${cpuMs.toFixed(0)}ms)`,
  );
  const diff = Math.abs(gpu.fractionCovered - cpuFraction);
  console.info(
    `[smoketest] |gpu - cpu| = ${(diff * 100).toFixed(4)}%`,
  );
  pipeline.destroy();
};
