import type { ChargerStation } from "../data/types";

export interface IndexedStation {
  station: ChargerStation;
  lat: number;
  lon: number;
}

export interface NearestStationResult {
  station: ChargerStation;
  distanceKm: number;
}

const EARTH_RADIUS_KM = 6371.0088;

export class ChargerSpatialIndex {
  readonly cellDegrees: number;
  private readonly buckets = new Map<string, IndexedStation[]>();
  private readonly stations: IndexedStation[];

  constructor(stations: readonly ChargerStation[], cellDegrees = 1) {
    this.cellDegrees = cellDegrees;
    this.stations = stations
      .filter((station) => Number.isFinite(station.lat) && Number.isFinite(station.lon))
      .map((station) => ({ station, lat: station.lat, lon: station.lon }));

    for (const indexed of this.stations) {
      const key = this.keyFor(indexed.lat, indexed.lon);
      const bucket = this.buckets.get(key);
      if (bucket) bucket.push(indexed);
      else this.buckets.set(key, [indexed]);
    }
  }

  get size(): number {
    return this.stations.length;
  }

  nearestWithin(
    lat: number,
    lon: number,
    maxRadiusKm: number,
  ): NearestStationResult | null {
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || maxRadiusKm < 0) {
      return null;
    }
    const maxLatCells = Math.ceil(maxRadiusKm / 111 / this.cellDegrees) + 1;
    const cosLat = Math.max(0.05, Math.cos((lat * Math.PI) / 180));
    const maxLonCells =
      Math.ceil(maxRadiusKm / (111 * cosLat) / this.cellDegrees) + 1;
    const cx = this.lonCell(lon);
    const cy = this.latCell(lat);
    let best: NearestStationResult | null = null;

    for (let dy = -maxLatCells; dy <= maxLatCells; dy += 1) {
      for (let dx = -maxLonCells; dx <= maxLonCells; dx += 1) {
        const bucket = this.buckets.get(`${cx + dx}:${cy + dy}`);
        if (!bucket) continue;
        for (const candidate of bucket) {
          const distanceKm = haversineKm(lat, lon, candidate.lat, candidate.lon);
          if (distanceKm > maxRadiusKm) continue;
          if (!best || distanceKm < best.distanceKm) {
            best = { station: candidate.station, distanceKm };
          }
        }
      }
    }

    return best;
  }

  private keyFor(lat: number, lon: number): string {
    return `${this.lonCell(lon)}:${this.latCell(lat)}`;
  }

  private lonCell(lon: number): number {
    return Math.floor((lon + 180) / this.cellDegrees);
  }

  private latCell(lat: number): number {
    return Math.floor((lat + 90) / this.cellDegrees);
  }
}

export function haversineKm(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const phi1 = (lat1 * Math.PI) / 180;
  const phi2 = (lat2 * Math.PI) / 180;
  const dPhi = ((lat2 - lat1) * Math.PI) / 180;
  const dLambda = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dPhi / 2) ** 2 +
    Math.cos(phi1) * Math.cos(phi2) * Math.sin(dLambda / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

