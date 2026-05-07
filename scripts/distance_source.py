import argparse
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
FRONTEND_DIR = ROOT / "frontend"

SOURCE_ALIASES = {
    "great-circle": "great-circle",
    "great_circle": "great-circle",
    "greatcircle": "great-circle",
    "gc": "great-circle",
    "default": "great-circle",
    "road": "road",
}


def normalize_source(value):
    source = SOURCE_ALIASES.get((value or "great-circle").strip().lower())
    if not source:
        choices = ", ".join(sorted(SOURCE_ALIASES))
        raise argparse.ArgumentTypeError(
            f"unknown distance source {value!r}; expected one of: {choices}"
        )
    return source


def add_source_arg(parser):
    parser.add_argument(
        "--source",
        default=os.environ.get("DISTANCE_SOURCE", "great-circle"),
        type=normalize_source,
        help=(
            "Distance source to consume. Defaults to great-circle. "
            "Can also be set with DISTANCE_SOURCE=road."
        ),
    )


def source_suffix(source):
    return "" if normalize_source(source) == "great-circle" else "_road"


def distance_npz_path(source):
    suffix = source_suffix(source)
    return DATA_DIR / f"pixel_distances{suffix}.npz"


def frontend_path(source, stem, ext):
    suffix = source_suffix(source)
    return FRONTEND_DIR / f"{stem}{suffix}.{ext}"


def data_path(source, stem, ext):
    suffix = source_suffix(source)
    return DATA_DIR / f"{stem}{suffix}.{ext}"


def tiles_dir(source):
    suffix = source_suffix(source)
    return FRONTEND_DIR / f"tiles{suffix}"


def sorted_distance_cache_name(source):
    suffix = source_suffix(source)
    return f"dist_sorted{suffix}.npy"
