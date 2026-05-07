"""
Run the V2 data build pipeline in dependency order.

Default behavior:
- Rebuilds local/cached ingests and generated summary artifacts.
- Skips blocked/manual sources unless explicitly requested.
- Prints timing and status for each step.

Usage:
  .venv/Scripts/python v2/scripts/build_v2_data.py
  .venv/Scripts/python v2/scripts/build_v2_data.py --include-nobil
  .venv/Scripts/python v2/scripts/build_v2_data.py --skip-population
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PYTHON = ROOT / ".venv/Scripts/python.exe"


@dataclass(frozen=True)
class Step:
    name: str
    script: str
    optional: bool = False


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-nobil", action="store_true", help="Run Nobil ingest; requires API key or cached data.")
    ap.add_argument("--skip-population", action="store_true", help="Skip the 0.25-degree population prototype build.")
    ap.add_argument("--skip-ingests", action="store_true", help="Skip source ingests and rebuild only derived JSONs.")
    return ap.parse_args()


def run_step(step: Step) -> bool:
    start = time.time()
    print(f"\n=== {step.name} ===", file=sys.stderr)
    proc = subprocess.run([str(PYTHON), step.script], cwd=ROOT)
    elapsed = time.time() - start
    status = "ok" if proc.returncode == 0 else "skipped/failed" if step.optional else "failed"
    print(f"=== {step.name}: {status} in {elapsed:.1f}s ===", file=sys.stderr)
    if proc.returncode != 0 and not step.optional:
        return False
    return True


def main() -> int:
    args = parse_args()
    if not PYTHON.exists():
        print(f"[error] missing venv Python at {PYTHON}", file=sys.stderr)
        return 1

    steps: list[Step] = []
    if not args.skip_ingests:
        steps.extend(
            [
                Step("Tesla supercharge.info ingest", "v2/scripts/ingest_supercharge.py"),
                Step("Germany BNetzA ingest", "v2/scripts/ingest_bnetza.py"),
                Step("France IRVE ingest", "v2/scripts/ingest_irve.py"),
                Step("US/Canada AFDC ingest", "v2/scripts/ingest_afdc.py"),
            ]
        )
        if args.include_nobil:
            steps.append(Step("Norway Nobil ingest", "v2/scripts/ingest_nobil.py", optional=True))

    steps.extend(
        [
            Step("EV stock ingest", "v2/scripts/ingest_iea_ev_stock.py"),
            Step("Station summary", "v2/scripts/build_station_summary.py"),
        ]
    )
    if not args.skip_population:
        steps.append(Step("Population prototype grid", "v2/scripts/build_population_layer.py"))
    steps.extend(
        [
            Step("Country catalog", "v2/scripts/build_country_catalog.py"),
            Step("V2 data validation", "v2/scripts/validate_v2_data.py"),
        ]
    )

    overall_start = time.time()
    print(f"[build] running {len(steps)} V2 data steps", file=sys.stderr)
    for step in steps:
        if not run_step(step):
            print(f"[build] stopped at failed step: {step.name}", file=sys.stderr)
            return 1
    elapsed = time.time() - overall_start
    print(f"\n[done] V2 data build completed in {elapsed:.1f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
