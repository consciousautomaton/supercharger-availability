import type { ChargerStation } from "./types";

interface LoadedSource {
  source: string;
  stations: ChargerStation[];
}

async function fetchJson<T>(url: string): Promise<T> {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`Failed to load ${url}: ${resp.status}`);
  return (await resp.json()) as T;
}

function inferKind(s: ChargerStation): ChargerStation["kind"] {
  if (s.kind) return s.kind;
  const p = s.power_kw;
  if (p == null) return "unknown";
  return p >= 50 ? "fast" : "slow";
}

export async function loadAllStations(): Promise<ChargerStation[]> {
  const sources: Array<Promise<LoadedSource>> = [
    fetchJson<ChargerStation[]>("/data/chargers_tesla.json").then((stations) => ({
      source: "tesla",
      stations,
    })),
    fetchJson<ChargerStation[]>("/data/chargers_bnetza.json").then((stations) => ({
      source: "bnetza",
      stations,
    })),
  ];
  const loaded = await Promise.all(sources);
  const out: ChargerStation[] = [];
  for (const { stations } of loaded) {
    for (const s of stations) {
      s.kind = inferKind(s);
      out.push(s);
    }
  }
  return out;
}

export function openYear(s: ChargerStation): number | null {
  if (!s.open_date) return null;
  const y = Number.parseInt(s.open_date.slice(0, 4), 10);
  return Number.isFinite(y) ? y : null;
}
