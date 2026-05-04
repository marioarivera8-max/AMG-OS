from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from amg.config import (
    QUOTA_POSTERPOSE_TARGET,
    QUOTA_BUILDUP_TARGET,
    QUOTA_FINISH_TARGET,
    QUOTA_POSITION_PER_LABEL_TARGET,
    QUOTA_POSITION_MAX_LABELS,
    QUOTA_MIN_GAP_SEC,
    POSITION_CLASSIFIER_MIN_PEN_CONF,
)


@dataclass(frozen=True)
class QuotaSpec:
    posterpose: int = QUOTA_POSTERPOSE_TARGET
    buildup: int = QUOTA_BUILDUP_TARGET
    finish: int = QUOTA_FINISH_TARGET
    positions_per_label: int = QUOTA_POSITION_PER_LABEL_TARGET
    positions_max_labels: int = QUOTA_POSITION_MAX_LABELS
    min_gap_sec: float = QUOTA_MIN_GAP_SEC


def _score_of(c: dict) -> float:
    scored = c.get("scored_frame")
    return float(scored.score) if scored else 0.0


def _ts_of(c: dict) -> Optional[float]:
    ts = c.get("timestamp_sec")
    return float(ts) if ts is not None else None


def _bucket_of(c: dict) -> str:
    """
    Map a scored candidate into a quota bucket using existing metadata.

    This is v0: it uses `tier` and the existing `ScoredFrame.type_` field.
    Later we can add a real position taxonomy classifier and split PENETRATION
    into missionary/cowgirl/doggy/etc.
    """
    tier = (c.get("tier") or "").lower()
    if tier == "finish_hunter":
        return "finish"
    if tier == "buildup_hunter":
        return "buildup"

    scored = c.get("scored_frame")
    t = (getattr(scored, "type_", None) or "UNKNOWN").upper() if scored else "UNKNOWN"
    if t == "FINISH":
        return "finish"
    if t == "BUILDUP":
        return "buildup"
    if t == "PENETRATION":
        pen_visible = bool(getattr(scored, "penetration_visible", False))
        pen_conf = float(getattr(scored, "penetration_confidence", 0.0) or 0.0)
        if not pen_visible or pen_conf < POSITION_CLASSIFIER_MIN_PEN_CONF:
            return "other"
        return "positions"
    if t == "COMPOSITION":
        return "posterpose"

    # Fallthrough: treat nudity/sex_act as potential poster shots
    if t in {"NUDE", "SEX_ACT"}:
        return "posterpose"

    return "other"


def _position_label_of(c: dict) -> str:
    label = (c.get("position_label") or "").upper().strip()
    return label if label else "OTHER"


def _is_far_enough(ts: float, chosen_ts: List[float], min_gap_sec: float) -> bool:
    return all(abs(ts - prev) >= min_gap_sec for prev in chosen_ts)


def quota_progress(
    candidates: List[dict],
    quota: Optional[QuotaSpec] = None,
) -> Dict[str, int]:
    """Count how many candidates we currently have per bucket."""
    quota = quota or QuotaSpec()
    counts = {
        "posterpose": 0,
        "positions": 0,
        "buildup": 0,
        "finish": 0,
    }
    for c in candidates:
        b = _bucket_of(c)
        if b in counts:
            counts[b] += 1
    return counts


def quota_satisfied(
    candidates: List[dict],
    quota: Optional[QuotaSpec] = None,
) -> bool:
    """Return True if all quota buckets are satisfied (ignores overall cap)."""
    quota = quota or QuotaSpec()
    counts = quota_progress(candidates, quota)
    # For early-stop while scanning, use a coarse positions target based on
    # per-label target * max labels. Final selection enforces per-label limits.
    positions_total_target = quota.positions_per_label * quota.positions_max_labels
    return (
        counts.get("posterpose", 0) >= quota.posterpose
        and counts.get("positions", 0) >= positions_total_target
        and counts.get("buildup", 0) >= quota.buildup
        and counts.get("finish", 0) >= quota.finish
    )


def select_quota_fill(
    candidates: List[dict],
    *,
    max_total: int,
    min_total: int,
    quota: Optional[QuotaSpec] = None,
) -> Tuple[List[dict], Dict[str, int]]:
    """
    Select a bounded set of covers using a simple quota-fill strategy.

    Returns (selected, stats).
    """
    quota = quota or QuotaSpec()
    if max_total <= 0:
        return [], {"max_total": max_total, "min_total": min_total}

    # Sort once by score, highest first.
    sorted_candidates = sorted(candidates, key=_score_of, reverse=True)

    base_targets = {
        "posterpose": quota.posterpose,
        "buildup": quota.buildup,
        "finish": quota.finish,
    }

    # Build per-position-label targets from observed classified labels.
    # Keep only the strongest labels so output does not balloon.
    label_rank: Dict[str, float] = {}
    label_count: Dict[str, int] = {}
    for c in sorted_candidates:
        if _bucket_of(c) != "positions":
            continue
        label = _position_label_of(c)
        if label in {"", "OTHER"}:
            continue
        label_count[label] = label_count.get(label, 0) + 1
        label_rank[label] = max(label_rank.get(label, 0.0), _score_of(c))
    selected_labels = sorted(
        label_count.keys(),
        key=lambda k: (label_count[k], label_rank.get(k, 0.0)),
        reverse=True,
    )[: quota.positions_max_labels]
    position_targets = {label: quota.positions_per_label for label in selected_labels}

    selected: List[dict] = []
    selected_ts: List[float] = []
    per_bucket: Dict[str, int] = {k: 0 for k in ["posterpose", "positions", "buildup", "finish", "other"]}
    per_position: Dict[str, int] = {label: 0 for label in selected_labels}

    # Pass 1: fill each target bucket with spacing to avoid near-duplicates.
    for c in sorted_candidates:
        if len(selected) >= max_total:
            break
        ts = _ts_of(c)
        if ts is None:
            continue
        bucket = _bucket_of(c)
        if bucket in base_targets and per_bucket[bucket] >= base_targets[bucket]:
            continue
        if bucket == "positions":
            label = _position_label_of(c)
            if label not in position_targets:
                continue
            if per_position.get(label, 0) >= position_targets[label]:
                continue
        if not _is_far_enough(ts, selected_ts, quota.min_gap_sec):
            continue
        selected.append(c)
        selected_ts.append(ts)
        per_bucket[bucket] += 1
        if bucket == "positions":
            label = _position_label_of(c)
            per_position[label] = per_position.get(label, 0) + 1

    # Pass 2: if we’re below min_total, top off by score (still enforcing spacing).
    if len(selected) < min_total:
        for c in sorted_candidates:
            if len(selected) >= min_total or len(selected) >= max_total:
                break
            ts = _ts_of(c)
            if ts is None:
                continue
            if not _is_far_enough(ts, selected_ts, quota.min_gap_sec):
                continue
            selected.append(c)
            selected_ts.append(ts)
            per_bucket[_bucket_of(c)] = per_bucket.get(_bucket_of(c), 0) + 1

    stats = {
        "max_total": max_total,
        "min_total": min_total,
        "selected": len(selected),
        **{f"bucket_{k}": v for k, v in per_bucket.items()},
        "position_labels": ",".join(selected_labels),
        **{f"position_{k}": v for k, v in per_position.items()},
    }
    return selected, stats

