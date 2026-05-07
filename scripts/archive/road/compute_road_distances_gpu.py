"""
GPU multi-source shortest-path from chargers over the OSM driving graph.

Approach:
    Treat every charger as a source with distance 0. Run edge-parallel
    Bellman-Ford on the GPU until no edge produces a relaxation. The result
    is, for every road node, its road-distance to the nearest charger.

    Each populated cell then snaps to its nearest road node (CPU kdtree)
    and inherits that distance.

Why Bellman-Ford and not Dijkstra:
    Dijkstra's priority-queue frontier serializes badly on GPU. Edge-parallel
    BF launches one thread per directed edge and uses atomicMin to relax —
    massively parallel and very GPU-friendly. With multi-source seeding from
    thousands of chargers, the effective diameter is small (tens of passes
    for continental graphs), so total iterations stay low.

Inputs:
    data/populated_pixels.npz     from extract_pixels.py
    data/chargers.npz             from extract_chargers.py
    --pbf <path>                  OSM extract for the region

Output (same contract as compute_road_distances.py):
    data/pixel_road_distances_<region>.npz
        lons, lats, pop, road_dist_km, gc_dist_km, global_pixel_index, region

Install:
    .venv/Scripts/pip install osmium scipy
    # cupy and cuda are assumed present (you already use them in compute_distances.py)
    # Note: we use pyosmium (PyPI name "osmium"), not pyrosm. pyrosm depends on
    # pyrobuf which is unmaintained and fails to build on modern setuptools.

Run:
    .venv/Scripts/python scripts/compute_road_distances_gpu.py `
        --pbf data/germany-latest.osm.pbf --region DEU

GPU memory rough check (RTX 4050, 6GB):
    Germany   : ~0.2 GB    (5M nodes, 12M directed edges after expand)
    Cont. EU  : ~1.5 GB
    Cont. US  : ~1.5 GB
    Global    : ~4 GB — tight; chunk by region instead.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

NETWORK_MODE = "driving"
MAX_SEARCH_KM = 500.0  # match the slider; cells beyond stay at +inf for the merge step


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ─────────────────────────────────── data loaders ───────────────────────────────────

def load_chargers_in_bbox(bbox):
    minx, miny, maxx, maxy = bbox
    z = np.load(DATA / "chargers.npz")
    lats, lons = z["lats"], z["lons"]
    mask = (lons >= minx) & (lons <= maxx) & (lats >= miny) & (lats <= maxy)
    return lons[mask], lats[mask]


def load_pixels_in_bbox(bbox):
    minx, miny, maxx, maxy = bbox
    z = np.load(DATA / "populated_pixels.npz")
    lats, lons, pop = z["lats"], z["lons"], z["pop"]
    mask = (lons >= minx) & (lons <= maxx) & (lats >= miny) & (lats <= maxy)
    idx = np.where(mask)[0]
    return lons[mask], lats[mask], pop[mask], idx


def load_gc_distances(global_idx):
    pd_path = DATA / "pixel_distances.npz"
    if not pd_path.exists():
        return np.full(len(global_idx), np.nan, dtype="float32")
    return np.load(pd_path)["min_dist_km"][global_idx].astype("float32")


# ─────────────────────────────────── OSM → arrays ───────────────────────────────────

# Drivable-network OSM highway tags. We exclude footways/cycleways/paths since
# the question is about car access to chargers. service/living_street are
# included because driveways and side streets do connect destinations to the
# arterial network.
_DRIVING_HIGHWAYS = frozenset({
    "motorway", "motorway_link",
    "trunk", "trunk_link",
    "primary", "primary_link",
    "secondary", "secondary_link",
    "tertiary", "tertiary_link",
    "unclassified", "residential",
    "living_street", "service",
})


def _haversine_m(lon1, lat1, lon2, lat2):
    """Vectorized haversine distance in meters between paired lon/lat arrays."""
    R = 6_371_000.0
    rlat1 = np.radians(lat1)
    rlat2 = np.radians(lat2)
    dlat = rlat2 - rlat1
    dlon = np.radians(lon2 - lon1)
    a = np.sin(dlat / 2.0) ** 2 + np.cos(rlat1) * np.cos(rlat2) * np.sin(dlon / 2.0) ** 2
    return 2.0 * R * np.arcsin(np.sqrt(np.maximum(a, 0.0)))


def load_road_graph(pbf_path: Path):
    """
    Single-pass PBF scan via pyosmium. With locations=True, way-node coordinates
    are filled from a sparse cache so we don't need a second pass over nodes.

    Returns:
        node_xy : (N, 2) float64  WGS84 lon/lat per compacted node index
        edge_u  : (E,)   int32    source node index    (directed)
        edge_v  : (E,)   int32    dest node index      (directed)
        edge_w  : (E,)   float32  edge length, METERS
    OSM ways are undirected for our purposes; we emit both directions per segment.
    """
    import osmium

    class _RoadCollector(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.node_osm_ids = []
            self.node_lons = []
            self.node_lats = []
            self._seen = set()
            self.edge_u_osm = []
            self.edge_v_osm = []
            self._invalid_skipped = 0

        def way(self, w):
            if w.tags.get("highway") not in _DRIVING_HIGHWAYS:
                return
            prev_nid = None
            for n in w.nodes:
                nid = n.ref
                # If a way crosses the PBF clipping boundary, some node refs
                # have no location in the cache. Break the chain there.
                if not n.location.valid():
                    self._invalid_skipped += 1
                    prev_nid = None
                    continue
                if nid not in self._seen:
                    self._seen.add(nid)
                    self.node_osm_ids.append(nid)
                    self.node_lons.append(n.location.lon)
                    self.node_lats.append(n.location.lat)
                if prev_nid is not None:
                    self.edge_u_osm.append(prev_nid)
                    self.edge_v_osm.append(nid)
                prev_nid = nid

    log(f"loading PBF: {pbf_path.name}")
    h = _RoadCollector()
    log("scanning ways for driving highways (single pass with location cache)…")
    h.apply_file(str(pbf_path), locations=True)
    log(f"  collected {len(h.node_osm_ids):,} nodes, "
        f"{len(h.edge_u_osm):,} undirected edges "
        f"(skipped {h._invalid_skipped:,} cross-boundary refs)")

    osm_to_idx = {oid: i for i, oid in enumerate(h.node_osm_ids)}
    node_xy = np.column_stack([
        np.asarray(h.node_lons, dtype="float64"),
        np.asarray(h.node_lats, dtype="float64"),
    ])

    n_undir = len(h.edge_u_osm)
    u_idx = np.fromiter(
        (osm_to_idx[a] for a in h.edge_u_osm), dtype="int32", count=n_undir,
    )
    v_idx = np.fromiter(
        (osm_to_idx[b] for b in h.edge_v_osm), dtype="int32", count=n_undir,
    )

    log("computing haversine edge lengths…")
    weights = _haversine_m(
        node_xy[u_idx, 0], node_xy[u_idx, 1],
        node_xy[v_idx, 0], node_xy[v_idx, 1],
    ).astype("float32")

    # Expand undirected -> directed both ways. Highway one-ways aren't honored;
    # for nearest-charger access that's an acceptable simplification.
    edge_u = np.concatenate([u_idx, v_idx]).astype("int32")
    edge_v = np.concatenate([v_idx, u_idx]).astype("int32")
    edge_w = np.concatenate([weights, weights]).astype("float32")
    log(f"  compacted: {len(node_xy):,} nodes, {len(edge_u):,} directed edges")
    return node_xy, edge_u, edge_v, edge_w


# ─────────────────────────────────── GPU SSSP ───────────────────────────────────

_RELAX_KERNEL = r"""
extern "C" __global__
void relax_edges(
    const int*   __restrict__ u,
    const int*   __restrict__ v,
    const float* __restrict__ w,
    float*       dist,
    int*         changed,
    const int    n_edges,
    const float  max_dist
) {
    int e = blockIdx.x * blockDim.x + threadIdx.x;
    if (e >= n_edges) return;

    float du = dist[u[e]];
    if (du >= max_dist) return;            // unreachable source so far
    float nd = du + w[e];
    if (nd >= max_dist) return;            // would exceed search cap; skip

    int   target = v[e];
    float dv = dist[target];
    if (nd >= dv) return;

    // atomicMin on a float via int reinterpretation. Safe because all distances
    // are non-negative — IEEE 754 nonneg floats sort identically to their int bits.
    int* dv_int  = reinterpret_cast<int*>(&dist[target]);
    int  new_int = __float_as_int(nd);
    int  old_int = atomicMin(dv_int, new_int);
    if (new_int < old_int) {
        *changed = 1;
    }
}
"""


def gpu_sssp(node_xy, edge_u, edge_v, edge_w, source_node_ids, max_search_m):
    """
    Edge-parallel multi-source Bellman-Ford on the GPU.
    Returns float32 numpy array of length N — meters to nearest source per node.
    """
    import cupy as cp

    dev_id = cp.cuda.runtime.getDevice()
    props = cp.cuda.runtime.getDeviceProperties(dev_id)
    name = props["name"].decode() if isinstance(props["name"], bytes) else props["name"]
    free_b, total_b = cp.cuda.runtime.memGetInfo()
    log(f"GPU device {dev_id}: {name}  "
        f"({free_b / 1e9:.2f} GB free / {total_b / 1e9:.2f} GB total)")

    n_nodes = len(node_xy)
    n_edges = len(edge_u)

    log(f"GPU: pushing {n_nodes:,} nodes / {n_edges:,} edges to device")
    d_u = cp.asarray(edge_u, dtype=cp.int32)
    d_v = cp.asarray(edge_v, dtype=cp.int32)
    d_w = cp.asarray(edge_w, dtype=cp.float32)

    INF = np.float32(np.inf)
    dist = cp.full(n_nodes, INF, dtype=cp.float32)
    dist[cp.asarray(source_node_ids, dtype=cp.int32)] = 0.0

    changed = cp.zeros(1, dtype=cp.int32)
    relax = cp.RawKernel(_RELAX_KERNEL, "relax_edges")

    threads = 256
    blocks = (n_edges + threads - 1) // threads
    max_iters = 4096            # generous safety; real diameter is much smaller

    log("GPU: relaxing edges until convergence…")
    t0 = time.time()
    for it in range(max_iters):
        changed.fill(0)
        relax(
            (blocks,), (threads,),
            (d_u, d_v, d_w, dist, changed,
             np.int32(n_edges), np.float32(max_search_m)),
        )
        if int(changed[0]) == 0:
            log(f"  converged in {it + 1} iterations  ({time.time() - t0:.1f}s)")
            break
        if (it + 1) % 25 == 0:
            reached = int(cp.isfinite(dist).sum().get())
            log(f"  iter {it + 1:4d}  reached nodes: {reached:,}/{n_nodes:,}")
    else:
        log(f"  WARNING: hit max_iters={max_iters} without converging")

    return cp.asnumpy(dist)


# ─────────────────────────────────── main ───────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbf", required=True, help="OSM PBF for the region")
    ap.add_argument("--region", required=True, help="region tag, e.g. DEU")
    ap.add_argument("--bbox", nargs=4, type=float,
                    metavar=("minx", "miny", "maxx", "maxy"),
                    help="optional bbox; defaults to network bounds")
    args = ap.parse_args()

    pbf_path = Path(args.pbf)
    if not pbf_path.exists():
        sys.exit(f"PBF not found: {pbf_path}")

    node_xy, edge_u, edge_v, edge_w = load_road_graph(pbf_path)

    if args.bbox is None:
        bbox = (
            float(node_xy[:, 0].min()), float(node_xy[:, 1].min()),
            float(node_xy[:, 0].max()), float(node_xy[:, 1].max()),
        )
    else:
        bbox = tuple(args.bbox)
    log(f"bbox: ({bbox[0]:.3f}, {bbox[1]:.3f}, {bbox[2]:.3f}, {bbox[3]:.3f})")

    # CPU kdtree on node lon/lat — used twice: snap chargers, then snap pixels.
    log("building node kdtree…")
    tree = cKDTree(node_xy)

    # ── snap chargers → source node ids ─────────────────────────────────────
    ch_lons, ch_lats = load_chargers_in_bbox(bbox)
    log(f"chargers in bbox: {len(ch_lons):,}")
    if len(ch_lons) == 0:
        sys.exit("no chargers in this region — nothing to compute")
    _, charger_node_ids = tree.query(np.column_stack([ch_lons, ch_lats]), k=1)
    source_nodes = np.unique(charger_node_ids).astype("int32")
    log(f"unique charger-snapped source nodes: {len(source_nodes):,}")

    # ── GPU multi-source SSSP ───────────────────────────────────────────────
    node_dist_m = gpu_sssp(
        node_xy, edge_u, edge_v, edge_w,
        source_node_ids=source_nodes,
        max_search_m=MAX_SEARCH_KM * 1000.0,
    )

    # ── snap cells → nodes, look up ────────────────────────────────────────
    px_lons, px_lats, px_pop, px_idx = load_pixels_in_bbox(bbox)
    log(f"populated cells in bbox: {len(px_lons):,}")
    if len(px_lons) == 0:
        sys.exit("no populated cells in this region's bbox")

    log("snapping cells to nearest road node…")
    _, cell_node_ids = tree.query(np.column_stack([px_lons, px_lats]), k=1)
    road_dist_m = node_dist_m[cell_node_ids]
    road_dist_km = (road_dist_m / 1000.0).astype("float32")

    gc_dist_km = load_gc_distances(px_idx)

    out_path = DATA / f"pixel_road_distances_{args.region}.npz"
    np.savez_compressed(
        out_path,
        lons=px_lons.astype("float32"),
        lats=px_lats.astype("float32"),
        pop=px_pop.astype("float32"),
        road_dist_km=road_dist_km,
        gc_dist_km=gc_dist_km,
        global_pixel_index=px_idx.astype("int64"),
        region=np.array(args.region),
    )

    finite = np.isfinite(road_dist_km)
    log(f"wrote {out_path.name}")
    log(f"  cells with road distance: {finite.sum():,} / {len(road_dist_km):,}")
    if finite.any() and np.isfinite(gc_dist_km).any():
        both = finite & np.isfinite(gc_dist_km) & (gc_dist_km > 0.5)
        if both.any():
            ratio = road_dist_km[both] / gc_dist_km[both]
            log(f"  road / great-circle ratio  median: {np.median(ratio):.2f}"
                f"   p90: {np.percentile(ratio, 90):.2f}"
                f"   p99: {np.percentile(ratio, 99):.2f}")


if __name__ == "__main__":
    main()
