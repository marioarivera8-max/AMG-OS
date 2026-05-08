#!/usr/bin/env python3
"""Curate a .docx/.txt/.md metadata examples document into accepted/rejected JSONL."""

from pathlib import Path
import argparse

from amg.learning.doc_example_curator import curate_examples_document


def main() -> int:
    parser = argparse.ArgumentParser(description="Curate examples from Word/text docs")
    parser.add_argument("input_path", type=Path, help="Path to .docx/.txt/.md")
    parser.add_argument("--dataset-name", type=str, default="word_doc_examples")
    parser.add_argument("--min-quality", type=float, default=3.0)
    parser.add_argument("--dedupe-threshold", type=float, default=0.86)
    parser.add_argument("--append-to-bank", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    stats = curate_examples_document(
        input_path=args.input_path,
        dataset_name=args.dataset_name,
        min_quality=float(args.min_quality),
        dedupe_threshold=float(args.dedupe_threshold),
        append_to_bank=bool(args.append_to_bank),
        dry_run=bool(args.dry_run),
    )
    print(f"Input records: {stats.input_records}")
    print(f"Accepted: {stats.accepted_rows}")
    print(f"Rejected: {stats.rejected_rows}")
    print(f"Duplicates: {stats.duplicate_rows}")
    if stats.output_dir:
        print(f"Output dir: {stats.output_dir}")
    if stats.accepted_path:
        print(f"Accepted path: {stats.accepted_path}")
    if stats.rejected_path:
        print(f"Rejected path: {stats.rejected_path}")
    if stats.summary_path:
        print(f"Summary path: {stats.summary_path}")
    if stats.appended_to_bank:
        print(f"Appended to approved bank: {stats.appended_to_bank}")
    return 0 if stats.accepted_rows > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
