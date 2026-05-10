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
    POSITION_SEGMENT_COVERAGE_ENABLED,
    POSITION_SEGMENT_TARGET_PER_SEGMENT,
    POSITION_SEGMENT_MERGE_GAP_SEC,
    POSITION_SEGMENT_MIN_LABEL_CONF,
    POSITION_SEGMENT_MAX_TOTAL,
    normalize_position_label,
)


@dataclass(frozen=True)
class QuotaSpec:
    posterpose: int = QUOTA_POSTERPOSE_TARGET
    buildup: int = QUOTA_BUILDUP_TARGET
    finish: int = QUOTA_FINISH_TARGET
    positions_per_label: int = QUOTA_POSITION_PER_LABEL_TARGET
    positions_max_labels: int = QUOTA_POSITION_MAX_LABELS
    min_gap_sec: float = QUOTA_MIN_GAP_SEC
    position_segment_coverage: bool = POSITION_SEGMENT_COVERAGE_ENABLED
    position_segment_target: int = POSITION_SEGMENT_TARGET_PER_SEGMENT
    position_segment_merge_gap_sec: float = POSITION_SEGMENT_MERGE_GAP_SEC
    position_segment_min_conf: float = POSITION_SEGMENT_MIN_LABEL_CONF
    position_segment_max_total: int = POSITION_SEGMENT_MAX_TOTAL


@dataclass
class PositionSegment:
    label: str
    index: int
    start_sec: float
    end_sec: float
    candidates: List[dict]

    @property
    def segment_id(self) -> str:
        return f"{self.label}_{self.index:02d}"


def _score_of(c: dict) -> float:
    scored = c.get("scored_frame")
    return float(scored.score) if scored else 0.0


def _selection_rank(c: dict) -> tuple[float, float, float]:
    """Primary rank is the existing score; later fields only break ties."""
    motion = 0.0
    try:
        motion = float(c.get("motion") or 0.0)
    except (TypeError, ValueError):
        motion = 0.0
    return (_score_of(c), _position_conf_of(c), motion)


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
    if t in {"PENETRATION", "SEX_ACT"}:
        label = _position_label_of(c)
        if label not in {"", "OTHER"}:
            return "positions"
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
    label = c.get("position_label")
    if not label:
        scored = c.get("scored_frame")
        label = getattr(scored, "position_label", "OTHER") if scored else "OTHER"
    return normalize_position_label(label)


def _position_conf_of(c: dict) -> float:
    conf = c.get("position_label_confidence")
    if conf is None:
        scored = c.get("scored_frame")
        conf = getattr(scored, "position_confidence", 0.0) if scored else 0.0
    try:
        return max(0.0, min(1.0, float(conf or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _is_far_enough(ts: float, chosen_ts: List[float], min_gap_sec: float) -> bool:
    return all(abs(ts - prev) >= min_gap_sec for prev in chosen_ts)


def _build_position_segments(candidates: List[dict], quota: QuotaSpec) -> List[PositionSegment]:
    eligible: List[dict] = []
    for c in sorted(candidates, key=lambda item: (_ts_of(item) is None, _ts_of(item) or 0.0)):
        if _bucket_of(c) != "positions":
            continue
        ts = _ts_of(c)
        if ts is None:
            continue
        label = _position_label_of(c)
        if label in {"", "OTHER"}:
            continue
        if _position_conf_of(c) < quota.position_segment_min_conf:
            continue
        eligible.append(c)

    segments: List[PositionSegment] = []
    per_label_seen: Dict[str, int] = {}
    current: Optional[PositionSegment] = None
    for c in eligible:
        ts = _ts_of(c)
        if ts is None:
            continue
        label = _position_label_of(c)
        same_run = (
            current is not None
            and current.label == label
            and ts - current.end_sec <= quota.position_segment_merge_gap_sec
        )
        if same_run and current is not None:
            current.candidates.append(c)
            current.end_sec = ts
            continue

        per_label_seen[label] = per_label_seen.get(label, 0) + 1
        current = PositionSegment(
            label=label,
            index=per_label_seen[label],
            start_sec=ts,
            end_sec=ts,
            candidates=[c],
        )
        segments.append(current)
    return segments


def _mark_segment(c: dict, segment: PositionSegment) -> None:
    c["position_segment_id"] = segment.segment_id
    c["position_segment_label"] = segment.label
    c["position_segment_start_sec"] = round(segment.start_sec, 3)
    c["position_segment_end_sec"] = round(segment.end_sec, 3)
    c["analysis_section_tag"] = segment.label


def _annotate_analysis_hint(c: dict) -> None:
    label = _position_label_of(c)
    if label and label != "OTHER":
        c.setdefault("analysis_section_tag", label)
        return
    scored = c.get("scored_frame")
    type_ = (getattr(scored, "type_", "") if scored else "").strip().upper()
    if type_:
        c.setdefault("analysis_section_tag", type_)


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
    sorted_candidates = sorted(candidates, key=_selection_rank, reverse=True)
    position_segments = _build_position_segments(sorted_candidates, quota) if quota.position_segment_coverage else []
    effective_max_total = max_total
    if position_segments:
        segment_need = len(position_segments) * max(1, quota.position_segment_target)
        effective_max_total = max(max_total, segment_need, min_total)
        effective_max_total = min(max(1, quota.position_segment_max_total), effective_max_total)

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
    selected_ids = set()
    per_bucket: Dict[str, int] = {k: 0 for k in ["posterpose", "positions", "buildup", "finish", "other"]}
    per_position: Dict[str, int] = {label: 0 for label in selected_labels}
    per_segment: Dict[str, int] = {seg.segment_id: 0 for seg in position_segments}
    segment_pick_count = 0
    bucket_pick_count = 0
    topoff_count = 0

    # Pass 0: temporal position coverage. Every detected position run gets
    # first claim on up to three strong shots before generic top-off ranking.
    for segment in position_segments:
        if len(selected) >= effective_max_total:
            break
        segment_candidates = sorted(segment.candidates, key=_selection_rank, reverse=True)
        for min_gap in (quota.min_gap_sec, min(8.0, quota.min_gap_sec)):
            for c in segment_candidates:
                if len(selected) >= effective_max_total:
                    break
                if per_segment[segment.segment_id] >= quota.position_segment_target:
                    break
                if id(c) in selected_ids:
                    continue
                ts = _ts_of(c)
                if ts is None:
                    continue
                if not _is_far_enough(ts, selected_ts, min_gap):
                    continue
                _mark_segment(c, segment)
                _annotate_analysis_hint(c)
                selected.append(c)
                selected_ids.add(id(c))
                selected_ts.append(ts)
                per_bucket["positions"] += 1
                per_position[segment.label] = per_position.get(segment.label, 0) + 1
                per_segment[segment.segment_id] += 1
                segment_pick_count += 1
            if per_segment[segment.segment_id] >= quota.position_segment_target:
                break

    # Pass 1: fill each target bucket with spacing to avoid near-duplicates.
    for c in sorted_candidates:
        if len(selected) >= effective_max_total:
            break
        if id(c) in selected_ids:
            continue
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
        _annotate_analysis_hint(c)
        selected.append(c)
        selected_ids.add(id(c))
        selected_ts.append(ts)
        per_bucket[bucket] += 1
        bucket_pick_count += 1
        if bucket == "positions":
            label = _position_label_of(c)
            per_position[label] = per_position.get(label, 0) + 1

    # Pass 2: if we’re below min_total, top off by score (still enforcing spacing).
    if len(selected) < min_total:
        for c in sorted_candidates:
            if len(selected) >= min_total or len(selected) >= effective_max_total:
                break
            if id(c) in selected_ids:
                continue
            ts = _ts_of(c)
            if ts is None:
                continue
            if not _is_far_enough(ts, selected_ts, quota.min_gap_sec):
                continue
            _annotate_analysis_hint(c)
            selected.append(c)
            selected_ids.add(id(c))
            selected_ts.append(ts)
            per_bucket[_bucket_of(c)] = per_bucket.get(_bucket_of(c), 0) + 1
            topoff_count += 1

    stats = {
        "max_total": max_total,
        "effective_max_total": effective_max_total,
        "min_total": min_total,
        "selected": len(selected),
        "segment_pick_count": segment_pick_count,
        "bucket_pick_count": bucket_pick_count,
        "topoff_count": topoff_count,
        "position_segment_coverage": bool(position_segments),
        "position_segments": len(position_segments),
        **{f"bucket_{k}": v for k, v in per_bucket.items()},
        "position_labels": ",".join(selected_labels),
        **{f"position_{k}": v for k, v in per_position.items()},
        **{f"position_segment_{k}": v for k, v in per_segment.items()},
    }
    return selected, stats

