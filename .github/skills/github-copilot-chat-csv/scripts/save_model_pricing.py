#!/usr/bin/env python3
"""Save the GitHub Copilot model-pricing snapshot for later use."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

DEFAULT_SOURCE = Path(__file__).with_name("copilot_model_pricing.csv")
DEFAULT_DESTINATION = Path(__file__).with_name("saved_model_pricing.csv")
REQUIRED_COLUMNS = (
    "Provider",
    "Model",
    "ReleaseStatus",
    "Category",
    "Tier",
    "InputTokenThreshold",
    "InputPricePer1M",
    "CachedInputPricePer1M",
    "CacheWritePricePer1M",
    "OutputPricePer1M",
    "Source",
)


def save_model_pricing(source: Path, destination: Path) -> int:
    """Copy and validate the pricing snapshot, returning the number of rows saved."""
    with source.open("r", encoding="utf-8-sig", newline="") as input_file:
        reader = csv.DictReader(input_file)
        if tuple(reader.fieldnames or ()) != REQUIRED_COLUMNS:
            raise ValueError(
                f"Unexpected pricing columns in {source}; "
                f"expected {REQUIRED_COLUMNS!r}"
            )
        rows = list(reader)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=REQUIRED_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        type=Path,
        nargs="?",
        default=DEFAULT_SOURCE,
        help=f"Pricing snapshot to save (default: {DEFAULT_SOURCE.name})",
    )
    parser.add_argument(
        "destination",
        type=Path,
        nargs="?",
        default=DEFAULT_DESTINATION,
        help=f"CSV file to create (default: {DEFAULT_DESTINATION.name})",
    )
    args = parser.parse_args()
    row_count = save_model_pricing(args.source, args.destination)
    print(f"Saved {row_count} model-pricing row(s) to {args.destination}")


if __name__ == "__main__":
    main()
