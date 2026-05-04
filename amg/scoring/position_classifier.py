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
    POSITION_LABELS,
)
from amg.scoring.ai_client import AIClient
from amg.utils.logging import get_logger

log = get_logger("scoring.position_classifier")

_VALID = set(POSITION_LABELS)
_RE_POSITION = re.compile(r"POSITION:\s*([A-Z_]+)", re.IGNORECASE)


def _score(entry: dict) -> float:
    scored = entry.get("scored_frame")
    return float(scored.score) if scored else 0.0


def _is_candidate_worthy(entry: dict) -> bool:
    scored = entry.get("scored_frame")
    if not scored or not scored.parse_succeeded:
        return False
    if scored.score < POSITION_CLASSIFIER_MIN_SCORE:
        return False
    t = (getattr(scored, "type_", None) or "").upper()
    return t in {"PENETRATION", "SEX_ACT", "NUDE", "UNKNOWN"}


def _build_prompt() -> str:
    labels = ", ".join(POSITION_LABELS)
    return (
        "Classify the primary explicit sexual position shown in this frame.\n"
        "If uncertain, choose OTHER.\n"
        f"Allowed labels: {labels}\n\n"
        "Respond exactly in this format:\n"
        "POSITION: <ONE_LABEL>\n"
        "END"
    )


def _parse_label(raw_text: str) -> str:
    if not raw_text:
        return "OTHER"
    m = _RE_POSITION.search(raw_text.upper())
    if not m:
        return "OTHER"
    label = m.group(1).strip().upper()
    if label not in _VALID:
        return "OTHER"
    return label


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

    def _one(entry: dict) -> tuple[dict, str, bool]:
        frame = entry.get("frame")
        if frame is None:
            return entry, "OTHER", False
        resp = client.score_frame(frame, prompt)
        if not resp.success:
            return entry, "OTHER", False
        return entry, _parse_label(resp.raw_text), True

    errors = 0
    classified = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_one, e) for e in workset]
        for fut in as_completed(futs):
            try:
                entry, label, ok = fut.result()
                entry["position_label"] = label
                if ok:
                    classified += 1
                else:
                    errors += 1
            except Exception:
                errors += 1

    log.info(
        "Position classification complete",
        considered=len(workset),
        classified=classified,
        errors=errors,
    )
    return {"considered": len(workset), "classified": classified, "errors": errors}

