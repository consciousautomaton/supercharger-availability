import { createGlobe } from "./globe/setup";
import { attachPoints } from "./globe/points";
import { bindControls } from "./ui/controls";
import { loadAllStations, openYear } from "./data/loader";
import type { AppState, ChargerStation } from "./data/types";

const initialState: AppState = {
  region: "world",
  dataset: "fast_only",
  mode: "today",
  year: 2026,
  distance_km: 50,
};

const container = document.getElementById("globe-container");
if (!container) throw new Error("#globe-container missing");

const { globe } = createGlobe(container);
const statsEl = document.querySelector<HTMLElement>("#stats-panel");

let currentState: AppState = { ...initialState };
let updatePoints: ((state: AppState) => number) | null = null;
let allStations: ChargerStation[] = [];
let totalCount = 0;

function fmtNumber(n: number): string {
  return n.toLocaleString();
}

function networkBreakdown(state: AppState): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const s of allStations) {
    if (state.dataset === "fast_only" && s.kind !== "fast") continue;
    if (state.dataset === "tesla_only" && s.network !== "Tesla") continue;
    if (state.mode === "timeline") {
      const y = openYear(s);
      if (y === null || y > state.year) continue;
    } else {
      if (s.network === "Tesla") {
        if (s.status !== "OPEN" && s.status !== "EXPANDING") continue;
      } else if (s.close_date != null) continue;
    }
    counts.set(s.network, (counts.get(s.network) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

function renderStats(state: AppState, visibleCount: number): void {
  if (!statsEl) return;
  const yearText = state.mode === "timeline" ? ` in ${state.year}` : "";
  const top = networkBreakdown(state).slice(0, 8);
  const totalForBreakdown = top.reduce((acc, [, n]) => acc + n, 0);
  const rows = top
    .map(([net, n]) => {
      const pct = totalForBreakdown
        ? ((n / visibleCount) * 100).toFixed(1)
        : "0.0";
      return `<tr><td>${escapeHtml(net)}</td><td>${fmtNumber(n)}</td><td>${pct}%</td></tr>`;
    })
    .join("");
  statsEl.innerHTML = `
    <p><strong>${fmtNumber(visibleCount)}</strong> charging stations${yearText}</p>
    <p class="placeholder">${fmtNumber(totalCount)} loaded across all sources</p>
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

bindControls(currentState, applyState);

loadAllStations()
  .then((stations) => {
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
