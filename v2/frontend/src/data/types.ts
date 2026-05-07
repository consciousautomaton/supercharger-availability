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
