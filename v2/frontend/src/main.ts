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
import type {
  AppState,
  ChargerStation,
  CountryCatalog,
  CountryCatalogEntry,
  EVStockCountryYear,
  StationSummary,
} from "./data/types";

const initialState: AppState = {
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

let currentState: AppState = { ...initialState };
let updatePoints: ((state: AppState) => number) | null = null;
let allStations: ChargerStation[] = [];
let totalCount = 0;
let countryCatalog: CountryCatalog | null = null;
let stationSummary: StationSummary | null = null;
let evStock: EVStockCountryYear | null = null;
const controls = bindControls(currentState, applyState);

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

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function applyState(state: AppState): void {
  currentState = state;
  if (updatePoints) {
    const visible = updatePoints(state);
    renderStats(state, visible);
  }
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
    console.info(`[v2] loaded ${stations.length.toLocaleString()} stations`);
  })
  .catch((err) => {
    console.error("[v2] failed to load data:", err);
    if (statsEl) {
      statsEl.innerHTML = `<p style="color:#c63131">Data load failed: ${err instanceof Error ? err.message : String(err)}</p>`;
    }
  });

console.info("[v2] phase 3 mounted");

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
