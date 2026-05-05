"""
Lightweight evaluation harness for text training datasets.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from amg.config import TRAINING_DATASETS_DIR


def evaluate_text_dataset(dataset_name: str = "scoring_selection_v1", split: str = "val") -> Dict[str, object]:
    path = TRAINING_DATASETS_DIR / _safe_name(dataset_name) / f"{_safe_name(split)}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"Dataset split not found: {path}")

    rows: List[dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue

    total = len(rows)
    if total == 0:
        return {"dataset_name": dataset_name, "split": split, "total_rows": 0}

    with_title = 0
    with_description = 0
    with_categories = 0
    with_tags = 0
    performer_rows = 0
    performer_in_title = 0
    unique_titles = set()

    for row in rows:
        title = _s(row.get("title"))
        description = _s(row.get("description"))
        label = row.get("label") if isinstance(row.get("label"), dict) else {}
        categories = _as_list(label.get("categories"))
        tags = _as_list(label.get("tags"))
        performers = _as_list(row.get("performers"))

        if title:
            with_title += 1
            unique_titles.add(title.lower())
        if description:
            with_description += 1
        if categories:
            with_categories += 1
        if tags:
            with_tags += 1
        if performers:
            performer_rows += 1
            ttl = (title or "").lower()
            if any(p.lower() in ttl for p in performers if p):
                performer_in_title += 1

    return {
        "dataset_name": dataset_name,
        "split": split,
        "total_rows": total,
        "title_rate": _pct(with_title, total),
        "description_rate": _pct(with_description, total),
        "categories_rate": _pct(with_categories, total),
        "tags_rate": _pct(with_tags, total),
        "performer_rows": performer_rows,
        "performer_in_title_rate": _pct(performer_in_title, performer_rows) if performer_rows else None,
        "title_unique_ratio": round(len(unique_titles) / total, 3),
    }


def format_text_eval(metrics: Dict[str, object]) -> str:
    if metrics.get("total_rows", 0) == 0:
        return "No rows found in the requested split."
    lines = []
    lines.append("Text Dataset Evaluation")
    lines.append("=" * 64)
    lines.append(f"Dataset: {metrics.get('dataset_name')}")
    lines.append(f"Split: {metrics.get('split')}")
    lines.append(f"Rows: {metrics.get('total_rows')}")
    lines.append(f"Title coverage: {metrics.get('title_rate')}%")
    lines.append(f"Description coverage: {metrics.get('description_rate')}%")
    lines.append(f"Categories coverage: {metrics.get('categories_rate')}%")
    lines.append(f"Tags coverage: {metrics.get('tags_rate')}%")
    lines.append(f"Performer-in-title rate: {metrics.get('performer_in_title_rate')}%")
    lines.append(f"Title uniqueness ratio: {metrics.get('title_unique_ratio')}")
    return "\n".join(lines)


def _s(v: object) -> str:
    return str(v).strip() if v is not None else ""


def _as_list(v: object) -> List[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


def _pct(num: int, den: int) -> float:
    if den <= 0:
        return 0.0
    return round((num / den) * 100.0, 1)


def _safe_name(s: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_" else "_" for c in (s or "dataset"))
    return out[:120] or "dataset"
