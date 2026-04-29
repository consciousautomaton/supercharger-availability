import json
import numpy as np

CHARGER_JSON = "data/tesla_scrape.json"
OUT_PATH     = "data/chargers.npz"

SUPERCHARGER_TYPES = {"supercharger", "winner_supercharger", "current_winner_supercharger"}

with open(CHARGER_JSON) as f:
    entries = json.load(f)["data"]["data"]

chargers = []

for e in entries:
    sf = e.get("supercharger_function")
    types = set(e.get("location_type", []))

    if sf is not None:
        # Has supercharger_function — trust site_status regardless of location_type tag
        if sf.get("site_status") == "open":
            chargers.append((e["latitude"], e["longitude"]))
    else:
        # No supercharger_function (China locations) — include if tagged as supercharger type
        if types & SUPERCHARGER_TYPES:
            chargers.append((e["latitude"], e["longitude"]))

lats = np.array([c[0] for c in chargers], dtype=np.float64)
lons = np.array([c[1] for c in chargers], dtype=np.float64)

np.savez_compressed(OUT_PATH, lats=lats, lons=lons)
print(f"Saved {len(lats):,} superchargers to {OUT_PATH}")
