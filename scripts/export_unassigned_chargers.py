"""
Export Superchargers that do not spatially assign to a country.

This is the charger-only version of the country assignment step. It does not
touch the 370M population cells, so it should run quickly.

Run from project root:
    .venv/Scripts/python scripts/export_unassigned_chargers.py
"""

from precompute_country_stats import (
    aggregate_chargers,
    load_country_features,
    prepare_country_shapes,
)


def main():
    features = load_country_features()
    height, width, transform, shapes = prepare_country_shapes(features)
    aggregate_chargers(features, height, width, transform, shapes)


if __name__ == "__main__":
    main()
