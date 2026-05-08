#!/usr/bin/env python3
"""Ingest a ZIP of approved VOD cover images into training seed examples."""

from __future__ import annotations

import argparse
from pathlib import Path

from amg.learning.vod_cover_seed_ingest import ingest_vod_cover_seed_zip


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest approved cover ZIP into training seeds")
    parser.add_argument("zip_path", type=Path, help="Path to zip file containing cover images")
    parser.add_argument("--dataset-name", type=str, default="vod_cover_seed")
    parser.add_argument("--min-quality", type=float, default=1.8)
    parser.add_argument("--no-append-to-bank", action="store_true")
    args = parser.parse_args()

    stats = ingest_vod_cover_seed_zip(
        zip_path=args.zip_path,
        dataset_name=args.dataset_name,
        min_quality=float(args.min_quality),
        append_to_bank=not bool(args.no_append_to_bank),
    )
    print(f"Zip: {stats.zip_path}")
    print(f"Input entries: {stats.input_entries}")
    print(f"Image entries: {stats.image_entries}")
    print(f"Accepted: {stats.accepted_rows}")
    print(f"Rejected: {stats.rejected_rows}")
    print(f"Skipped non-images: {stats.skipped_non_images}")
    if stats.accepted_path:
        print(f"Accepted path: {stats.accepted_path}")
    if stats.rejected_path:
        print(f"Rejected path: {stats.rejected_path}")
    if stats.summary_path:
        print(f"Summary path: {stats.summary_path}")
    if stats.appended_to_bank:
        print(f"Appended to approved bank: {stats.appended_to_bank}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
