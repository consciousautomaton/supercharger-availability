// WebGPU coverage compute pipeline.
//
// Per-cell brute-force nearest-charger lookup, pruned by a 1° bucket grid.
// Outputs:
//   - per-cell covered mask (u32, kept on GPU for visual layer)
//   - global totals: covered_pop, total_pop (atomic u32)
//
// Coverage matches CPU prototype's semantics:
//   covered = exists charger c with passes_filter(c) AND haversine(cell, c) <= radius.

import type { LoadedPopulationGrid } from "./populationGrid";
import { canonicalNetworkId } from "../data/networks";
import { isStationOpenToday, openYear } from "../data/filters";
import type { AppState, ChargerStation } from "../data/types";

const BUCKET_DEG = 1;
const BUCKET_LON = 360 / BUCKET_DEG;
const BUCKET_LAT = 180 / BUCKET_DEG;
const CHARGER_STRIDE_BYTES = 24; // 6 × 4 bytes

// dataset filter ids — must match WGSL switch
const DATASET_ID: Record<AppState["dataset"], number> = {
  all_public: 0,
  fast_only: 1,
  ultra_fast: 2,
  tesla_only: 3,
  single_network: 4,
};

const MODE_ID: Record<AppState["mode"], number> = {
  today: 0,
  timeline: 1,
};

function fnv1a32(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

interface PackedChargers {
  data: ArrayBuffer; // tightly packed Charger structs
  count: number;
  buckets: Uint32Array; // [start0, count0, start1, count1, ...] — vec2<u32> per bucket
}

function packChargers(stations: readonly ChargerStation[]): PackedChargers {
  const valid = stations.filter(
    (s) => Number.isFinite(s.lat) && Number.isFinite(s.lon),
  );
  const n = valid.length;

  // Assign each station to a 1° bucket, bin them, then re-emit in bucket order.
  const bucketOf = (lat: number, lon: number): number => {
    const blon = Math.floor((((lon + 180) % 360) + 360) % 360 / BUCKET_DEG);
    const blat = Math.min(
      BUCKET_LAT - 1,
      Math.max(0, Math.floor((lat + 90) / BUCKET_DEG)),
    );
    return blat * BUCKET_LON + blon;
  };

  // First pass: count per bucket.
  const bucketCount = new Uint32Array(BUCKET_LON * BUCKET_LAT);
  const stationBucket = new Uint32Array(n);
  for (let i = 0; i < n; i += 1) {
    const b = bucketOf(valid[i].lat, valid[i].lon);
    stationBucket[i] = b;
    bucketCount[b] += 1;
  }

  // Prefix sum → starts.
  const bucketStart = new Uint32Array(BUCKET_LON * BUCKET_LAT);
  let acc = 0;
  for (let b = 0; b < BUCKET_LON * BUCKET_LAT; b += 1) {
    bucketStart[b] = acc;
    acc += bucketCount[b];
  }

  // Reset count for refill cursor.
  const bucketCursor = new Uint32Array(BUCKET_LON * BUCKET_LAT);

  const data = new ArrayBuffer(CHARGER_STRIDE_BYTES * n);
  const dv = new DataView(data);

  for (let i = 0; i < n; i += 1) {
    const s = valid[i];
    const b = stationBucket[i];
    const dest = bucketStart[b] + bucketCursor[b];
    bucketCursor[b] += 1;

    const offset = dest * CHARGER_STRIDE_BYTES;

    const oy = openYear(s) ?? 0;
    let cy = 0;
    if (s.close_date) {
      const parsed = Number.parseInt(s.close_date.slice(0, 4), 10);
      if (Number.isFinite(parsed)) cy = parsed;
    }

    const isFast = s.kind === "fast" ? 1 : 0;
    const isUltra = isFast && typeof s.power_kw === "number" && s.power_kw >= 150 ? 1 : 0;
    const isTesla = s.network === "Tesla" ? 1 : 0;
    const openToday = isStationOpenToday(s) ? 1 : 0;
    const openToOthers = s.open_to_others ? 1 : 0;

    const flags =
      (openToday & 1) |
      ((isFast & 1) << 1) |
      ((isUltra & 1) << 2) |
      ((isTesla & 1) << 3) |
      ((openToOthers & 1) << 4);

    const networkHash = fnv1a32(canonicalNetworkId(s.network));

    dv.setFloat32(offset + 0, s.lat, true);
    dv.setFloat32(offset + 4, s.lon, true);
    dv.setInt32(offset + 8, oy, true);
    dv.setInt32(offset + 12, cy, true);
    dv.setUint32(offset + 16, networkHash, true);
    dv.setUint32(offset + 20, flags, true);
  }

  // Bucket buffer: pairs of (start, count) packed as Uint32.
  const buckets = new Uint32Array(BUCKET_LON * BUCKET_LAT * 2);
  for (let b = 0; b < BUCKET_LON * BUCKET_LAT; b += 1) {
    buckets[b * 2] = bucketStart[b];
    buckets[b * 2 + 1] = bucketCount[b];
  }

  return { data, count: n, buckets };
}

const SHADER = /* wgsl */ `
struct Charger {
  lat: f32,
  lon: f32,
  open_year: i32,
  close_year: i32,
  network_hash: u32,
  flags: u32,
}

struct Params {
  cell_deg: f32,
  west: f32,
  north: f32,
  width: u32,
  height: u32,
  radius_km: f32,
  year: i32,
  mode: u32,
  dataset: u32,
  network_filter: u32,
  station_count: u32,
  bucket_lon_count: u32,
  bucket_lat_count: u32,
  bucket_deg: f32,
  pad0: u32,
  pad1: u32,
}

@group(0) @binding(0) var<uniform> params: Params;
@group(0) @binding(1) var<storage, read> chargers: array<Charger>;
@group(0) @binding(2) var<storage, read> buckets: array<vec2<u32>>;
@group(0) @binding(3) var<storage, read> pop: array<f32>;
@group(0) @binding(4) var<storage, read_write> covered_mask: array<u32>;
@group(0) @binding(5) var<storage, read_write> totals: array<atomic<u32>>;

const PI: f32 = 3.14159265358979;
const DEG2RAD: f32 = 0.01745329252;
const EARTH_R_KM: f32 = 6371.0088;

fn haversine(lat1: f32, lon1: f32, lat2: f32, lon2: f32) -> f32 {
  let phi1 = lat1 * DEG2RAD;
  let phi2 = lat2 * DEG2RAD;
  let dphi = (lat2 - lat1) * DEG2RAD;
  let dlam = (lon2 - lon1) * DEG2RAD;
  let s1 = sin(dphi * 0.5);
  let s2 = sin(dlam * 0.5);
  let a = s1 * s1 + cos(phi1) * cos(phi2) * s2 * s2;
  return 2.0 * EARTH_R_KM * atan2(sqrt(a), sqrt(max(1.0 - a, 0.0)));
}

fn passes_filter(c: Charger) -> bool {
  if (params.mode == 0u) {
    if ((c.flags & 1u) == 0u) { return false; }
  } else {
    if (c.open_year == 0 || c.open_year > params.year) { return false; }
    if (c.close_year != 0 && c.close_year <= params.year) { return false; }
  }
  switch params.dataset {
    case 0u: {}
    case 1u: { if ((c.flags & 2u) == 0u) { return false; } }
    case 2u: { if ((c.flags & 4u) == 0u) { return false; } }
    case 3u: { if ((c.flags & 8u) == 0u) { return false; } }
    case 4u: { if (c.network_hash != params.network_filter) { return false; } }
    default: {}
  }
  return true;
}

@compute @workgroup_size(64)
fn cs_main(@builtin(global_invocation_id) gid: vec3<u32>) {
  let cell_total = params.width * params.height;
  let idx = gid.x;
  if (idx >= cell_total) { return; }

  let p = pop[idx];
  if (p <= 0.0) { return; }

  // Scale to kilopeople. Total world pop ~8.48B exceeds u32 max (4.29B) at
  // person granularity; thousands keeps the global sum well under u32 limits
  // while preserving sub-percent accuracy at the 0.25 deg cell scale.
  let q = u32(p * 0.001);
  atomicAdd(&totals[1], q);

  let x = idx % params.width;
  let y = idx / params.width;
  let lon = params.west + (f32(x) + 0.5) * params.cell_deg;
  let lat = params.north - (f32(y) + 0.5) * params.cell_deg;

  let cos_lat = max(cos(lat * DEG2RAD), 0.05);
  let lat_search = i32(ceil(params.radius_km / 111.0 / params.bucket_deg)) + 1;
  let lon_search = i32(ceil(params.radius_km / (111.0 * cos_lat) / params.bucket_deg)) + 1;

  let blat = i32(floor((lat + 90.0) / params.bucket_deg));
  let blon = i32(floor(((lon + 180.0)) / params.bucket_deg));

  var covered: u32 = 0u;
  for (var dy = -lat_search; dy <= lat_search; dy = dy + 1) {
    let by = blat + dy;
    if (by < 0 || by >= i32(params.bucket_lat_count)) { continue; }
    for (var dx = -lon_search; dx <= lon_search; dx = dx + 1) {
      var bx = blon + dx;
      // wrap longitude
      let lon_count = i32(params.bucket_lon_count);
      bx = ((bx % lon_count) + lon_count) % lon_count;
      let bidx = u32(by) * params.bucket_lon_count + u32(bx);
      let b = buckets[bidx];
      for (var i = 0u; i < b.y; i = i + 1u) {
        let c = chargers[b.x + i];
        if (!passes_filter(c)) { continue; }
        if (haversine(lat, lon, c.lat, c.lon) <= params.radius_km) {
          covered = 1u;
          break;
        }
      }
      if (covered == 1u) { break; }
    }
    if (covered == 1u) { break; }
  }

  covered_mask[idx] = covered;
  if (covered == 1u) { atomicAdd(&totals[0], q); }
}
`;

export interface CoverageDispatchResult {
  coveredPop: number;
  totalPop: number;
  fractionCovered: number;
  stationCount: number;
  dispatchMs: number;
}

export interface CoverageGPU {
  device: GPUDevice;
  width: number;
  height: number;
  dispatch(state: AppState): Promise<CoverageDispatchResult>;
  readCoveredMask(): Promise<Uint32Array>;
  destroy(): void;
}

export async function createCoverageGPU(
  population: LoadedPopulationGrid,
  stations: readonly ChargerStation[],
): Promise<CoverageGPU> {
  const nav = navigator as Navigator & { gpu?: GPU };
  if (!nav.gpu) throw new Error("WebGPU not supported in this browser.");
  const adapter = await nav.gpu.requestAdapter();
  if (!adapter) throw new Error("WebGPU adapter request failed.");
  const device = await adapter.requestDevice();

  const meta = population.meta;
  const cellTotal = meta.width * meta.height;

  const packed = packChargers(stations);

  // Charger storage buffer.
  const chargerBuf = device.createBuffer({
    size: Math.max(CHARGER_STRIDE_BYTES, packed.data.byteLength),
    usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST,
  });
  device.queue.writeBuffer(chargerBuf, 0, packed.data);

  // Bucket storage buffer (vec2<u32> packed).
  const bucketBuf = device.createBuffer({
    size: packed.buckets.byteLength,
    usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST,
  });
  device.queue.writeBuffer(
    bucketBuf,
    0,
    packed.buckets as unknown as ArrayBuffer,
  );

  // Population storage buffer.
  const popBuf = device.createBuffer({
    size: population.values.byteLength,
    usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST,
  });
  device.queue.writeBuffer(
    popBuf,
    0,
    population.values as unknown as ArrayBuffer,
  );

  // Per-cell covered mask.
  const maskBuf = device.createBuffer({
    size: cellTotal * 4,
    usage:
      GPUBufferUsage.STORAGE |
      GPUBufferUsage.COPY_SRC |
      GPUBufferUsage.COPY_DST,
  });

  // Totals: covered_pop, total_pop (u32 atomics).
  const totalsBuf = device.createBuffer({
    size: 8,
    usage:
      GPUBufferUsage.STORAGE |
      GPUBufferUsage.COPY_SRC |
      GPUBufferUsage.COPY_DST,
  });

  const totalsReadback = device.createBuffer({
    size: 8,
    usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ,
  });

  // Params uniform — 16 × 4 bytes = 64 bytes.
  const paramsBuf = device.createBuffer({
    size: 64,
    usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
  });

  const module = device.createShaderModule({ code: SHADER });
  const pipeline = device.createComputePipeline({
    layout: "auto",
    compute: { module, entryPoint: "cs_main" },
  });

  const bindGroup = device.createBindGroup({
    layout: pipeline.getBindGroupLayout(0),
    entries: [
      { binding: 0, resource: { buffer: paramsBuf } },
      { binding: 1, resource: { buffer: chargerBuf } },
      { binding: 2, resource: { buffer: bucketBuf } },
      { binding: 3, resource: { buffer: popBuf } },
      { binding: 4, resource: { buffer: maskBuf } },
      { binding: 5, resource: { buffer: totalsBuf } },
    ],
  });

  const writeParams = (state: AppState): void => {
    const view = new ArrayBuffer(64);
    const dv = new DataView(view);
    dv.setFloat32(0, meta.cell_degrees, true);
    dv.setFloat32(4, meta.bounds.west, true);
    dv.setFloat32(8, meta.bounds.north, true);
    dv.setUint32(12, meta.width, true);
    dv.setUint32(16, meta.height, true);
    dv.setFloat32(20, state.distance_km, true);
    dv.setInt32(24, state.year, true);
    dv.setUint32(28, MODE_ID[state.mode], true);
    dv.setUint32(32, DATASET_ID[state.dataset], true);
    const netHash = state.network_id ? fnv1a32(state.network_id) : 0;
    dv.setUint32(36, netHash, true);
    dv.setUint32(40, packed.count, true);
    dv.setUint32(44, BUCKET_LON, true);
    dv.setUint32(48, BUCKET_LAT, true);
    dv.setFloat32(52, BUCKET_DEG, true);
    dv.setUint32(56, 0, true);
    dv.setUint32(60, 0, true);
    device.queue.writeBuffer(paramsBuf, 0, view);
  };

  const dispatch = async (state: AppState): Promise<CoverageDispatchResult> => {
    writeParams(state);

    // Reset totals.
    device.queue.writeBuffer(
      totalsBuf,
      0,
      new Uint32Array(2) as unknown as ArrayBuffer,
    );
    // Clear mask each dispatch — pop==0 cells aren't written by the shader,
    // so stale values from prior dispatches would leak otherwise.
    device.queue.writeBuffer(
      maskBuf,
      0,
      new Uint32Array(cellTotal) as unknown as ArrayBuffer,
    );

    const start = performance.now();
    const encoder = device.createCommandEncoder();
    const pass = encoder.beginComputePass();
    pass.setPipeline(pipeline);
    pass.setBindGroup(0, bindGroup);
    const wgCount = Math.ceil(cellTotal / 64);
    pass.dispatchWorkgroups(wgCount);
    pass.end();
    encoder.copyBufferToBuffer(totalsBuf, 0, totalsReadback, 0, 8);
    device.queue.submit([encoder.finish()]);
    await device.queue.onSubmittedWorkDone();
    const dispatchMs = performance.now() - start;

    await totalsReadback.mapAsync(GPUMapMode.READ);
    const view = new Uint32Array(totalsReadback.getMappedRange().slice(0));
    totalsReadback.unmap();
    // Shader accumulates kilopeople; multiply back to absolute people.
    const coveredPop = view[0] * 1000;
    const totalPop = view[1] * 1000;
    const fractionCovered = totalPop > 0 ? coveredPop / totalPop : 0;

    return {
      coveredPop,
      totalPop,
      fractionCovered,
      stationCount: packed.count,
      dispatchMs,
    };
  };

  const readCoveredMask = async (): Promise<Uint32Array> => {
    const readback = device.createBuffer({
      size: cellTotal * 4,
      usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ,
    });
    const encoder = device.createCommandEncoder();
    encoder.copyBufferToBuffer(maskBuf, 0, readback, 0, cellTotal * 4);
    device.queue.submit([encoder.finish()]);
    await readback.mapAsync(GPUMapMode.READ);
    const out = new Uint32Array(readback.getMappedRange().slice(0));
    readback.unmap();
    readback.destroy();
    return out;
  };

  const destroy = (): void => {
    chargerBuf.destroy();
    bucketBuf.destroy();
    popBuf.destroy();
    maskBuf.destroy();
    totalsBuf.destroy();
    totalsReadback.destroy();
    paramsBuf.destroy();
    device.destroy();
  };

  return {
    device,
    width: meta.width,
    height: meta.height,
    dispatch,
    readCoveredMask,
    destroy,
  };
}
