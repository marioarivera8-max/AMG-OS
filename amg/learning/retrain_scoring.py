"""
Manual-trigger scoring retrain orchestration.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from amg.config import (
    TRAINING_FLAGS_PATH,
    TRAINING_RETRAIN_RUNS_DIR,
    TRAINING_ACTIVE_SCORER_PATH,
)
from amg.learning.dataset_builder import build_training_dataset
from amg.learning.scoring_training_export import export_scoring_training_dataset, evaluate_scoring_dataset
from amg.learning.training_registry import record_training_artifact


VALID_FLAG_TAGS = {"golden_positive", "golden_negative"}


@dataclass
class RetrainRunResult:
    run_id: str
    manifest_path: Path
    candidate_ready: bool
    blocked_reasons: List[str]
    dataset_name: str
    points_path: Path
    pairs_path: Path


def append_retrain_flag(
    *,
    scene_id: str,
    tag: str,
    note: Optional[str] = None,
    source: str = "cli",
) -> dict:
    clean_tag = (tag or "").strip().lower()
    if clean_tag not in VALID_FLAG_TAGS:
        raise ValueError(f"Invalid tag: {tag}. Expected one of: {sorted(VALID_FLAG_TAGS)}")
    row = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "scene_id": str(scene_id).strip(),
        "tag": clean_tag,
        "note": (note or "").strip() or None,
        "source": source,
    }
    TRAINING_FLAGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TRAINING_FLAGS_PATH, "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def read_retrain_flags(limit: int = 100) -> List[dict]:
    if not TRAINING_FLAGS_PATH.exists():
        return []
    rows: List[dict] = []
    with open(TRAINING_FLAGS_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    if limit > 0:
        return rows[-limit:]
    return rows


def run_scoring_retrain(
    *,
    from_scene_id: Optional[str] = None,
    tag: str = "golden_positive",
    note: Optional[str] = None,
    dataset_prefix: str = "scoring_retrain",
    max_pairs_per_scene: int = 300,
    gate_config: Optional[Dict[str, float]] = None,
) -> RetrainRunResult:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = TRAINING_RETRAIN_RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    flag_row = None
    if from_scene_id:
        flag_row = append_retrain_flag(scene_id=from_scene_id, tag=tag, note=note, source="retrain-score")

    dataset_name = _safe_name(f"{dataset_prefix}_{run_id}")
    build_stats = build_training_dataset(dataset_name=dataset_name, val_pct=0.1, test_pct=0.1)
    export_stats = export_scoring_training_dataset(
        dataset_name=dataset_name,
        split="all",
        output_prefix=f"{dataset_name}_all",
        max_pairs_per_scene=max_pairs_per_scene,
    )
    eval_metrics = evaluate_scoring_dataset(dataset_name=dataset_name, split="val")

    gates = evaluate_retrain_gates(eval_metrics, config=gate_config)
    manifest = {
        "run_id": run_id,
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "input_flag": flag_row,
        "dataset_name": dataset_name,
        "build": {
            "total_rows": build_stats.total_rows,
            "train_rows": build_stats.train_rows,
            "val_rows": build_stats.val_rows,
            "test_rows": build_stats.test_rows,
            "output_dir": str(build_stats.output_dir),
        },
        "export": {
            "input_rows": export_stats.input_rows,
            "point_rows": export_stats.point_rows,
            "pair_rows": export_stats.pair_rows,
            "points_path": str(export_stats.points_path),
            "pairs_path": str(export_stats.pairs_path),
        },
        "eval_val": eval_metrics,
        "gates": gates,
        "candidate_ready": gates["pass"],
        "promotion": {"status": "not_promoted"},
    }
    manifest_path = run_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    record_training_artifact(
        "scoring_retrain_run_manifest",
        manifest_path,
        metadata={
            "run_id": run_id,
            "dataset_name": dataset_name,
            "candidate_ready": bool(gates["pass"]),
            "blocked_reasons": gates.get("blocked_reasons", []),
        },
    )
    return RetrainRunResult(
        run_id=run_id,
        manifest_path=manifest_path,
        candidate_ready=bool(gates["pass"]),
        blocked_reasons=list(gates.get("blocked_reasons", [])),
        dataset_name=dataset_name,
        points_path=export_stats.points_path,
        pairs_path=export_stats.pairs_path,
    )


def evaluate_retrain_gates(metrics: Dict[str, object], config: Optional[Dict[str, float]] = None) -> dict:
    cfg = {
        "min_decision_rows": 40,
        "min_keep_rows": 10,
        "min_maybe_rows": 5,
        "min_reject_rows": 10,
        "min_pairs_possible": 100,
        "max_model_score_mae": 18.0,
    }
    if config:
        cfg.update(config)

    blocked: List[str] = []
    if int(metrics.get("decision_labeled_rows") or 0) < int(cfg["min_decision_rows"]):
        blocked.append("decision_labeled_rows_below_threshold")
    if int(metrics.get("keep_rows") or 0) < int(cfg["min_keep_rows"]):
        blocked.append("keep_rows_below_threshold")
    if int(metrics.get("maybe_rows") or 0) < int(cfg["min_maybe_rows"]):
        blocked.append("maybe_rows_below_threshold")
    if int(metrics.get("reject_rows") or 0) < int(cfg["min_reject_rows"]):
        blocked.append("reject_rows_below_threshold")
    if int(metrics.get("ranking_pairs_possible") or 0) < int(cfg["min_pairs_possible"]):
        blocked.append("ranking_pairs_possible_below_threshold")

    mae = metrics.get("model_score_mae")
    if mae is None:
        blocked.append("model_score_mae_missing")
    else:
        try:
            if float(mae) > float(cfg["max_model_score_mae"]):
                blocked.append("model_score_mae_above_threshold")
        except (TypeError, ValueError):
            blocked.append("model_score_mae_invalid")

    return {
        "pass": len(blocked) == 0,
        "blocked_reasons": blocked,
        "config": cfg,
    }


def list_retrain_runs(limit: int = 20) -> List[dict]:
    if not TRAINING_RETRAIN_RUNS_DIR.exists():
        return []
    manifests = sorted(
        TRAINING_RETRAIN_RUNS_DIR.glob("*/manifest.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    out = []
    for p in manifests[: max(1, int(limit))]:
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            continue
    return out


def promote_score_candidate(run_id: str) -> dict:
    run_path = TRAINING_RETRAIN_RUNS_DIR / str(run_id) / "manifest.json"
    if not run_path.exists():
        raise FileNotFoundError(f"Run manifest not found: {run_path}")
    manifest = json.loads(run_path.read_text())
    if not bool(manifest.get("candidate_ready")):
        raise RuntimeError("Candidate is blocked by retrain gates; cannot promote.")

    pointer = {
        "activated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": manifest.get("run_id"),
        "dataset_name": manifest.get("dataset_name"),
        "manifest_path": str(run_path),
        "points_path": (manifest.get("export") or {}).get("points_path"),
        "pairs_path": (manifest.get("export") or {}).get("pairs_path"),
    }
    TRAINING_ACTIVE_SCORER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TRAINING_ACTIVE_SCORER_PATH, "w") as f:
        json.dump(pointer, f, indent=2)

    manifest["promotion"] = {
        "status": "promoted",
        "activated_at_utc": pointer["activated_at_utc"],
    }
    with open(run_path, "w") as f:
        json.dump(manifest, f, indent=2)

    record_training_artifact(
        "scoring_candidate_promoted",
        TRAINING_ACTIVE_SCORER_PATH,
        metadata={"run_id": pointer["run_id"], "manifest_path": pointer["manifest_path"]},
    )
    return pointer


def _safe_name(s: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_" else "_" for c in (s or "retrain"))
    return out[:120] or "retrain"
