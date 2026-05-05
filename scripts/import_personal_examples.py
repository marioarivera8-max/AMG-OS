#!/usr/bin/env python3
"""Import personal labeled examples into data/training/examples (CSV/JSONL/XLSX)."""

from pathlib import Path
import argparse

from amg.learning.import_personal_examples import import_personal_examples


def main() -> int:
    parser = argparse.ArgumentParser(description="Import personal examples (CSV/JSONL/XLSX)")
    parser.add_argument("input_path", type=Path, help="Path to CSV, JSONL, or XLSX")
    parser.add_argument("--dataset-name", type=str, default="personal_examples")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    stats = import_personal_examples(
        input_path=args.input_path,
        dataset_name=args.dataset_name,
        dry_run=bool(args.dry_run),
    )
    print(f"Input rows: {stats.input_rows}")
    print(f"Accepted: {stats.accepted_rows}")
    print(f"Skipped: {stats.skipped_rows}")
    print(f"Errors: {stats.errors}")
    if stats.output_path:
        print(f"Output: {stats.output_path}")
    return 0 if stats.accepted_rows > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
