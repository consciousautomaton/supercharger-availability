"""
Build a canonical network catalog from raw station summary labels.

Input:
  v2/frontend/public/data/station_summary.json

Output:
  v2/frontend/public/data/network_catalog.json

Purpose:
  Public registries contain thousands of raw operator strings. The UI needs a
  manageable list of canonical networks for filters and summaries while still
  preserving raw labels for traceability.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "v2/frontend/public/data"
SUMMARY = DATA_DIR / "station_summary.json"
OUT = DATA_DIR / "network_catalog.json"


def canonical_network(raw: str) -> tuple[str, str]:
    low = raw.lower()
    rules = [
        (r"\btesla\b", "tesla", "Tesla"),
        (r"\bchargepoint\b", "chargepoint", "ChargePoint"),
        (r"\bionity\b", "ionity", "Ionity"),
        (r"\bfastned\b", "fastned", "Fastned"),
        (r"\belectrify america\b", "electrify-america", "Electrify America"),
        (r"\belectrify canada\b", "electrify-canada", "Electrify Canada"),
        (r"\bevgo\b|\bevgo network\b", "evgo", "EVgo"),
        (r"\bflo\b", "flo", "FLO"),
        (r"circuit.*lectrique", "circuit-electrique", "Circuit electrique"),
        (r"\blink\b", "blink", "Blink"),
        (r"\bvolta\b", "volta", "Volta"),
        (r"\bshell\b", "shell-recharge", "Shell Recharge"),
        (r"\bbp\b|\baral pulse\b", "bp-pulse", "bp pulse / Aral Pulse"),
        (r"\benbw\b", "enbw", "EnBW"),
        (r"\be\.?on\b", "eon", "E.ON"),
        (r"\ballego\b", "allego", "Allego"),
        (r"\belectra\b", "electra", "Electra"),
        (r"\bizivia\b", "izivia", "Izivia"),
        (r"\bfreshmile\b", "freshmile", "Freshmile"),
        (r"\btotal\b", "totalenergies", "TotalEnergies"),
        (r"\bengie\b", "engie", "Engie"),
        (r"\bmer\b", "mer", "Mer"),
        (r"\bewe go\b", "ewe-go", "EWE Go"),
        (r"\bstadtwerke\b", "stadtwerke", "Stadtwerke / regional utilities"),
        (r"\bnon-networked\b", "non-networked", "Non-networked"),
    ]
    for pattern, ident, label in rules:
        if re.search(pattern, low):
            return ident, label
    cleaned = re.sub(r"[^a-z0-9]+", "-", low).strip("-")
    return f"raw:{cleaned or 'unknown'}", raw or "Unknown"


def add_counts(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in [
        "station_count",
        "slow_count",
        "dc_fast_count",
        "ultra_count",
        "unknown_power_count",
        "with_open_date",
    ]:
        target[key] += int(source.get(key) or 0)
    opened = target["opened_by_year"]
    for year, count in (source.get("opened_by_year") or {}).items():
        opened[year] = opened.get(year, 0) + int(count)


def main() -> None:
    if not SUMMARY.exists():
        raise FileNotFoundError(SUMMARY)
    with SUMMARY.open(encoding="utf-8") as f:
        summary = json.load(f)
    raw_networks = summary.get("networks") or {}
    if not isinstance(raw_networks, dict):
        raise RuntimeError("station_summary.json missing networks object")

    groups: dict[str, dict[str, Any]] = {}
    raw_labels_by_group: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for raw_name, counts in raw_networks.items():
        ident, label = canonical_network(raw_name)
        group = groups.get(ident)
        if group is None:
            group = {
                "id": ident,
                "label": label,
                "station_count": 0,
                "slow_count": 0,
                "dc_fast_count": 0,
                "ultra_count": 0,
                "unknown_power_count": 0,
                "with_open_date": 0,
                "opened_by_year": {},
                "raw_label_count": 0,
                "top_raw_labels": [],
            }
            groups[ident] = group
        add_counts(group, counts)
        raw_count = int((counts or {}).get("station_count") or 0)
        raw_labels_by_group[ident].append((raw_name, raw_count))
        group["raw_label_count"] += 1

    for ident, group in groups.items():
        group["opened_by_year"] = dict(sorted(group["opened_by_year"].items()))
        top_raw = sorted(raw_labels_by_group[ident], key=lambda item: (-item[1], item[0].lower()))[:20]
        group["top_raw_labels"] = [{"label": label, "station_count": count} for label, count in top_raw]

    rows = sorted(groups.values(), key=lambda row: (-row["station_count"], row["label"].lower()))
    out = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "Derived from station_summary.json raw network labels",
            "raw_network_count": len(raw_networks),
            "canonical_network_count": len(rows),
            "notes": [
                "Canonicalization is heuristic and intended for UI grouping.",
                "Raw labels are preserved in top_raw_labels for auditability.",
            ],
        },
        "networks": rows,
    }
    with OUT.open("w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)

    top = ", ".join(f"{row['label']}={row['station_count']:,}" for row in rows[:10])
    print(f"[done] wrote {OUT}", file=sys.stderr)
    print(f"  raw networks        : {len(raw_networks):,}", file=sys.stderr)
    print(f"  canonical networks  : {len(rows):,}", file=sys.stderr)
    print(f"  top groups          : {top}", file=sys.stderr)
    print(f"  output size         : {OUT.stat().st_size / 1024:.1f} KiB", file=sys.stderr)


if __name__ == "__main__":
    main()

