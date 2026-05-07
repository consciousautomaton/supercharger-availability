"""
Compute road-network nearest-charger distances for populated cells in a region.

Approach:
    Multi-source Dijkstra from every charger over the OSM driving graph.
    One traversal labels every road node with its meters-to-nearest-charger.
    Each populated cell then snaps to its nearest road node and inherits that
    distance. This avoids per-cell routing (370M calls is intractable).

Scope:
    Runs for ONE region at a time, defined by an OSM PBF. The global road
    graph won't fit in 16GB RAM, so for full coverage you run this per
    country/continent and merge outputs.

Inputs:
    data/populated_pixels.npz   from extract_pixels.py
    data/chargers.npz           from extract_chargers.py
    --pbf <path>                OSM extract for the region
                                (download from https://download.geofabrik.de/)

Output:
    data/pixel_road_distances_<region>.npz
        lons, lats, pop, road_dist_km, gc_dist_km, global_pixel_index, region

    `global_pixel_index` is the index back into populated_pixels.npz, used by
    the merge step that overlays road distances onto the global cell array.

Install:
    .venv/Scripts/pip install pyrosm pandana

Run:
    .venv/Scripts/python scripts/compute_road_distances.py `
        --pbf data/germany-latest.osm.pbf --region DEU

Runtime (rough, 16GB / 14-core laptop):
    Germany   : ~10 min,  ~6 GB RAM peak
    Western EU: ~45 min, ~14 GB RAM peak
    Continental US: similar to Western EU.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# Drivable roads only (motorway / trunk / primary / secondary / tertiary / residential).
# Pandana then traverses these as a weighted graph using edge length in meters.
NETWORK_MODE = "driving"

# Cap the search at the slider's max radius. Cells farther than this stay at inf
# and the merge step will fall back to great-circle distance for them.
MAX_SEARCH_M = 500_000


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


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
    """Lift great-circle distances from the existing pipeline output, for QA + fallback."""
    pd_path = DATA / "pixel_distances.npz"
    if not pd_path.exists():
        log("pixel_distances.npz not found — skipping great-circle comparison")
        return np.full(len(global_idx), np.nan, dtype="float32")
    return np.load(pd_path)["min_dist_km"][global_idx].astype("float32")


def build_network(pbf_path: Path):
    import pyrosm
    from pandana.network import Network

    log(f"loading PBF: {pbf_path.name}")
    osm = pyrosm.OSM(str(pbf_path))

    log("extracting drivable network (slow step — reading PBF)…")
    nodes, edges = osm.get_network(network_type=NETWORK_MODE, nodes=True)
    log(f"  nodes: {len(nodes):,}   edges: {len(edges):,}")

    # pyrosm names: nodes have id/lon/lat; edges have u/v/length(m).
    log("building pandana Network…")
    net = Network(
        nodes["lon"].astype("float64").values,
        nodes["lat"].astype("float64").values,
        edges["u"].values,
        edges["v"].values,
        edges[["length"]].astype("float64"),
    )
    log("  network ready")
    return net


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbf", required=True, help="OSM PBF for the region")
    ap.add_argument("--region", required=True,
                    help="region tag for output filename, e.g. DEU, NA, EU")
    ap.add_argument("--bbox", nargs=4, type=float,
                    metavar=("minx", "miny", "maxx", "maxy"),
                    help="optional bbox to clip cells/chargers; defaults to network bounds")
    args = ap.parse_args()

    pbf_path = Path(args.pbf)
    if not pbf_path.exists():
        sys.exit(f"PBF not found: {pbf_path}")

    net = build_network(pbf_path)

    if args.bbox is None:
        x = net.nodes_df["x"].values
        y = net.nodes_df["y"].values
        bbox = (float(x.min()), float(y.min()), float(x.max()), float(y.max()))
    else:
        bbox = tuple(args.bbox)
    log(f"bbox: ({bbox[0]:.3f}, {bbox[1]:.3f}, {bbox[2]:.3f}, {bbox[3]:.3f})")

    # ── chargers ────────────────────────────────────────────────────────────
    ch_lons, ch_lats = load_chargers_in_bbox(bbox)
    log(f"chargers in bbox: {len(ch_lons):,}")
    if len(ch_lons) == 0:
        sys.exit("no chargers in this region — nothing to compute")

    net.set_pois(
        category="charger",
        maxdist=MAX_SEARCH_M,
        maxitems=1,
        x_col=ch_lons,
        y_col=ch_lats,
    )

    # ── multi-source Dijkstra ───────────────────────────────────────────────
    log("running multi-source nearest-POI (this is the heavy step)…")
    nearest = net.nearest_pois(
        distance=MAX_SEARCH_M,
        category="charger",
        num_pois=1,
    )
    # nearest is a DataFrame indexed by node id; column `1` is meters to the
    # closest charger. Nodes farther than MAX_SEARCH_M get NaN.
    log(f"  labeled {nearest[1].notna().sum():,} of {len(nearest):,} road nodes")

    # ── snap cells to nodes ─────────────────────────────────────────────────
    px_lons, px_lats, px_pop, px_idx = load_pixels_in_bbox(bbox)
    log(f"populated cells in bbox: {len(px_lons):,}")
    if len(px_lons) == 0:
        sys.exit("no populated cells in this region's bbox")

    log("snapping cells to nearest network node…")
    cell_node_ids = net.get_node_ids(px_lons, px_lats)
    dist_m = nearest[1].reindex(cell_node_ids).values
    road_dist_km = (dist_m / 1000.0).astype("float32")
    road_dist_km[np.isnan(road_dist_km)] = np.inf

    # ── compare to great-circle ─────────────────────────────────────────────
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
