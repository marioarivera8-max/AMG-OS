#!/usr/bin/env python3
"""Build canonical train/val/test training dataset."""

import argparse

from amg.learning.dataset_builder import build_training_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description="Build AMG training dataset")
    parser.add_argument("--dataset-name", type=str, default="scoring_v1")
    parser.add_argument("--val-pct", type=float, default=0.10)
    parser.add_argument("--test-pct", type=float, default=0.10)
    args = parser.parse_args()

    stats = build_training_dataset(
        dataset_name=args.dataset_name,
        val_pct=float(args.val_pct),
        test_pct=float(args.test_pct),
    )
    print(f"Total rows: {stats.total_rows}")
    print(f"Train: {stats.train_rows}")
    print(f"Val: {stats.val_rows}")
    print(f"Test: {stats.test_rows}")
    print(f"Output dir: {stats.output_dir}")
    return 0 if stats.total_rows > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
