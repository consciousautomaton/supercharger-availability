import type ThreeGlobe from "three-globe";
import { openYear } from "../data/loader";
import type { AppState, ChargerStation } from "../data/types";

interface PointDatum {
  lat: number;
  lng: number;
  station: ChargerStation;
  color: string;
  size: number;
}

const COLOR_TESLA = "#ff3838";
const COLOR_TESLA_OPEN = "#ff8a00";
const COLOR_FAST = "#3aa3ff";
const COLOR_SLOW = "#5a8a8a";
const COLOR_RETIRED = "#5a5a5a";

function pickColor(s: ChargerStation): string {
  if (s.status === "CLOSED_PERM" || s.status === "CLOSED_TEMP")
    return COLOR_RETIRED;
  if (s.network === "Tesla") {
    return s.open_to_others ? COLOR_TESLA_OPEN : COLOR_TESLA;
  }
  return s.kind === "fast" ? COLOR_FAST : COLOR_SLOW;
}

function pickSize(s: ChargerStation): number {
  const stalls = s.stall_count ?? 4;
  return 0.16 + Math.min(0.55, Math.sqrt(stalls) * 0.07);
}

function passesDataset(s: ChargerStation, dataset: AppState["dataset"]): boolean {
  switch (dataset) {
    case "fast_only":
      return s.kind === "fast";
    case "tesla_only":
      return s.network === "Tesla";
    case "all_public":
    default:
      return true;
  }
}

function visibleAt(s: ChargerStation, state: AppState): boolean {
  if (!passesDataset(s, state.dataset)) return false;
  if (state.mode === "today") {
    if (s.network === "Tesla") {
      return s.status === "OPEN" || s.status === "EXPANDING";
    }
    return s.close_date == null;
  }
  const y = openYear(s);
  if (y === null) return false;
  return y <= state.year;
}

export function attachPoints(
  globe: ThreeGlobe,
  stations: ChargerStation[],
): (state: AppState) => number {
  const allData: PointDatum[] = stations.map((s) => ({
    lat: s.lat,
    lng: s.lon,
    station: s,
    color: pickColor(s),
    size: pickSize(s),
  }));

  globe
    .pointsData([])
    .pointLat("lat")
    .pointLng("lng")
    .pointColor("color")
    .pointAltitude(0.005)
    .pointRadius("size")
    .pointResolution(5)
    .pointsMerge(true);

  function update(state: AppState): number {
    const visible = allData.filter((d) => visibleAt(d.station, state));
    globe.pointsData(visible);
    return visible.length;
  }

  return update;
}
