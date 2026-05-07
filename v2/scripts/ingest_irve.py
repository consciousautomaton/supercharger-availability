"""
Ingest France IRVE consolidated charging data.

Source:
  https://transport.data.gouv.fr/datasets/base-nationale-des-irve-infrastructures-de-recharge-pour-vehicules-electriques

Download:
  The script caches the official latest 2026-05-06 CSV under data/.

License:
  Licence Ouverte / Etalab Open License.

Output:
  v2/frontend/public/data/chargers_irve.json

Notes:
- The IRVE CSV is point-of-charge oriented. V2 renders station-level dots, so
  rows are aggregated by id_station_itinerance.
- Tesla rows are dropped because supercharge.info is the Tesla source of truth.
- `date_mise_en_service` is treated as authoritative install date. For a
  multi-point station, the earliest available date is used as station open_date.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "data/irve_consolidation_20260506.csv"
OUT = ROOT / "v2/frontend/public/data/chargers_irve.json"

URL = "https://www.data.gouv.fr/fr/datasets/r/eb76d20a-8501-400e-b336-d85724de5435"

TESLA_NEEDLES = ("tesla",)


def download_if_needed() -> None:
    if SRC.exists() and SRC.stat().st_size > 100_000_000:
        print(f"[fetch] using cached {SRC} ({SRC.stat().st_size / 1024 / 1024:.1f} MiB)", file=sys.stderr)
        return
    SRC.parent.mkdir(parents=True, exist_ok=True)
    print(f"[fetch] downloading IRVE CSV to {SRC}", file=sys.stderr)
    subprocess.run(
        [
            "curl.exe",
            "-L",
            "--fail",
            "--show-error",
            "--progress-bar",
            "-o",
            str(SRC),
            URL,
        ],
        check=True,
    )
    print(f"[fetch] downloaded {SRC.stat().st_size / 1024 / 1024:.1f} MiB", file=sys.stderr)


def norm_text(s: str | None) -> str:
    return (s or "").strip()


def parse_float(s: str | None) -> float | None:
    raw = norm_text(s).replace(",", ".")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_int(s: str | None) -> int | None:
    raw = norm_text(s).replace(",", ".")
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def parse_iso_date(s: str | None) -> str | None:
    raw = norm_text(s)
    if not raw:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T.*", raw):
        return raw[:10]
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", raw):
        d, m, y = raw.split("/")
        return f"{y}-{m}-{d}"
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", raw):
        d, m, y = raw.split(".")
        return f"{y}-{m}-{d}"
    return None


def normalize_network(operator: str, enseigne: str) -> str:
    raw = operator or enseigne or "Unknown"
    low = raw.lower()
    rules = [
        ("ionity", "Ionity"),
        ("tesla", "Tesla"),
        ("total", "TotalEnergies"),
        ("fastned", "Fastned"),
        ("allego", "Allego"),
        ("electra", "Electra"),
        ("enedis", "Enedis"),
        ("izivia", "Izivia"),
        ("chargepoint", "ChargePoint"),
        ("freshmile", "Freshmile"),
        ("shell", "Shell Recharge"),
        ("engie", "Engie"),
        ("eborn", "eBorn"),
        ("reveo", "Reveo"),
        ("mobive", "Mobive"),
    ]
    for needle, canon in rules:
        if needle in low:
            return canon
    return raw.strip() or "Unknown"


def is_tesla_row(row: dict[str, str]) -> bool:
    haystack = " ".join(
        [
            row.get("nom_operateur", ""),
            row.get("nom_enseigne", ""),
            row.get("nom_amenageur", ""),
            row.get("nom_station", ""),
        ]
    ).lower()
    return any(needle in haystack for needle in TESLA_NEEDLES)


def parse_coord(row: dict[str, str]) -> tuple[float | None, float | None]:
    lon = parse_float(row.get("consolidated_longitude"))
    lat = parse_float(row.get("consolidated_latitude"))
    if lon is not None and lat is not None:
        return lon, lat

    raw = norm_text(row.get("coordonneesXY"))
    if raw:
        nums = re.findall(r"-?\d+(?:[\.,]\d+)?", raw)
        if len(nums) >= 2:
            lon = parse_float(nums[0])
            lat = parse_float(nums[1])
            return lon, lat
    return None, None


def merge_station(station: dict[str, Any], row: dict[str, str]) -> None:
    pdc_id = norm_text(row.get("id_pdc_itinerance")) or norm_text(row.get("id_pdc_local"))
    if pdc_id:
        station["_pdc_ids"].add(pdc_id)

    power = parse_float(row.get("puissance_nominale"))
    if power is not None:
        station["power_kw"] = max(station["power_kw"] or 0.0, power)

    open_date = parse_iso_date(row.get("date_mise_en_service"))
    if open_date is not None:
        if station["open_date"] is None or open_date < station["open_date"]:
            station["open_date"] = open_date

    connectors = station["connectors"]
    for field in [
        "prise_type_ef",
        "prise_type_2",
        "prise_type_combo_ccs",
        "prise_type_chademo",
        "prise_type_autre",
    ]:
        value = norm_text(row.get(field)).lower()
        if value in {"true", "1", "oui", "yes"}:
            connectors.add(field.replace("prise_type_", ""))


def main() -> None:
    download_if_needed()

    rows = 0
    skipped_tesla = 0
    skipped_no_geo = 0
    skipped_bad_geo = 0
    stations: dict[str, dict[str, Any]] = {}

    with SRC.open(encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f)
        if not rdr.fieldnames:
            raise RuntimeError("IRVE CSV has no header")
        print(f"[parse] columns: {len(rdr.fieldnames)}", file=sys.stderr)

        for row in rdr:
            rows += 1
            if rows % 250_000 == 0:
                print(
                    f"  parsed {rows:,} rows | stations: {len(stations):,} | "
                    f"skipped tesla: {skipped_tesla:,} | skipped geo: {skipped_no_geo + skipped_bad_geo:,}",
                    file=sys.stderr,
                )

            if is_tesla_row(row):
                skipped_tesla += 1
                continue

            lon, lat = parse_coord(row)
            if lon is None or lat is None:
                skipped_no_geo += 1
                continue
            if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
                skipped_bad_geo += 1
                continue

            station_id = norm_text(row.get("id_station_itinerance")) or norm_text(row.get("id_station_local"))
            if not station_id:
                station_id = f"{lat:.6f},{lon:.6f}:{norm_text(row.get('nom_station'))}"

            operator = norm_text(row.get("nom_operateur"))
            enseigne = norm_text(row.get("nom_enseigne"))
            network = normalize_network(operator, enseigne)
            station = stations.get(station_id)
            if station is None:
                station = {
                    "id": f"irve:{station_id}",
                    "lat": lat,
                    "lon": lon,
                    "network": network,
                    "power_kw": None,
                    "stall_count": None,
                    "open_date": None,
                    "close_date": None,
                    "date_source": "authoritative_install",
                    "country": "FRA",
                    "name": norm_text(row.get("nom_station")) or None,
                    "kind": "unknown",
                    "operator_raw": operator or None,
                    "amenageur_raw": norm_text(row.get("nom_amenageur")) or None,
                    "connectors": set(),
                    "_pdc_ids": set(),
                }
                stations[station_id] = station
            merge_station(station, row)

    out: list[dict[str, Any]] = []
    missing_open_date = 0
    fast = 0
    slow = 0
    unknown = 0
    for station in stations.values():
        pdc_count = len(station.pop("_pdc_ids"))
        station["stall_count"] = pdc_count or parse_int(station.get("stall_count"))
        station["connectors"] = sorted(station["connectors"])
        power = station["power_kw"]
        if power is None:
            station["kind"] = "unknown"
            unknown += 1
        elif power >= 50:
            station["kind"] = "fast"
            fast += 1
        else:
            station["kind"] = "slow"
            slow += 1
        if station["open_date"] is None:
            missing_open_date += 1
        out.append(station)

    out.sort(key=lambda s: (str(s["network"]), str(s["id"])))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)

    print(f"[done] wrote {len(out):,} stations to {OUT}", file=sys.stderr)
    print(f"  raw input rows        : {rows:,}", file=sys.stderr)
    print(f"  skipped Tesla rows    : {skipped_tesla:,}", file=sys.stderr)
    print(f"  skipped missing geo   : {skipped_no_geo:,}", file=sys.stderr)
    print(f"  skipped bad geo       : {skipped_bad_geo:,}", file=sys.stderr)
    print(f"  fast / slow / unknown : {fast:,} / {slow:,} / {unknown:,}", file=sys.stderr)
    print(f"  no open_date output   : {missing_open_date:,}", file=sys.stderr)
    print(f"  output size           : {OUT.stat().st_size / 1024 / 1024:.1f} MiB", file=sys.stderr)


if __name__ == "__main__":
    main()

