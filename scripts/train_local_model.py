#!/usr/bin/env python3
"""Prepare scoring training artifacts (points + ranking pairs)."""

import argparse

from amg.learning.scoring_training_export import export_scoring_training_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description="Export scoring training artifacts")
    parser.add_argument("--dataset-name", type=str, default="scoring_selection_v1")
    parser.add_argument("--split", type=str, default="all")
    parser.add_argument("--output-prefix", type=str, default=None)
    args = parser.parse_args()

    stats = export_scoring_training_dataset(
        dataset_name=args.dataset_name,
        split=args.split,
        output_prefix=args.output_prefix,
    )
    print(f"Input rows: {stats.input_rows}")
    print(f"Point rows: {stats.point_rows}")
    print(f"Pair rows: {stats.pair_rows}")
    print(f"Points output: {stats.points_path}")
    print(f"Pairs output: {stats.pairs_path}")
    return 0 if stats.point_rows > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
