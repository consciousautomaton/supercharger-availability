import json
import numpy as np

DISTANCES_NPZ = "data/pixel_distances.npz"
RASTER_OUT    = "data/global_min_dist.bin"
META_OUT      = "data/global_raster_meta.json"
CUMULATIVE_OUT = "data/pop_cumulative.json"

# Raster grid: 0.05 deg/cell, 85S-85N x 180W-180E
NCOLS   = 7200
NROWS   = 3400
LAT_MAX =  85.0
LAT_MIN = -85.0
LON_MIN = -180.0
LON_MAX =  180.0
MAX_KM  = 500

print("Loading pixel_distances.npz...")
d = np.load(DISTANCES_NPZ)
lons        = d["lons"]          # float32 (N,)
lats        = d["lats"]          # float32 (N,)
pop         = d["pop"]           # float32 (N,)
min_dist_km = d["min_dist_km"]   # float32 (N,)
N = len(lons)
print(f"Loaded {N:,} pixels")

# ------------------------------------------------------------------ #
# 1. pop_cumulative.json                                               #
# ------------------------------------------------------------------ #
print("\nBuilding pop_cumulative.json...")

hist, _ = np.histogram(min_dist_km, bins=MAX_KM, range=(0.0, MAX_KM), weights=pop)
cumulative = np.concatenate([[0.0], np.cumsum(hist)])   # shape (501,)

total_pop = float(pop.sum())
print(f"  Total population in dataset : {total_pop/1e9:.3f} B")
print(f"  Within 10 km  : {cumulative[10]/1e9:.3f} B  ({cumulative[10]/total_pop*100:.2f}%)")
print(f"  Within 50 km  : {cumulative[50]/1e9:.3f} B  ({cumulative[50]/total_pop*100:.2f}%)")
print(f"  Within 100 km : {cumulative[100]/1e9:.3f} B  ({cumulative[100]/total_pop*100:.2f}%)")
print(f"  Within 500 km : {cumulative[500]/1e9:.3f} B  ({cumulative[500]/total_pop*100:.2f}%)")

with open(CUMULATIVE_OUT, "w") as f:
    json.dump(cumulative.tolist(), f)
print(f"  Saved {CUMULATIVE_OUT}")

# ------------------------------------------------------------------ #
# 2. global_min_dist.bin                                               #
# ------------------------------------------------------------------ #
print("\nBuilding global_min_dist.bin...")

# Convert lat/lon to raster indices
col_idx = ((lons - LON_MIN) / (LON_MAX - LON_MIN) * NCOLS).astype(np.int32)
row_idx = ((LAT_MAX - lats) / (LAT_MAX - LAT_MIN) * NROWS).astype(np.int32)

# Drop any pixels outside the grid bounds (shouldn't be many)
valid = (col_idx >= 0) & (col_idx < NCOLS) & (row_idx >= 0) & (row_idx < NROWS)
print(f"  Valid pixels: {valid.sum():,} / {N:,}")

flat_idx = row_idx[valid].astype(np.int64) * NCOLS + col_idx[valid].astype(np.int64)
dist_u16 = np.clip(min_dist_km[valid], 0, 65534).astype(np.uint16)

# Scatter-minimum: for each raster cell, keep the smallest distance
raster = np.full(NROWS * NCOLS, 65535, dtype=np.uint16)  # 65535 = no data
print("  Scattering min distances into raster...")
np.minimum.at(raster, flat_idx, dist_u16)

covered = (raster < 65535).sum()
print(f"  Raster cells with data: {covered:,} / {NROWS*NCOLS:,}")

raster.reshape(NROWS, NCOLS).tofile(RASTER_OUT)
print(f"  Saved {RASTER_OUT}")

meta = {
    "ncols": NCOLS, "nrows": NROWS,
    "lat_max": LAT_MAX, "lat_min": LAT_MIN,
    "lon_min": LON_MIN, "lon_max": LON_MAX,
    "dtype": "uint16", "nodata": 65535
}
with open(META_OUT, "w") as f:
    json.dump(meta, f, indent=2)
print(f"  Saved {META_OUT}")

print("\nDone.")