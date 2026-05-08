"""
Adaptive cluster expansion.

When we find a high-scoring frame, mine the surrounding seconds since
excellence clusters in time.

Window scaling (score on 0–100 scale, see CLUSTER_WINDOWS in config):
    50–79: narrow window
    80–89: medium window
    90–99: wide window
    100:   widest window
"""
import time
from pathlib import Path
from typing import List, Optional

from amg.config import get_cluster_window, CLUSTER_HUNTER_TOP_N, SCORE_TIER_3_SUCCESS_FLOOR
from amg.video.reader import VideoReader
from amg.video.frames import measure_sharpness, is_frame_too_dark
from amg.video.dedup import deduplicate_frames
from amg.scoring.orchestrator import get_last_scoring_stats, score_frames_parallel
from amg.utils.logging import get_logger

log = get_logger("scanning.cluster")


def expand_clusters(
    video_path: Path,
    duration_sec: float,
    seed_candidates: List[dict],
    calibration: dict,
    prompt: str,
    system_prompt: Optional[str] = None,
    deadline_sec: Optional[float] = None,
    already_seen_timestamps: Optional[set] = None,
) -> dict:
    """
    For each high-scoring seed, sample neighboring frames and score them.

    Args:
        seed_candidates: Already-scored frames. We expand around the highest scorers.
        already_seen_timestamps: Set of timestamps to skip (from prior scans)

    Returns:
        {
            'cluster_candidates': [newly scored frames],
            'expansions_count': int,
            'aborted': bool,
        }
    """
    if not seed_candidates:
        return {"cluster_candidates": [], "expansions_count": 0, "aborted": False}

    seen = already_seen_timestamps or set()
    sharpness_floor = calibration["tier_3_floor"]  # Lenient for cluster (we want neighbors)

    # Sort seeds by score (mine highest first)
    sorted_seeds = sorted(
        seed_candidates,
        key=lambda x: x.get("scored_frame").score if x.get("scored_frame") else 0,
        reverse=True,
    )

    # v11.1.2: seed consolidation. If two scoring seeds are close enough that
    # their expansion windows would overlap, only expand the higher-scoring one.
    # The high-scorer's cluster already mines that timeline range — expanding
    # around the lower-scoring seed produces redundant samples and Mario ends
    # up with 13 near-duplicate covers from the same 70-second hot zone (which
    # is exactly what scene 8 produced in v11.1.1: 8m03/07/08/27/36/39/41/42/...).
    # Higher-scorer wins because it's empirically the better representative.
    kept_seeds = []
    kept_ranges = []  # (lo_ts, hi_ts) of each kept seed's expansion window
    dropped_for_overlap = 0
    for seed in sorted_seeds:
        scored = seed.get("scored_frame")
        if not scored or scored.score < SCORE_TIER_3_SUCCESS_FLOOR:
            continue
        seed_ts = seed["timestamp_sec"]
        window_sec, _ = get_cluster_window(scored.score)
        if window_sec == 0:
            continue
        seed_lo = seed_ts - window_sec
        seed_hi = seed_ts + window_sec
        if any(seed_lo < hi and seed_hi > lo for lo, hi in kept_ranges):
            dropped_for_overlap += 1
            continue
        kept_seeds.append(seed)
        kept_ranges.append((seed_lo, seed_hi))

    if dropped_for_overlap:
        log.info("Cluster expansion: seeds consolidated",
                 kept=len(kept_seeds), dropped_overlap=dropped_for_overlap)

    # Build list of candidate timestamps to sample (only from kept seeds)
    sample_points = []  # List of (timestamp, source_seed_score)
    expansions = 0

    for seed in kept_seeds:
        scored = seed.get("scored_frame")
        seed_ts = seed["timestamp_sec"]
        window_sec, interval_sec = get_cluster_window(scored.score)

        expansions += 1

        # Sample points in window around seed
        offsets = []
        steps = int(window_sec / interval_sec) if interval_sec > 0 else 0
        for i in range(1, steps + 1):
            offsets.append(-i * interval_sec)
            offsets.append(+i * interval_sec)

        for offset in offsets:
            ts = seed_ts + offset
            if ts < 0 or ts > duration_sec:
                continue
            ts_key = round(ts, 1)
            if ts_key in seen:
                continue
            sample_points.append((ts, scored.score))
            seen.add(ts_key)

    log.info("Cluster expansion plan",
             seeds=len(kept_seeds),
             expansions=expansions,
             sample_points=len(sample_points))

    if not sample_points:
        return {"cluster_candidates": [], "expansions_count": 0, "aborted": False}

    # Extract frames at sample points
    candidates = []
    aborted = False
    abort_reason = None
    chunk_size = 16

    with VideoReader(video_path) as vr:
        for chunk_start in range(0, len(sample_points), chunk_size):
            if deadline_sec and time.time() > deadline_sec:
                log.warn("Cluster expansion: deadline exceeded")
                aborted = True
                abort_reason = "E_TIMEOUT_HARD"
                break
            chunk = sample_points[chunk_start:chunk_start + chunk_size]
            frames = vr.get_frames_at([t for t, _ in chunk])
            for (ts, source_score), frame in zip(chunk, frames):
                if deadline_sec and time.time() > deadline_sec:
                    log.warn("Cluster expansion: deadline exceeded")
                    aborted = True
                    abort_reason = "E_TIMEOUT_HARD"
                    break
                if frame is None or is_frame_too_dark(frame):
                    continue
                sharp = measure_sharpness(frame)
                if sharp < sharpness_floor:
                    continue
                candidates.append({
                    "timestamp_sec": ts,
                    "frame": frame,
                    "sharpness": sharp,
                    "tier": "cluster",
                    "_seed_score": source_score,
                })
            if aborted:
                break

    log.info("Cluster expansion: post-gate", count=len(candidates))

    if not candidates:
        return {
            "cluster_candidates": [],
            "expansions_count": expansions,
            "aborted": aborted,
            "abort_reason": abort_reason,
        }

    # Dedup (cluster samples around the same seed will look similar)
    deduped = deduplicate_frames(candidates)

    # v11.1.4: cap AI scoring count. Mirrors BUILDUP_HUNTER_TOP_N pattern.
    # Without this, fast decode lets cluster balloon — scene 10 with v11.1.3
    # PyAV produced 145 post-gate candidates, which would have been ~15 min
    # of AI scoring. Sort by sharpness desc, take top N.
    deduped.sort(key=lambda x: x["sharpness"], reverse=True)
    capped = deduped[:CLUSTER_HUNTER_TOP_N]
    if len(deduped) > CLUSTER_HUNTER_TOP_N:
        log.info("Cluster expansion: post-cap candidates",
                 kept=len(capped), dropped=len(deduped) - len(capped),
                 cap=CLUSTER_HUNTER_TOP_N)

    # Score in parallel
    scored = score_frames_parallel(
        capped,
        prompt,
        system_prompt=system_prompt,
        deadline_sec=deadline_sec,
    )
    scoring_stats = _scoring_stat_payload()
    log.info("Cluster expansion: scored", count=len(scored))

    # Return all scored (caller will filter)
    return {
        "cluster_candidates": scored,
        "expansions_count": expansions,
        "aborted": aborted,
        "abort_reason": abort_reason,
        **scoring_stats,
    }


def _scoring_stat_payload() -> dict:
    stats = get_last_scoring_stats()
    return {
        "ai_submitted_count": int(stats.get("submitted_count", 0) or 0),
        "ai_completed_count": int(stats.get("completed_count", 0) or 0),
        "ai_skipped_count": int(stats.get("skipped_count", 0) or 0),
        "ai_batch_wall_sec": float(stats.get("batch_wall_sec", 0.0) or 0.0),
    }
