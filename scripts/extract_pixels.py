import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import rasterio
from rasterio.windows import Window
from pyproj import Transformer

TIF_PATH = "data/GHS_POP_E2030_GLOBE_R2023A_54009_100_V1_0.tif"
OUT_PATH = "data/populated_pixels.npz"
TILE     = 4096
WORKERS  = 14
MIN_POP  = 1.0

with rasterio.open(TIF_PATH) as ds:
    transform = ds.transform
    width     = ds.width
    height    = ds.height
    nodata    = ds.nodata

transformer = Transformer.from_crs("ESRI:54009", "EPSG:4326", always_xy=True)

_local = threading.local()

def get_ds():
    if not hasattr(_local, "ds"):
        _local.ds = rasterio.open(TIF_PATH)
    return _local.ds

def process_tile(args):
    row_off, col_off = args
    ds = get_ds()

    actual_h = min(row_off + TILE, height) - row_off
    actual_w = min(col_off + TILE, width)  - col_off

    data = ds.read(1, window=Window(col_off, row_off, actual_w, actual_h)).astype(np.float32)

    mask = (data != nodata) & (data >= MIN_POP)
    if not mask.any():
        return None

    rows, cols = np.where(mask)
    pop        = data[mask]

    rows = (rows + row_off).astype(np.float64)
    cols = (cols + col_off).astype(np.float64)

    x = transform.c + (cols + 0.5) * transform.a
    y = transform.f + (rows + 0.5) * transform.e

    lons, lats = transformer.transform(x, y)

    return np.float32(lons), np.float32(lats), np.float32(pop), np.float32(x), np.float32(y)


def main():
    tile_args = [
        (row_off, col_off)
        for row_off in range(0, height, TILE)
        for col_off in range(0, width, TILE)
    ]
    n_tiles = len(tile_args)
    print(f"Processing {n_tiles:,} tiles across {WORKERS} workers...")

    lons_list, lats_list, pop_list, x_list, y_list = [], [], [], [], []
    total_px = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        for i, result in enumerate(executor.map(process_tile, tile_args), 1):
            if result is not None:
                lons, lats, pop, x, y = result
                lons_list.append(lons)
                lats_list.append(lats)
                pop_list.append(pop)
                x_list.append(x)
                y_list.append(y)
                total_px += len(pop)
            if i % 100 == 0:
                print(f"  {i}/{n_tiles} tiles  |  {total_px:,} pixels so far")

    print(f"\nTotal populated pixels: {total_px:,}")
    print("Concatenating and saving...")
    np.savez_compressed(
        OUT_PATH,
        lons   = np.concatenate(lons_list),
        lats   = np.concatenate(lats_list),
        pop    = np.concatenate(pop_list),
        x_moll = np.concatenate(x_list),
        y_moll = np.concatenate(y_list),
    )
    print(f"Saved to {OUT_PATH}")


if __name__ == "__main__":
    main()