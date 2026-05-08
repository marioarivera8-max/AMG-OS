"""
Rule-pack evaluation, promotion, and rollback controls.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from amg.config import TRAINING_RULE_RUNS_DIR
from amg.learning.rule_evaluator import (
    evaluate_rule_pack_kpis,
    evaluate_rule_promotion_gates,
)
from amg.learning.rule_packs import (
    deactivate_rule_pack,
    load_rule_pack,
    save_rule_pack,
    set_active_rule_pack,
)
from amg.learning.training_registry import record_training_artifact


@dataclass
class RuleEvalRunResult:
    run_id: str
    manifest_path: Path
    rule_pack_id: str
    gates_passed: bool
    blocked_reasons: List[str]


def run_rule_eval(*, rule_pack_id: str, days_back: int = 30) -> RuleEvalRunResult:
    if not load_rule_pack(rule_pack_id):
        raise FileNotFoundError(f"Rule pack not found: {rule_pack_id}")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = TRAINING_RULE_RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    kpis = evaluate_rule_pack_kpis(rule_pack_id=rule_pack_id, days_back=days_back)
    recent_runs = [
        r
        for r in list_rule_eval_runs(limit=20)
        if str((r or {}).get("rule_pack_id") or "").strip() == rule_pack_id
    ]
    metric_dict = {
        "rule_pack_id": kpis.rule_pack_id,
        "days_back": kpis.days_back,
        "candidate": kpis.candidate,
        "baseline": kpis.baseline,
        "delta": kpis.delta,
        "recent_runs": recent_runs,
    }
    gates = evaluate_rule_promotion_gates(metric_dict)
    rollout = _evaluate_rollout_stage(
        rule_pack_id=rule_pack_id,
        consecutive_windows=int(gates.get("consecutive_windows_passing", 0) or 0),
        gates_pass=bool(gates.get("pass")),
    )
    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "rule_pack_id": rule_pack_id,
        "kpis": metric_dict,
        "gates": gates,
        "retrieval_rollout": rollout,
        "promotion": {"status": "not_promoted"},
    }
    manifest_path = run_dir / "rule_eval_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    record_training_artifact(
        "rule_eval_manifest",
        manifest_path,
        metadata={
            "run_id": run_id,
            "rule_pack_id": rule_pack_id,
            "gates_passed": bool(gates.get("pass")),
        },
    )
    return RuleEvalRunResult(
        run_id=run_id,
        manifest_path=manifest_path,
        rule_pack_id=rule_pack_id,
        gates_passed=bool(gates.get("pass")),
        blocked_reasons=list(gates.get("blocked_reasons") or []),
    )


def _evaluate_rollout_stage(*, rule_pack_id: str, consecutive_windows: int, gates_pass: bool) -> Dict[str, object]:
    pack = load_rule_pack(rule_pack_id) or {}
    constraints = (pack.get("constraints") or {}) if isinstance(pack, dict) else {}
    current = str(constraints.get("retrieval_stage") or "titles").strip().lower()
    sequence = ["off", "titles", "titles_description", "full"]
    if current not in sequence:
        current = "titles"
    if not gates_pass or consecutive_windows < 2:
        return {
            "current_stage": current,
            "next_stage": current,
            "advance_recommended": False,
            "reason": "requires_two_consecutive_passing_windows",
        }
    idx = sequence.index(current)
    if idx >= len(sequence) - 1:
        return {
            "current_stage": current,
            "next_stage": current,
            "advance_recommended": False,
            "reason": "already_full",
        }
    return {
        "current_stage": current,
        "next_stage": sequence[idx + 1],
        "advance_recommended": True,
        "reason": "gates_passed_two_windows",
    }


def list_rule_eval_runs(limit: int = 20) -> List[dict]:
    if not TRAINING_RULE_RUNS_DIR.exists():
        return []
    paths = sorted(
        TRAINING_RULE_RUNS_DIR.glob("*/rule_eval_manifest.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    out: List[dict] = []
    for p in paths[: max(1, int(limit))]:
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            continue
    return out


def promote_rule_pack_from_run(*, run_id: str, mode: str = "canary", canary_pct: float = 35.0) -> Dict[str, object]:
    manifest_path = TRAINING_RULE_RUNS_DIR / str(run_id) / "rule_eval_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Rule eval manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    gates = manifest.get("gates") if isinstance(manifest.get("gates"), dict) else {}
    if not bool(gates.get("pass")):
        raise RuntimeError("Rule pack eval gates did not pass; promotion blocked.")
    rule_pack_id = str(manifest.get("rule_pack_id") or "").strip()
    if not load_rule_pack(rule_pack_id):
        raise FileNotFoundError(f"Rule pack missing: {rule_pack_id}")
    set_active_rule_pack(rule_pack_id=rule_pack_id, mode=mode, canary_pct=canary_pct)
    manifest["promotion"] = {
        "status": "promoted",
        "mode": mode,
        "canary_pct": canary_pct,
        "activated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    record_training_artifact(
        "rule_pack_promoted",
        manifest_path,
        metadata={
            "run_id": run_id,
            "rule_pack_id": rule_pack_id,
            "mode": mode,
            "canary_pct": canary_pct,
        },
    )
    return manifest["promotion"]


def rollback_active_rule_pack() -> Dict[str, object]:
    deactivate_rule_pack()
    payload = {
        "status": "rolled_back",
        "at_utc": datetime.now(timezone.utc).isoformat(),
    }
    record_training_artifact(
        "rule_pack_rollback",
        TRAINING_RULE_RUNS_DIR / "rollback_event.json",
        metadata=payload,
    )
    return payload


def advance_rule_pack_retrieval_stage_from_run(*, run_id: str) -> Dict[str, object]:
    manifest_path = TRAINING_RULE_RUNS_DIR / str(run_id) / "rule_eval_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Rule eval manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    rollout = manifest.get("retrieval_rollout") if isinstance(manifest.get("retrieval_rollout"), dict) else {}
    if not bool(rollout.get("advance_recommended")):
        raise RuntimeError("Rollout stage advance not recommended for this run.")
    rule_pack_id = str(manifest.get("rule_pack_id") or "").strip()
    pack = load_rule_pack(rule_pack_id)
    if not pack:
        raise FileNotFoundError(f"Rule pack missing: {rule_pack_id}")
    constraints = dict((pack.get("constraints") or {}))
    next_stage = str(rollout.get("next_stage") or "").strip().lower()
    if not next_stage:
        raise RuntimeError("Missing next retrieval stage.")
    constraints["retrieval_stage"] = next_stage
    save_rule_pack(
        rule_pack_id=rule_pack_id,
        constraints=constraints,
        description=str(pack.get("description") or ""),
        source="rollout_stage_advance",
        metrics=pack.get("metrics") if isinstance(pack.get("metrics"), dict) else {},
        notes=str(pack.get("notes") or "") or None,
    )
    payload = {
        "status": "advanced",
        "rule_pack_id": rule_pack_id,
        "new_stage": next_stage,
        "from_run_id": run_id,
        "advanced_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    manifest["retrieval_rollout"] = {**rollout, **payload}
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    record_training_artifact(
        "rule_pack_rollout_stage_advanced",
        manifest_path,
        metadata=payload,
    )
    return payload
