"""
Rule-pack KPI evaluation from local reviewed + feedback artifacts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

from amg.config import OPERATOR_FEEDBACK_PATH, REVIEWED_DIR, DATA_DIR


@dataclass
class RuleKpiResult:
    rule_pack_id: str
    days_back: int
    candidate: Dict[str, Any]
    baseline: Dict[str, Any]
    delta: Dict[str, Any]


def evaluate_rule_pack_kpis(*, rule_pack_id: str, days_back: int = 30) -> RuleKpiResult:
    reviewed = _load_reviewed_rows(days_back=days_back)
    candidate_rows = [r for r in reviewed if str((r or {}).get("rule_pack_id") or "") == rule_pack_id]
    baseline_rows = [r for r in reviewed if str((r or {}).get("rule_pack_id") or "") != rule_pack_id]

    candidate_scene_ids = {str((r or {}).get("scene_id") or "") for r in candidate_rows if str((r or {}).get("scene_id") or "")}
    baseline_scene_ids = {str((r or {}).get("scene_id") or "") for r in baseline_rows if str((r or {}).get("scene_id") or "")}
    feedback_rows = _load_feedback_rows(days_back=days_back)

    candidate_metrics = _compute_metrics(candidate_rows, feedback_rows, candidate_scene_ids)
    baseline_metrics = _compute_metrics(baseline_rows, feedback_rows, baseline_scene_ids)
    delta = _delta_metrics(candidate_metrics, baseline_metrics)
    return RuleKpiResult(
        rule_pack_id=rule_pack_id,
        days_back=days_back,
        candidate=candidate_metrics,
        baseline=baseline_metrics,
        delta=delta,
    )


def format_rule_kpi_report(result: RuleKpiResult) -> str:
    c = result.candidate
    b = result.baseline
    d = result.delta
    lines = []
    lines.append("Rule Pack KPI Report")
    lines.append("=" * 64)
    lines.append(f"Rule pack: {result.rule_pack_id}")
    lines.append(f"Window: last {result.days_back} days")
    lines.append("")
    lines.append("Candidate cohort:")
    lines.append(f"  scenes: {c.get('scene_count')}")
    lines.append(f"  title unchanged rate: {c.get('title_unchanged_rate_pct')}%")
    lines.append(f"  description unchanged rate: {c.get('description_unchanged_rate_pct')}%")
    lines.append(f"  metadata acceptance rate: {c.get('metadata_acceptance_rate_pct')}%")
    lines.append(f"  median title edit distance: {c.get('median_title_edit_distance')}")
    lines.append(f"  median description edit distance: {c.get('median_description_edit_distance')}")
    lines.append(f"  metadata blockers per scene: {c.get('avg_metadata_blockers')}")
    lines.append(f"  metadata blocker severity per scene: {c.get('avg_metadata_blocker_severity')}")
    lines.append(f"  metadata ready rate: {c.get('metadata_ready_rate_pct')}%")
    lines.append(f"  title-set diversity: {c.get('title_set_diversity')}")
    lines.append(f"  reject decision rate: {c.get('feedback_reject_rate_pct')}%")
    lines.append("")
    lines.append("Baseline cohort:")
    lines.append(f"  scenes: {b.get('scene_count')}")
    lines.append(f"  title unchanged rate: {b.get('title_unchanged_rate_pct')}%")
    lines.append(f"  description unchanged rate: {b.get('description_unchanged_rate_pct')}%")
    lines.append(f"  metadata acceptance rate: {b.get('metadata_acceptance_rate_pct')}%")
    lines.append(f"  median title edit distance: {b.get('median_title_edit_distance')}")
    lines.append(f"  median description edit distance: {b.get('median_description_edit_distance')}")
    lines.append(f"  metadata blockers per scene: {b.get('avg_metadata_blockers')}")
    lines.append(f"  metadata blocker severity per scene: {b.get('avg_metadata_blocker_severity')}")
    lines.append(f"  metadata ready rate: {b.get('metadata_ready_rate_pct')}%")
    lines.append(f"  title-set diversity: {b.get('title_set_diversity')}")
    lines.append(f"  reject decision rate: {b.get('feedback_reject_rate_pct')}%")
    lines.append("")
    lines.append("Delta (candidate - baseline):")
    lines.append(f"  title unchanged: {d.get('title_unchanged_rate_pct_delta'):+.2f} pts")
    lines.append(f"  description unchanged: {d.get('description_unchanged_rate_pct_delta'):+.2f} pts")
    lines.append(f"  metadata acceptance: {d.get('metadata_acceptance_rate_pct_delta'):+.2f} pts")
    lines.append(f"  median title edit distance: {d.get('median_title_edit_distance_delta'):+.4f}")
    lines.append(f"  median description edit distance: {d.get('median_description_edit_distance_delta'):+.4f}")
    lines.append(f"  blockers per scene: {d.get('avg_metadata_blockers_delta'):+.2f}")
    lines.append(f"  blocker severity per scene: {d.get('avg_metadata_blocker_severity_delta'):+.2f}")
    lines.append(f"  metadata ready rate: {d.get('metadata_ready_rate_pct_delta'):+.2f} pts")
    lines.append(f"  title-set diversity: {d.get('title_set_diversity_delta'):+.4f}")
    lines.append(f"  reject rate: {d.get('feedback_reject_rate_pct_delta'):+.2f} pts")
    return "\n".join(lines)


def evaluate_rule_promotion_gates(metrics: Dict[str, Any]) -> Dict[str, Any]:
    delta = metrics.get("delta") if isinstance(metrics.get("delta"), dict) else {}
    blocked: List[str] = []
    candidate = metrics.get("candidate") if isinstance(metrics.get("candidate"), dict) else {}
    if float(delta.get("metadata_ready_rate_pct_delta", 0.0)) < -2.0:
        blocked.append("metadata_ready_rate_regressed")
    if float(delta.get("avg_metadata_blockers_delta", 0.0)) > 0.25:
        blocked.append("metadata_blockers_increased")
    if float(delta.get("avg_metadata_blocker_severity_delta", 0.0)) > 0.35:
        blocked.append("metadata_blocker_severity_increased")
    if float(delta.get("metadata_acceptance_rate_pct_delta", 0.0)) < -3.0:
        blocked.append("metadata_acceptance_rate_regressed")
    if float(delta.get("median_title_edit_distance_delta", 0.0)) > 0.05:
        blocked.append("title_edit_distance_regressed")
    if float(delta.get("median_description_edit_distance_delta", 0.0)) > 0.05:
        blocked.append("description_edit_distance_regressed")
    if float(delta.get("feedback_reject_rate_pct_delta", 0.0)) > 3.0:
        blocked.append("feedback_reject_rate_increased")
    if float(candidate.get("scene_count", 0)) < 5:
        blocked.append("insufficient_candidate_scenes")
    if float(candidate.get("metadata_acceptance_rate_pct", 0.0)) < 55.0:
        blocked.append("metadata_acceptance_rate_too_low")
    provisional_pass = len(blocked) == 0
    consecutive = _consecutive_windows_passing(metrics, provisional_pass=provisional_pass)
    if consecutive < 2:
        blocked.append("needs_two_consecutive_eval_windows")
    return {
        "pass": len(blocked) == 0,
        "blocked_reasons": blocked,
        "consecutive_windows_passing": consecutive,
    }


def _load_reviewed_rows(*, days_back: int) -> List[dict]:
    if not REVIEWED_DIR.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days_back)))
    out: List[dict] = []
    for p in REVIEWED_DIR.glob("*.json"):
        try:
            with open(p) as f:
                row = json.load(f)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        ts = _parse_ts(str(row.get("timestamp") or ""))
        if ts and ts < cutoff:
            continue
        out.append(row)
    return out


def _load_feedback_rows(*, days_back: int) -> List[dict]:
    if not OPERATOR_FEEDBACK_PATH.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days_back)))
    out: List[dict] = []
    with open(OPERATOR_FEEDBACK_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            ts = _parse_ts(str((row or {}).get("timestamp") or ""))
            if ts and ts < cutoff:
                continue
            out.append(row)
    return out


def _parse_ts(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _load_insight_for_scene(scene_id: str) -> Optional[dict]:
    p = DATA_DIR / "work_dirs" / str(scene_id) / "insight.json"
    if not p.exists():
        return None
    try:
        with open(p) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _compute_metrics(reviewed_rows: List[dict], feedback_rows: List[dict], scene_ids: set[str]) -> Dict[str, Any]:
    scene_count = len(scene_ids)
    if scene_count == 0:
        return {
            "scene_count": 0,
            "title_unchanged_rate_pct": 0.0,
            "description_unchanged_rate_pct": 0.0,
            "metadata_acceptance_rate_pct": 0.0,
            "median_title_edit_distance": 0.0,
            "median_description_edit_distance": 0.0,
            "avg_metadata_blockers": 0.0,
            "avg_metadata_blocker_severity": 0.0,
            "metadata_ready_rate_pct": 0.0,
            "title_set_diversity": 0.0,
            "feedback_reject_rate_pct": 0.0,
        }

    title_unchanged = 0
    desc_unchanged = 0
    accepted_rows = 0
    title_edit_distances: List[float] = []
    desc_edit_distances: List[float] = []
    blocker_count = 0
    blocker_severity = 0.0
    ready_count = 0
    diversity_values: List[float] = []
    for r in reviewed_rows:
        sid = str((r or {}).get("scene_id") or "")
        if not sid:
            continue
        insight = _load_insight_for_scene(sid) or {}
        title = str((r or {}).get("title_override") or "").strip()
        ai_titles = [str((x or {}).get("text") or "").strip() for x in (insight.get("ai_titles") or []) if isinstance(x, dict)]
        if title and any(title == ai for ai in ai_titles):
            title_unchanged += 1
        desc = str((r or {}).get("long_description") or "").strip()
        ai_desc = str(insight.get("long_description") or "").strip()
        if desc and ai_desc and desc == ai_desc:
            desc_unchanged += 1
        label = str((r or {}).get("metadata_acceptance_label") or "").strip().lower()
        if label == "unchanged":
            accepted_rows += 1
        elif label == "edited":
            accepted_rows += 1
        title_edit = _safe_float((r or {}).get("title_edit_distance"), default=None)
        desc_edit = _safe_float((r or {}).get("description_edit_distance"), default=None)
        if title_edit is None:
            title_edit = 0.0 if (title and any(title == ai for ai in ai_titles)) else 0.25
        if desc_edit is None:
            desc_edit = 0.0 if (desc and ai_desc and desc == ai_desc) else 0.25
        title_edit_distances.append(max(0.0, min(1.0, title_edit)))
        desc_edit_distances.append(max(0.0, min(1.0, desc_edit)))
        mv = (r or {}).get("metadata_validation") if isinstance((r or {}).get("metadata_validation"), dict) else {}
        blockers = mv.get("blockers") or []
        blocker_count += len(blockers) if isinstance(blockers, list) else 0
        blocker_severity += _blocker_severity(blockers)
        if bool(mv.get("overall_ready")):
            ready_count += 1
        diversity_values.append(_title_diversity_score(insight))

    feedback_subset = [x for x in feedback_rows if str((x or {}).get("scene_id") or "") in scene_ids]
    n_feedback = len(feedback_subset)
    reject_rows = 0
    for row in feedback_subset:
        decision = str(((row.get("operator") or {}).get("decision")) or "").strip().lower()
        if decision == "reject":
            reject_rows += 1

    denom = max(1, scene_count)
    return {
        "scene_count": scene_count,
        "title_unchanged_rate_pct": round(title_unchanged / denom * 100.0, 2),
        "description_unchanged_rate_pct": round(desc_unchanged / denom * 100.0, 2),
        "metadata_acceptance_rate_pct": round(accepted_rows / denom * 100.0, 2),
        "median_title_edit_distance": round(_median_or_zero(title_edit_distances), 4),
        "median_description_edit_distance": round(_median_or_zero(desc_edit_distances), 4),
        "avg_metadata_blockers": round(blocker_count / denom, 3),
        "avg_metadata_blocker_severity": round(blocker_severity / denom, 3),
        "metadata_ready_rate_pct": round(ready_count / denom * 100.0, 2),
        "title_set_diversity": round(_median_or_zero(diversity_values), 4),
        "feedback_reject_rate_pct": round(reject_rows / max(1, n_feedback) * 100.0, 2),
    }


def _delta_metrics(candidate: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    keys = [
        "title_unchanged_rate_pct",
        "description_unchanged_rate_pct",
        "metadata_acceptance_rate_pct",
        "median_title_edit_distance",
        "median_description_edit_distance",
        "avg_metadata_blockers",
        "avg_metadata_blocker_severity",
        "metadata_ready_rate_pct",
        "title_set_diversity",
        "feedback_reject_rate_pct",
    ]
    for key in keys:
        out[f"{key}_delta"] = float(candidate.get(key, 0.0)) - float(baseline.get(key, 0.0))
    return out


def _safe_float(value: Any, default: Optional[float]) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _median_or_zero(values: List[float]) -> float:
    cleaned = [float(v) for v in values if v is not None]
    if not cleaned:
        return 0.0
    return float(median(cleaned))


def _blocker_severity(blockers: Any) -> float:
    if not isinstance(blockers, list):
        return 0.0
    total = 0.0
    for blocker in blockers:
        text = str(blocker or "").strip().lower()
        if not text:
            continue
        if any(k in text for k in ("too_short", "below_min", "missing", "requires", "must")):
            total += 2.0
        else:
            total += 1.0
    return total


def _title_diversity_score(insight: Dict[str, Any]) -> float:
    titles_raw = insight.get("ai_titles") if isinstance(insight.get("ai_titles"), list) else []
    titles = [str((x or {}).get("text") or "").strip() for x in titles_raw if isinstance(x, dict)]
    titles = [t for t in titles if t]
    if len(titles) < 2:
        return 0.0
    values: List[float] = []
    for i in range(len(titles)):
        for j in range(i + 1, len(titles)):
            values.append(_lexical_distance(titles[i], titles[j]))
    return _median_or_zero(values)


def _lexical_distance(a: str, b: str) -> float:
    ta = {w for w in a.lower().split() if w}
    tb = {w for w in b.lower().split() if w}
    union = ta | tb
    if not union:
        return 0.0
    overlap = len(ta & tb) / max(1, len(union))
    return 1.0 - overlap


def _consecutive_windows_passing(metrics: Dict[str, Any], *, provisional_pass: bool) -> int:
    windows = 1 if provisional_pass else 0
    recent_runs = metrics.get("recent_runs")
    if not isinstance(recent_runs, list):
        return windows
    for row in recent_runs:
        gates = row.get("gates") if isinstance(row, dict) else {}
        if not isinstance(gates, dict):
            break
        if bool(gates.get("pass")):
            windows += 1
            continue
        break
    return windows
