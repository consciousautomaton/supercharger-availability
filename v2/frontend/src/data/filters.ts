import type { AppState, ChargerStation, DatasetFilter, Mode } from "./types";
import { canonicalNetworkId } from "./networks";

export function openYear(station: ChargerStation): number | null {
  if (!station.open_date) return null;
  const year = Number.parseInt(station.open_date.slice(0, 4), 10);
  return Number.isFinite(year) ? year : null;
}

export function isStationOpenToday(station: ChargerStation): boolean {
  if (station.network === "Tesla") {
    return station.status === "OPEN" || station.status === "EXPANDING";
  }
  return station.close_date == null;
}

export function passesDatasetFilter(
  station: ChargerStation,
  dataset: DatasetFilter,
): boolean {
  switch (dataset) {
    case "ultra_fast":
      return typeof station.power_kw === "number" && station.power_kw >= 150;
    case "fast_only":
      return station.kind === "fast";
    case "tesla_only":
      return station.network === "Tesla";
    case "single_network":
      return true;
    case "all_public":
      return true;
  }
}

export function isVisibleForMode(
  station: ChargerStation,
  mode: Mode,
  year: number,
): boolean {
  if (mode === "today") return isStationOpenToday(station);
  const opened = openYear(station);
  if (opened == null || opened > year) return false;
  if (station.close_date) {
    const closed = Number.parseInt(station.close_date.slice(0, 4), 10);
    if (Number.isFinite(closed) && closed <= year) return false;
  }
  return true;
}

export function stationMatchesState(
  station: ChargerStation,
  state: AppState,
): boolean {
  if (state.region !== "world" && station.country !== state.region) {
    return false;
  }
  return (
    passesDatasetFilter(station, state.dataset) &&
    (state.dataset !== "single_network" ||
      (state.network_id != null &&
        canonicalNetworkId(station.network) === state.network_id)) &&
    isVisibleForMode(station, state.mode, state.year)
  );
}

export function filterStationsForState(
  stations: readonly ChargerStation[],
  state: AppState,
): ChargerStation[] {
  return stations.filter((station) => stationMatchesState(station, state));
}
