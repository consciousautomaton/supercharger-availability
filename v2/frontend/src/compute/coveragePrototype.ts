import { filterStationsForState } from "../data/filters";
import type { AppState, ChargerStation } from "../data/types";
import {
  type LoadedPopulationGrid,
  populationCellCenter,
} from "./populationGrid";
import { ChargerSpatialIndex } from "./spatialIndex";

export interface CoveragePrototypeResult {
  totalPopulation: number;
  coveredPopulation: number;
  coveredCells: number;
  totalNonzeroCells: number;
  stationCount: number;
}

export function computeCoveragePrototype(
  population: LoadedPopulationGrid,
  stations: readonly ChargerStation[],
  state: AppState,
): CoveragePrototypeResult {
  const visibleStations = filterStationsForState(stations, state);
  const index = new ChargerSpatialIndex(visibleStations, 1);
  let totalPopulation = 0;
  let coveredPopulation = 0;
  let coveredCells = 0;
  let totalNonzeroCells = 0;

  for (let i = 0; i < population.values.length; i += 1) {
    const pop = population.values[i];
    if (pop <= 0) continue;
    totalPopulation += pop;
    totalNonzeroCells += 1;
    const center = populationCellCenter(population.meta, i);
    const nearest = index.nearestWithin(
      center.lat,
      center.lon,
      state.distance_km,
    );
    if (nearest) {
      coveredPopulation += pop;
      coveredCells += 1;
    }
  }

  return {
    totalPopulation,
    coveredPopulation,
    coveredCells,
    totalNonzeroCells,
    stationCount: visibleStations.length,
  };
}

