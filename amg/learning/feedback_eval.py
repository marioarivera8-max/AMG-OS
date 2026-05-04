"""
Evaluate operator feedback against model classifications.
"""
from __future__ import annotations

import json
from pathlib import Path
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


def _rows() -> List[dict]:
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
    return out


def evaluate_feedback(scene_id: Optional[str] = None, studio: Optional[str] = None) -> Dict[str, object]:
    rows = _rows()
    if scene_id:
        rows = [r for r in rows if r.get("scene_id") == scene_id]

    if studio:
        filtered = []
        for r in rows:
            d = _load_decision_log(r.get("scene_id", ""))
            if d and (d.get("input", {}).get("studio") or "").lower() == studio.lower():
                filtered.append(r)
        rows = filtered

    total = len(rows)
    if total == 0:
        return {"total_rows": 0}

    pen_labeled = 0
    pen_matches = 0
    pos_labeled = 0
    pos_matches = 0

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

    return {
        "total_rows": total,
        "scene_count": len(by_scene),
        "penetration_labeled_rows": pen_labeled,
        "penetration_match_rate": round((pen_matches / pen_labeled) * 100, 1) if pen_labeled else None,
        "position_labeled_rows": pos_labeled,
        "position_match_rate": round((pos_matches / pos_labeled) * 100, 1) if pos_labeled else None,
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
    lines.append("")
    lines.append("Rows by scene:")
    for scene_id, cnt in sorted((metrics.get("rows_by_scene") or {}).items(), key=lambda x: x[1], reverse=True):
        lines.append(f"  - {scene_id}: {cnt}")
    lines.append("=" * 64)
    return "\n".join(lines)

