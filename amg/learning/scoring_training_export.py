"""
Export scoring/ranking training artifacts from canonical dataset splits.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from amg.config import TRAINING_DATASETS_DIR, TRAINING_SCORING_DIR
from amg.learning.training_registry import record_training_artifact


@dataclass
class ScoringExportStats:
    input_rows: int
    point_rows: int
    pair_rows: int
    points_path: Path
    pairs_path: Path


def export_scoring_training_dataset(
    dataset_name: str = "scoring_selection_v1",
    split: str = "all",
    output_prefix: Optional[str] = None,
    max_pairs_per_scene: Optional[int] = None,
) -> ScoringExportStats:
    safe_dataset = _safe_name(dataset_name)
    safe_split = _safe_name(split)
    src_path = TRAINING_DATASETS_DIR / safe_dataset / f"{safe_split}.jsonl"
    if not src_path.exists():
        raise FileNotFoundError(f"Dataset split not found: {src_path}")

    TRAINING_SCORING_DIR.mkdir(parents=True, exist_ok=True)
    prefix = _safe_name(output_prefix or f"{safe_dataset}_{safe_split}")
    points_path = TRAINING_SCORING_DIR / f"{prefix}_scoring_points.jsonl"
    pairs_path = TRAINING_SCORING_DIR / f"{prefix}_scoring_pairs.jsonl"

    input_rows = 0
    point_rows = 0
    candidates_by_scene: Dict[str, List[dict]] = {}

    with open(src_path) as src, open(points_path, "w") as points_out:
        for line in src:
            line = line.strip()
            if not line:
                continue
            input_rows += 1
            try:
                row = json.loads(line)
            except Exception:
                continue

            point = _to_point_example(row)
            if point is None:
                continue
            points_out.write(json.dumps(point) + "\n")
            point_rows += 1

            scene_id = point.get("scene_id") or "unknown_scene"
            candidates_by_scene.setdefault(scene_id, []).append(point)

    pair_rows = _write_pairs(
        candidates_by_scene,
        pairs_path,
        max_pairs_per_scene=max_pairs_per_scene,
    )

    record_training_artifact(
        "scoring_export_points",
        points_path,
        metadata={
            "dataset_name": dataset_name,
            "split": split,
            "rows": point_rows,
        },
    )
    record_training_artifact(
        "scoring_export_pairs",
        pairs_path,
        metadata={
            "dataset_name": dataset_name,
            "split": split,
            "rows": pair_rows,
            "max_pairs_per_scene": max_pairs_per_scene,
        },
    )

    return ScoringExportStats(
        input_rows=input_rows,
        point_rows=point_rows,
        pair_rows=pair_rows,
        points_path=points_path,
        pairs_path=pairs_path,
    )


def evaluate_scoring_dataset(dataset_name: str = "scoring_selection_v1", split: str = "val") -> dict:
    safe_dataset = _safe_name(dataset_name)
    safe_split = _safe_name(split)
    src_path = TRAINING_DATASETS_DIR / safe_dataset / f"{safe_split}.jsonl"
    if not src_path.exists():
        raise FileNotFoundError(f"Dataset split not found: {src_path}")

    total_rows = 0
    score_rows = 0
    decision_rows = 0
    keep_rows = 0
    maybe_rows = 0
    reject_rows = 0
    mae_sum = 0.0
    mae_rows = 0
    by_scene: Dict[str, Dict[str, int]] = {}

    with open(src_path) as src:
        for line in src:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            total_rows += 1
            label = row.get("label") if isinstance(row.get("label"), dict) else {}
            score = _to_float(label.get("score_100"))
            decision = _decision_rank(label.get("decision"))
            scene_id = str(row.get("scene_id") or "unknown_scene")
            by_scene.setdefault(scene_id, {"keep": 0, "maybe": 0, "reject": 0})
            if decision is not None:
                decision_rows += 1
                if decision == 2:
                    keep_rows += 1
                    by_scene[scene_id]["keep"] += 1
                elif decision == 1:
                    maybe_rows += 1
                    by_scene[scene_id]["maybe"] += 1
                else:
                    reject_rows += 1
                    by_scene[scene_id]["reject"] += 1
            if score is not None:
                score_rows += 1

            model_score = _extract_model_score(row)
            if score is not None and model_score is not None:
                mae_rows += 1
                mae_sum += abs(score - model_score)

    ranking_pairs_possible = 0
    for _, c in by_scene.items():
        ranking_pairs_possible += c["keep"] * c["reject"]
        ranking_pairs_possible += c["keep"] * c["maybe"]
        ranking_pairs_possible += c["maybe"] * c["reject"]

    return {
        "dataset_name": dataset_name,
        "split": split,
        "total_rows": total_rows,
        "score_labeled_rows": score_rows,
        "decision_labeled_rows": decision_rows,
        "keep_rows": keep_rows,
        "maybe_rows": maybe_rows,
        "reject_rows": reject_rows,
        "ranking_pairs_possible": ranking_pairs_possible,
        "model_score_mae": round(mae_sum / mae_rows, 2) if mae_rows else None,
        "model_score_mae_rows": mae_rows,
    }


def format_scoring_eval(metrics: dict) -> str:
    if metrics.get("total_rows", 0) == 0:
        return "No rows found in the requested scoring split."
    lines = []
    lines.append("Scoring Dataset Evaluation")
    lines.append("=" * 64)
    lines.append(f"Dataset: {metrics.get('dataset_name')}")
    lines.append(f"Split: {metrics.get('split')}")
    lines.append(f"Rows: {metrics.get('total_rows')}")
    lines.append(f"Score-labeled rows: {metrics.get('score_labeled_rows')}")
    lines.append(f"Decision-labeled rows: {metrics.get('decision_labeled_rows')}")
    lines.append(
        f"Decision mix: keep={metrics.get('keep_rows')} "
        f"maybe={metrics.get('maybe_rows')} reject={metrics.get('reject_rows')}"
    )
    lines.append(f"Ranking pairs possible: {metrics.get('ranking_pairs_possible')}")
    lines.append(
        f"Model-vs-operator score MAE: {metrics.get('model_score_mae')} "
        f"(rows={metrics.get('model_score_mae_rows')})"
    )
    return "\n".join(lines)


def _to_point_example(row: dict) -> Optional[dict]:
    label = row.get("label") if isinstance(row.get("label"), dict) else {}
    score = _to_float(label.get("score_100"))
    decision = _decision_rank(label.get("decision"))
    if score is None and decision is None:
        return None

    point = {
        "task": "frame_scoring",
        "sample_id": row.get("sample_id"),
        "scene_id": row.get("scene_id"),
        "filename": row.get("filename"),
        "image_path": row.get("image_path"),
        "source_type": row.get("source_type"),
        "features": {
            "position_label": label.get("position_label"),
            "penetration_visible": label.get("penetration_visible"),
            "categories": label.get("categories") or [],
            "tags": label.get("tags") or [],
            "notes": row.get("notes"),
        },
        "target": {
            "score_100": score,
            "decision_rank": decision,
        },
        "model_score_100": _extract_model_score(row),
    }
    return point


def _write_pairs(
    candidates_by_scene: Dict[str, List[dict]],
    pairs_path: Path,
    *,
    max_pairs_per_scene: Optional[int] = None,
) -> int:
    pair_rows = 0
    with open(pairs_path, "w") as out:
        for scene_id, rows in candidates_by_scene.items():
            scene_pairs = _pairs_for_scene(
                scene_id,
                rows,
                max_pairs=max_pairs_per_scene,
            )
            for pair in scene_pairs:
                out.write(json.dumps(pair) + "\n")
                pair_rows += 1
    return pair_rows


def _pairs_for_scene(scene_id: str, rows: List[dict], max_pairs: Optional[int]) -> List[dict]:
    n = len(rows)
    if n < 2:
        return []

    if max_pairs is None or max_pairs <= 0:
        out: List[dict] = []
        for i in range(n):
            for j in range(i + 1, n):
                pair = _pair_pref(scene_id, rows[i], rows[j])
                if pair is not None:
                    out.append(pair)
        return out

    rng = random.Random(str(scene_id))
    sample: List[dict] = []
    seen = 0
    limit = int(max_pairs)
    for i in range(n):
        for j in range(i + 1, n):
            pair = _pair_pref(scene_id, rows[i], rows[j])
            if pair is None:
                continue
            seen += 1
            if len(sample) < limit:
                sample.append(pair)
                continue
            replace_idx = rng.randint(0, seen - 1)
            if replace_idx < limit:
                sample[replace_idx] = pair
    return sample


def _pair_pref(scene_id: str, a: dict, b: dict) -> Optional[dict]:
    ar = a.get("target", {}).get("decision_rank")
    br = b.get("target", {}).get("decision_rank")
    ascore = a.get("target", {}).get("score_100")
    bscore = b.get("target", {}).get("score_100")

    preferred, other = _decide_preference((ar, ascore, a), (br, bscore, b))
    if preferred is None or other is None:
        return None
    return {
        "task": "frame_ranking",
        "scene_id": scene_id,
        "preferred": {
            "sample_id": preferred.get("sample_id"),
            "filename": preferred.get("filename"),
            "score_100": preferred.get("target", {}).get("score_100"),
            "decision_rank": preferred.get("target", {}).get("decision_rank"),
        },
        "other": {
            "sample_id": other.get("sample_id"),
            "filename": other.get("filename"),
            "score_100": other.get("target", {}).get("score_100"),
            "decision_rank": other.get("target", {}).get("decision_rank"),
        },
    }


def _decide_preference(a: Tuple[Optional[int], Optional[float], dict], b: Tuple[Optional[int], Optional[float], dict]):
    ar, ascore, arow = a
    br, bscore, brow = b
    if ar is not None and br is not None and ar != br:
        return (arow, brow) if ar > br else (brow, arow)
    if ascore is not None and bscore is not None and ascore != bscore:
        return (arow, brow) if ascore > bscore else (brow, arow)
    return (None, None)


def _decision_rank(raw: object) -> Optional[int]:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s == "keep":
        return 2
    if s == "maybe":
        return 1
    if s == "reject":
        return 0
    return None


def _extract_model_score(row: dict) -> Optional[float]:
    model = row.get("model")
    if isinstance(model, dict):
        v = _to_float(model.get("score_100"))
        if v is not None:
            return v
    v = row.get("model_score_100")
    return _to_float(v)


def _to_float(v: object) -> Optional[float]:
    if v is None:
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    if out < 0 or out > 100:
        return None
    return round(out, 2)


def _safe_name(s: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_." else "_" for c in (s or "dataset"))
    return out[:120] or "dataset"
