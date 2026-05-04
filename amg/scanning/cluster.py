"""
Adaptive cluster expansion.

When we find a high-scoring frame, mine the surrounding seconds since
excellence clusters in time.

Window scaling (from spec § XVII.A):
    5.0 - 7.9: ±2s at 1s intervals  (4 samples)
    8.0 - 8.9: ±5s at 1s intervals  (10 samples)
    9.0 - 9.9: ±15s at 3s intervals (10 samples)
    10.0:      ±30s at 5s intervals (12 samples)
"""
import time
from pathlib import Path
from typing import List, Optional

from amg.config import get_cluster_window
from amg.video.reader import VideoReader
from amg.video.frames import measure_sharpness, is_frame_too_dark
from amg.video.dedup import deduplicate_frames
from amg.scoring.orchestrator import score_frames_parallel
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

    # Build list of candidate timestamps to sample
    sample_points = []  # List of (timestamp, source_seed_score)
    expansions = 0

    for seed in sorted_seeds:
        scored = seed.get("scored_frame")
        if not scored or scored.score < 5.0:
            continue

        seed_ts = seed["timestamp_sec"]
        window_sec, interval_sec = get_cluster_window(scored.score)
        if window_sec == 0:
            continue

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
             seeds=len([s for s in sorted_seeds if s.get("scored_frame") and s["scored_frame"].score >= 5.0]),
             expansions=expansions,
             sample_points=len(sample_points))

    if not sample_points:
        return {"cluster_candidates": [], "expansions_count": 0, "aborted": False}

    # Extract frames at sample points
    candidates = []
    timestamps = [t for t, _ in sample_points]

    with VideoReader(video_path) as vr:
        frames = vr.get_frames_at(timestamps)
        for (ts, source_score), frame in zip(sample_points, frames):
            if deadline_sec and time.time() > deadline_sec:
                log.warn("Cluster expansion: deadline exceeded")
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

    log.info("Cluster expansion: post-gate", count=len(candidates))

    if not candidates:
        return {"cluster_candidates": [], "expansions_count": expansions, "aborted": False}

    # Dedup (cluster samples around the same seed will look similar)
    deduped = deduplicate_frames(candidates)

    # Score in parallel
    scored = score_frames_parallel(deduped, prompt, system_prompt=system_prompt)
    log.info("Cluster expansion: scored", count=len(scored))

    # Return all scored (caller will filter)
    return {
        "cluster_candidates": scored,
        "expansions_count": expansions,
        "aborted": False,
    }
