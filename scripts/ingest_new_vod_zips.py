#!/usr/bin/env python3
"""Ingest newly dropped VOD cover ZIPs and print a compact summary."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from amg.config import TRAINING_EXAMPLES_DIR
from amg.learning.vod_cover_seed_ingest import ingest_vod_cover_seed_zip


def _safe_dataset_name(stem: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_." else "_" for c in stem)
    return "vodseed_" + cleaned.replace("-", "_")


def _load_manifest(path: Path) -> Dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {}
    out: Dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        zip_path = str(row.get("zip_path") or "").strip()
        if zip_path:
            out[zip_path] = row
    return out


def _bootstrap_from_existing_summaries() -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    root = TRAINING_EXAMPLES_DIR / "vod_cover_seed"
    if not root.exists():
        return out
    for p in root.glob("*_summary.json"):
        try:
            row = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        zip_path = str(row.get("zip_path") or "").strip()
        if not zip_path:
            continue
        out[zip_path] = {
            "zip_path": zip_path,
            "dataset_name": str(row.get("dataset_name") or ""),
            "accepted_rows": int(row.get("accepted_rows") or 0),
            "rejected_rows": int(row.get("rejected_rows") or 0),
            "appended_to_bank": int(row.get("appended_to_approved_bank") or 0),
            "seeded_from_summary": True,
            "summary_path": str(p),
            "processed_at_utc": str(row.get("generated_at_utc") or ""),
        }
    return out


def _write_manifest(path: Path, rows: Dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "rows": sorted(rows.values(), key=lambda x: str(x.get("zip_path") or "").lower()),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest newly dropped VOD ZIPs and skip known ones.")
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path.home() / "Downloads",
        help="Directory containing incoming ZIP files",
    )
    parser.add_argument(
        "--glob",
        type=str,
        default="drive-download-*.zip",
        help="Glob pattern inside source-dir",
    )
    parser.add_argument("--min-quality", type=float, default=1.8)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=TRAINING_EXAMPLES_DIR / "vod_cover_seed" / "ingest_manifest.json",
        help="Manifest used to remember processed ZIPs",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Process files even if manifest marks them as already processed",
    )
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    if not source_dir.exists():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    known = _bootstrap_from_existing_summaries()
    known.update(_load_manifest(args.manifest))

    zips = sorted(source_dir.glob(args.glob), key=lambda p: p.stat().st_mtime)
    processed_now: List[dict] = []
    skipped_known = 0
    skipped_missing = 0
    for zp in zips:
        if not zp.exists():
            skipped_missing += 1
            continue
        key = str(zp)
        if key in known and not bool(args.reprocess):
            skipped_known += 1
            continue
        dataset_name = _safe_dataset_name(zp.stem)
        stats = ingest_vod_cover_seed_zip(
            zip_path=zp,
            dataset_name=dataset_name,
            min_quality=float(args.min_quality),
            append_to_bank=True,
        )
        row = {
            "zip_path": key,
            "dataset_name": dataset_name,
            "processed_at_utc": datetime.now(timezone.utc).isoformat(),
            "input_entries": int(stats.input_entries),
            "image_entries": int(stats.image_entries),
            "accepted_rows": int(stats.accepted_rows),
            "rejected_rows": int(stats.rejected_rows),
            "appended_to_bank": int(stats.appended_to_bank),
            "skipped_non_images": int(stats.skipped_non_images),
            "summary_path": str(stats.summary_path) if stats.summary_path else None,
            "file_size_bytes": int(zp.stat().st_size),
            "file_mtime_epoch": float(zp.stat().st_mtime),
        }
        known[key] = row
        processed_now.append(row)

    _write_manifest(args.manifest, known)

    total_accepted = sum(int(x.get("accepted_rows") or 0) for x in processed_now)
    total_rejected = sum(int(x.get("rejected_rows") or 0) for x in processed_now)
    total_appended = sum(int(x.get("appended_to_bank") or 0) for x in processed_now)
    report = {
        "source_dir": str(source_dir),
        "glob": args.glob,
        "manifest": str(args.manifest),
        "matched_zip_count": len(zips),
        "processed_now": len(processed_now),
        "skipped_known": skipped_known,
        "skipped_missing": skipped_missing,
        "accepted_now": total_accepted,
        "rejected_now": total_rejected,
        "appended_now": total_appended,
        "runs": processed_now,
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
