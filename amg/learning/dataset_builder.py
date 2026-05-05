"""
Build canonical train/val/test dataset from learning signals.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from amg.config import OPERATOR_FEEDBACK_PATH, TRAINING_DATASETS_DIR, TRAINING_EXAMPLES_DIR
from amg.learning.training_registry import record_training_artifact


@dataclass
class BuildStats:
    total_rows: int
    train_rows: int
    val_rows: int
    test_rows: int
    output_dir: Path


def build_training_dataset(
    dataset_name: str = "scoring_v1",
    val_pct: float = 0.1,
    test_pct: float = 0.1,
) -> BuildStats:
    rows = _collect_rows()
    out_dir = TRAINING_DATASETS_DIR / _safe_name(dataset_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    train, val, test = _split_rows(rows, val_pct=val_pct, test_pct=test_pct)

    _write_jsonl(out_dir / "all.jsonl", rows)
    _write_jsonl(out_dir / "train.jsonl", train)
    _write_jsonl(out_dir / "val.jsonl", val)
    _write_jsonl(out_dir / "test.jsonl", test)
    _write_manifest(
        out_dir / "manifest.json",
        dataset_name=dataset_name,
        total=len(rows),
        train=len(train),
        val=len(val),
        test=len(test),
        val_pct=val_pct,
        test_pct=test_pct,
    )
    record_training_artifact(
        "dataset_build",
        out_dir / "manifest.json",
        metadata={
            "dataset_name": dataset_name,
            "total_rows": len(rows),
            "train_rows": len(train),
            "val_rows": len(val),
            "test_rows": len(test),
        },
    )

    return BuildStats(
        total_rows=len(rows),
        train_rows=len(train),
        val_rows=len(val),
        test_rows=len(test),
        output_dir=out_dir,
    )


def _collect_rows() -> List[dict]:
    out: List[dict] = []
    out.extend(_from_feedback())
    out.extend(_from_personal_examples())
    return out


def _from_feedback() -> List[dict]:
    if not OPERATOR_FEEDBACK_PATH.exists():
        return []
    rows: List[dict] = []
    with open(OPERATOR_FEEDBACK_PATH) as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except Exception:
                continue
            rows.append(
                {
                    "sample_id": f"feedback_{line_num}",
                    "source_type": "feedback",
                    "scene_id": raw.get("scene_id"),
                    "filename": raw.get("filename"),
                    "image_path": raw.get("image_path"),
                    "model": raw.get("model", {}),
                    "operator": raw.get("operator", {}),
                    "label": {
                        "decision": raw.get("operator", {}).get("decision"),
                        "score_100": _score(raw.get("operator", {}).get("score_100")),
                        "position_label": raw.get("operator", {}).get("position_label"),
                        "penetration_visible": raw.get("operator", {}).get("penetration_visible"),
                    },
                    "notes": raw.get("operator", {}).get("notes"),
                }
            )
    return rows


def _from_personal_examples() -> List[dict]:
    if not TRAINING_EXAMPLES_DIR.exists():
        return []
    rows: List[dict] = []
    files = sorted(TRAINING_EXAMPLES_DIR.glob("*.jsonl"))
    for file_path in files:
        with open(file_path) as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except Exception:
                    continue
                sid = raw.get("scene_id")
                fn = raw.get("filename")
                rows.append(
                    {
                        "sample_id": f"{file_path.stem}_{line_num}",
                        "source_type": "personal_example",
                        "source_file": file_path.name,
                        "source_sheet": raw.get("source_sheet"),
                        "source_row_num": raw.get("source_row_num"),
                        "source_links": raw.get("source_links") or [],
                        "scene_id": sid,
                        "filename": fn,
                        "image_path": raw.get("image_path"),
                        "label": {
                            "decision": raw.get("decision"),
                            "score_100": _score(raw.get("operator_score_100")),
                            "categories": raw.get("categories") or [],
                            "tags": raw.get("tags") or [],
                            "should_have_picked": raw.get("should_have_picked"),
                            "record_quality": raw.get("record_quality"),
                        },
                        "title": raw.get("title"),
                        "description": raw.get("description"),
                        "studio": raw.get("studio"),
                        "performers": raw.get("performers") or [],
                        "notes": raw.get("notes"),
                    }
                )
    return rows


def _split_rows(rows: Sequence[dict], val_pct: float, test_pct: float):
    train, val, test = [], [], []
    for row in rows:
        token = row.get("scene_id") or row.get("filename") or row.get("sample_id") or ""
        bucket = _bucket(token)
        if bucket < test_pct:
            test.append(row)
        elif bucket < (test_pct + val_pct):
            val.append(row)
        else:
            train.append(row)
    return train, val, test


def _bucket(token: str) -> float:
    h = hashlib.sha1(token.encode("utf-8")).hexdigest()
    v = int(h[:8], 16)
    return (v % 1000) / 1000.0


def _write_jsonl(path: Path, rows: Sequence[dict]) -> None:
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _write_manifest(
    path: Path,
    *,
    dataset_name: str,
    total: int,
    train: int,
    val: int,
    test: int,
    val_pct: float,
    test_pct: float,
) -> None:
    payload = {
        "dataset_name": dataset_name,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_rows": total,
        "split": {"train": train, "val": val, "test": test},
        "split_config": {"val_pct": val_pct, "test_pct": test_pct},
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def _score(v: Optional[object]) -> Optional[float]:
    if v is None:
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    if 0 <= out <= 10:
        out *= 10.0
    if out < 0 or out > 100:
        return None
    return round(out, 1)


def _safe_name(s: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_" else "_" for c in (s or "dataset"))
    return out[:80] or "dataset"
