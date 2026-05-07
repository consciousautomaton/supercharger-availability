export type DateSource =
  | "authoritative_install"
  | "registry_listing"
  | "unknown";

export type ChargerKind = "fast" | "slow" | "unknown";

export interface ChargerStation {
  id: string;
  lat: number;
  lon: number;
  network: string;
  power_kw: number | null;
  stall_count: number | null;
  open_date: string | null;
  close_date: string | null;
  date_source: DateSource;

  country?: string | null;
  name?: string | null;
  kind?: ChargerKind;

  // Tesla-specific (from supercharge.info)
  status?: string;
  open_to_others?: boolean;

  // Bring-along extras for hover detail later
  [extra: string]: unknown;
}

export type Mode = "today" | "timeline";
export type DatasetFilter = "fast_only" | "all_public" | "tesla_only";

export interface AppState {
  region: string;
  dataset: DatasetFilter;
  mode: Mode;
  year: number;
  distance_km: number;
}

export interface EVStockYear {
  bev: number | null;
  phev: number | null;
  total: number | null;
}

export type EVStockCountryYear = Record<string, Record<string, EVStockYear>>;

export interface StationSummaryNetwork {
  station_count: number;
  slow_count: number;
  dc_fast_count: number;
  ultra_count: number;
  unknown_power_count: number;
  with_open_date: number;
  opened_by_year: Record<string, number>;
}

export interface StationSummaryBucket extends StationSummaryNetwork {
  networks: Record<string, StationSummaryNetwork>;
}

export interface StationSummary {
  meta: {
    generated_at: string;
    sources: Record<string, number>;
    station_count: number;
    unmapped_country_values: Record<string, number>;
    notes: string[];
  };
  global: StationSummaryBucket;
  countries: Record<string, StationSummaryBucket>;
  networks: Record<string, StationSummaryNetwork>;
}

export interface PopulationGridMeta {
  generated_at: string;
  source: string;
  epoch: number;
  grid: string;
  cell_degrees: number;
  width: number;
  height: number;
  dtype: "float32";
  byte_order: string;
  bounds: { west: number; south: number; east: number; north: number };
  layout: string;
  population_sum: number;
  nonzero_cells: number;
  source_populated_cells: number;
  skipped_source_cells: number;
  notes: string[];
}
