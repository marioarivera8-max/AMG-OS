#!/usr/bin/env python3
"""Export text-style training examples from canonical dataset split."""

import argparse

from amg.learning.text_style_export import export_text_training_bundle, export_text_training_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description="Export text training dataset")
    parser.add_argument("--dataset-name", type=str, default="scoring_selection_v1")
    parser.add_argument("--split", type=str, default="all")
    parser.add_argument("--all-splits", action="store_true")
    parser.add_argument("--format", type=str, default="instruction", choices=["instruction", "messages"])
    parser.add_argument("--output-name", type=str, default=None)
    args = parser.parse_args()

    if bool(args.all_splits):
        bundle = export_text_training_bundle(
            dataset_name=args.dataset_name,
            splits=("train", "val", "test", "all"),
            format_type=args.format,
        )
        print(f"Format: {bundle.format_type}")
        print(f"Manifest: {bundle.manifest_path}")
        for split, out in bundle.outputs.items():
            print(f"{split}: {out}")
        print(
            f"Totals input={bundle.totals.get('input_rows', 0)} "
            f"exported={bundle.totals.get('exported_rows', 0)} "
            f"skipped={bundle.totals.get('skipped_rows', 0)}"
        )
        return 0 if bundle.totals.get("exported_rows", 0) > 0 else 1

    stats = export_text_training_dataset(
        dataset_name=args.dataset_name,
        split=args.split,
        output_name=args.output_name,
        format_type=args.format,
    )
    print(f"Format: {args.format}")
    print(f"Input rows: {stats.input_rows}")
    print(f"Exported: {stats.exported_rows}")
    print(f"Skipped: {stats.skipped_rows}")
    print(f"Output: {stats.output_path}")
    return 0 if stats.exported_rows > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
