import type { PopulationGridMeta } from "../data/types";
import { dataUrl } from "../data/paths";

export interface LoadedPopulationGrid {
  meta: PopulationGridMeta;
  values: Float32Array;
}

async function fetchJson<T>(url: string): Promise<T> {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`Failed to load ${url}: ${resp.status}`);
  return (await resp.json()) as T;
}

export async function loadPopulationGrid2030(): Promise<LoadedPopulationGrid> {
  const meta = await fetchJson<PopulationGridMeta>(
    dataUrl("pop_025deg_world_2030_meta.json"),
  );
  const resp = await fetch(dataUrl("pop_025deg_world_2030.bin"));
  if (!resp.ok) {
    throw new Error(`Failed to load population grid binary: ${resp.status}`);
  }
  const buf = await resp.arrayBuffer();
  const expectedBytes = meta.width * meta.height * 4;
  if (buf.byteLength !== expectedBytes) {
    throw new Error(
      `Population grid size mismatch: got ${buf.byteLength} bytes, expected ${expectedBytes}`,
    );
  }
  return {
    meta,
    values: new Float32Array(buf),
  };
}

export function populationCellCenter(
  meta: PopulationGridMeta,
  index: number,
): { lat: number; lon: number } {
  const x = index % meta.width;
  const y = Math.floor(index / meta.width);
  const lon = meta.bounds.west + (x + 0.5) * meta.cell_degrees;
  const lat = meta.bounds.north - (y + 0.5) * meta.cell_degrees;
  return { lat, lon };
}

