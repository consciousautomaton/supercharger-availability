import type {
  ChargerStation,
  CountryCatalog,
  EVStockCountryYear,
  PopulationGridMeta,
  StationSummary,
} from "./types";

interface LoadedSource {
  source: string;
  stations: ChargerStation[];
}

interface SourceSpec {
  source: string;
  url: string;
  required: boolean;
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
  const sourceSpecs: SourceSpec[] = [
    { source: "tesla", url: "/data/chargers_tesla.json", required: true },
    { source: "bnetza", url: "/data/chargers_bnetza.json", required: true },
    { source: "irve", url: "/data/chargers_irve.json", required: false },
    { source: "nobil", url: "/data/chargers_nobil.json", required: false },
    { source: "afdc", url: "/data/chargers_afdc.json", required: false },
  ];
  const loaded = await Promise.all(
    sourceSpecs.map(async (spec): Promise<LoadedSource | null> => {
      try {
        const stations = await fetchJson<ChargerStation[]>(spec.url);
        return { source: spec.source, stations };
      } catch (err) {
        if (spec.required) throw err;
        console.warn(`Skipping optional charger source ${spec.source}:`, err);
        return null;
      }
    }),
  );
  const out: ChargerStation[] = [];
  for (const loadedSource of loaded) {
    if (!loadedSource) continue;
    const { stations } = loadedSource;
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

export async function loadEVStockCountryYear(): Promise<EVStockCountryYear | null> {
  try {
    return await fetchJson<EVStockCountryYear>("/data/ev_stock_country_year.json");
  } catch (err) {
    console.warn("Skipping EV stock data:", err);
    return null;
  }
}

export async function loadStationSummary(): Promise<StationSummary | null> {
  try {
    return await fetchJson<StationSummary>("/data/station_summary.json");
  } catch (err) {
    console.warn("Skipping station summary:", err);
    return null;
  }
}

export async function loadPopulationGridMeta(): Promise<PopulationGridMeta | null> {
  try {
    return await fetchJson<PopulationGridMeta>("/data/pop_025deg_world_2030_meta.json");
  } catch (err) {
    console.warn("Skipping population grid metadata:", err);
    return null;
  }
}

export async function loadCountryCatalog(): Promise<CountryCatalog | null> {
  try {
    return await fetchJson<CountryCatalog>("/data/country_catalog.json");
  } catch (err) {
    console.warn("Skipping country catalog:", err);
    return null;
  }
}
