"""
Evaluate operator feedback against model classifications.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from amg.config import OPERATOR_FEEDBACK_PATH, DECISION_LOGS_DIR


def _load_decision_log(scene_id: str) -> Optional[dict]:
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in scene_id)[:120]
    p = DECISION_LOGS_DIR / f"{safe}.json"
    if not p.exists():
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def _parse_timestamp(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def load_feedback_rows(
    *,
    scene_id: Optional[str] = None,
    studio: Optional[str] = None,
    since_days: Optional[int] = None,
) -> List[dict]:
    if not OPERATOR_FEEDBACK_PATH.exists():
        return []
    out = []
    with open(OPERATOR_FEEDBACK_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue

    if scene_id:
        out = [r for r in out if r.get("scene_id") == scene_id]

    if studio:
        filtered = []
        log_cache: Dict[str, Optional[dict]] = {}
        for r in out:
            sid = str(r.get("scene_id", "") or "")
            if sid not in log_cache:
                log_cache[sid] = _load_decision_log(sid)
            d = log_cache[sid]
            if d and (d.get("input", {}).get("studio") or "").lower() == studio.lower():
                filtered.append(r)
        out = filtered

    if since_days is not None and since_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
        filtered = []
        for r in out:
            parsed = _parse_timestamp(str(r.get("timestamp", "") or ""))
            if parsed and parsed >= cutoff:
                filtered.append(r)
        out = filtered

    return out


def evaluate_feedback(scene_id: Optional[str] = None, studio: Optional[str] = None) -> Dict[str, object]:
    rows = load_feedback_rows(scene_id=scene_id, studio=studio)

    total = len(rows)
    if total == 0:
        return {"total_rows": 0}

    pen_labeled = 0
    pen_matches = 0
    pos_labeled = 0
    pos_matches = 0
    score_labeled = 0
    score_abs_err_sum = 0.0
    score_within_5 = 0

    by_scene: Dict[str, int] = {}
    for r in rows:
        sid = r.get("scene_id", "unknown")
        by_scene[sid] = by_scene.get(sid, 0) + 1

        model = r.get("model", {})
        op = r.get("operator", {})

        op_pen = op.get("penetration_visible")
        if op_pen in ("yes", "no"):
            pen_labeled += 1
            model_pen = bool(model.get("penetration_visible", False))
            if (op_pen == "yes" and model_pen) or (op_pen == "no" and not model_pen):
                pen_matches += 1

        op_pos = (op.get("position_label") or "").upper()
        if op_pos:
            pos_labeled += 1
            model_pos = (model.get("position_label") or "").upper()
            if op_pos == model_pos:
                pos_matches += 1

        op_score = op.get("score_100")
        model_score = model.get("score_100")
        if op_score is not None and model_score is not None:
            try:
                op_v = float(op_score)
                m_v = float(model_score)
            except (TypeError, ValueError):
                op_v = None
                m_v = None
            if op_v is not None and m_v is not None:
                score_labeled += 1
                err = abs(op_v - m_v)
                score_abs_err_sum += err
                if err <= 5.0:
                    score_within_5 += 1

    return {
        "total_rows": total,
        "scene_count": len(by_scene),
        "penetration_labeled_rows": pen_labeled,
        "penetration_match_rate": round((pen_matches / pen_labeled) * 100, 1) if pen_labeled else None,
        "position_labeled_rows": pos_labeled,
        "position_match_rate": round((pos_matches / pos_labeled) * 100, 1) if pos_labeled else None,
        "score_labeled_rows": score_labeled,
        "score_mae": round(score_abs_err_sum / score_labeled, 2) if score_labeled else None,
        "score_within_5_rate": round((score_within_5 / score_labeled) * 100, 1) if score_labeled else None,
        "rows_by_scene": by_scene,
    }


def format_feedback_report(metrics: Dict[str, object]) -> str:
    if metrics.get("total_rows", 0) == 0:
        return "No operator feedback rows found."

    lines = []
    lines.append("Feedback Evaluation")
    lines.append("=" * 64)
    lines.append(f"Rows: {metrics.get('total_rows')}")
    lines.append(f"Scenes: {metrics.get('scene_count')}")
    lines.append(
        f"Penetration match: {metrics.get('penetration_match_rate')}% "
        f"(labeled={metrics.get('penetration_labeled_rows')})"
    )
    lines.append(
        f"Position match: {metrics.get('position_match_rate')}% "
        f"(labeled={metrics.get('position_labeled_rows')})"
    )
    lines.append(
        f"Score MAE: {metrics.get('score_mae')} "
        f"(labeled={metrics.get('score_labeled_rows')}, within±5={metrics.get('score_within_5_rate')}%)"
    )
    lines.append("")
    lines.append("Rows by scene:")
    for scene_id, cnt in sorted((metrics.get("rows_by_scene") or {}).items(), key=lambda x: x[1], reverse=True):
        lines.append(f"  - {scene_id}: {cnt}")
    lines.append("=" * 64)
    return "\n".join(lines)

