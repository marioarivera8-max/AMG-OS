"""
Position classifier for quota-fill selection.

Classifies a bounded subset of high-score candidates into common position labels
so quota-fill can target "3 of each position" without scoring every frame again.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from amg.config import (
    AI_PARALLEL_WORKERS,
    POSITION_CLASSIFIER_MAX_CANDIDATES,
    POSITION_CLASSIFIER_MIN_SCORE,
    POSITION_CLASSIFIER_MIN_PEN_CONF,
    POSITION_CLASSIFIER_MIN_LABEL_CONF,
    POSITION_CLASSIFIER_CONTEXT_WINDOW_SEC,
    POSITION_LABELS,
)
from amg.scoring.ai_client import AIClient
from amg.utils.logging import get_logger

log = get_logger("scoring.position_classifier")

_VALID = set(POSITION_LABELS)
_RE_POSITION = re.compile(r"POSITION:\s*([A-Z_]+)", re.IGNORECASE)
_RE_POS_CONF = re.compile(r"POSITION_CONFIDENCE:\s*(-?\d+\.?\d*)", re.IGNORECASE)


def _score(entry: dict) -> float:
    scored = entry.get("scored_frame")
    return float(scored.score) if scored else 0.0


def _is_candidate_worthy(entry: dict) -> bool:
    scored = entry.get("scored_frame")
    if not scored or not scored.parse_succeeded:
        return False
    if scored.score < POSITION_CLASSIFIER_MIN_SCORE:
        return False
    # Hard gate: we only classify position for penetration-positive frames.
    if not bool(getattr(scored, "penetration_visible", False)):
        return False
    if float(getattr(scored, "penetration_confidence", 0.0) or 0.0) < POSITION_CLASSIFIER_MIN_PEN_CONF:
        return False
    return True


def _build_prompt() -> str:
    labels = ", ".join(POSITION_LABELS)
    return (
        "Classify the primary explicit sexual position shown in this frame.\n"
        "This frame is already penetration-positive, but position can still be uncertain.\n"
        "If uncertain, choose OTHER.\n"
        f"Allowed labels: {labels}\n\n"
        "Respond exactly in this format:\n"
        "POSITION: <ONE_LABEL>\n"
        "POSITION_CONFIDENCE: <0.00-1.00>\n"
        "END"
    )


def _parse_label(raw_text: str) -> tuple[str, float]:
    if not raw_text:
        return "OTHER", 0.0
    m = _RE_POSITION.search(raw_text.upper())
    if not m:
        return "OTHER", 0.0
    label = m.group(1).strip().upper()
    if label not in _VALID:
        label = "OTHER"
    conf = 0.0
    c = _RE_POS_CONF.search(raw_text)
    if c:
        try:
            conf = max(0.0, min(1.0, float(c.group(1))))
        except ValueError:
            conf = 0.0
    return label, conf


def _supports_by_context(entry: dict, classified: List[dict]) -> bool:
    """Require nearby temporal support for fragile labels."""
    ts = entry.get("timestamp_sec")
    label = entry.get("position_label", "OTHER")
    conf = float(entry.get("position_label_confidence", 0.0) or 0.0)
    if ts is None or label in {"OTHER", ""}:
        return True
    if conf >= 0.85:
        return True
    for other in classified:
        if other is entry:
            continue
        if other.get("position_label") != label:
            continue
        ots = other.get("timestamp_sec")
        if ots is None:
            continue
        oconf = float(other.get("position_label_confidence", 0.0) or 0.0)
        if oconf < POSITION_CLASSIFIER_MIN_LABEL_CONF:
            continue
        if abs(float(ots) - float(ts)) <= POSITION_CLASSIFIER_CONTEXT_WINDOW_SEC:
            return True
    return False


def classify_candidate_positions(
    candidates: List[dict],
    *,
    ai_client: Optional[AIClient] = None,
    max_candidates: int = POSITION_CLASSIFIER_MAX_CANDIDATES,
    max_workers: int = AI_PARALLEL_WORKERS,
) -> Dict[str, int]:
    """
    Attach `position_label` to a bounded top-score subset of candidates.

    Returns stats dict.
    """
    if not candidates:
        return {"considered": 0, "classified": 0, "errors": 0}

    eligible = [c for c in candidates if _is_candidate_worthy(c)]
    eligible.sort(key=_score, reverse=True)
    workset = eligible[:max_candidates]

    if not workset:
        return {"considered": 0, "classified": 0, "errors": 0}

    client = ai_client or AIClient()
    prompt = _build_prompt()

    def _one(entry: dict) -> tuple[dict, str, float, bool]:
        frame = entry.get("frame")
        if frame is None:
            return entry, "OTHER", 0.0, False
        resp = client.score_frame(frame, prompt)
        if not resp.success:
            return entry, "OTHER", 0.0, False
        label, conf = _parse_label(resp.raw_text)
        return entry, label, conf, True

    errors = 0
    classified = 0
    classified_entries: List[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_one, e) for e in workset]
        for fut in as_completed(futs):
            try:
                entry, label, label_conf, ok = fut.result()
                # Conservative fallback: low confidence is OTHER.
                if label_conf < POSITION_CLASSIFIER_MIN_LABEL_CONF:
                    label = "OTHER"
                entry["position_label"] = label
                entry["position_label_confidence"] = round(label_conf, 3)
                classified_entries.append(entry)
                if ok:
                    classified += 1
                else:
                    errors += 1
            except Exception:
                errors += 1

    # Temporal consistency check: weak singleton labels become OTHER.
    downgraded_by_context = 0
    for entry in classified_entries:
        if entry.get("position_label") in {"OTHER", ""}:
            continue
        if not _supports_by_context(entry, classified_entries):
            entry["position_label"] = "OTHER"
            downgraded_by_context += 1

    log.info(
        "Position classification complete",
        considered=len(workset),
        classified=classified,
        errors=errors,
        downgraded_by_context=downgraded_by_context,
    )
    return {
        "considered": len(workset),
        "classified": classified,
        "errors": errors,
        "downgraded_by_context": downgraded_by_context,
    }

