"""
Finish hunter — search the last 20% of the video for money shots.

Adult VOD scenes typically have their climactic moments (facial, creampie,
squirt) in the last fifth of the video. Targeted search there often yields
top-scoring frames the tiered scan missed.

Strategy:
1. Sample frames at higher density in the last 20%
2. Apply CV gates
3. Deduplicate
4. Score top N candidates with AI
"""
import time
from pathlib import Path
from typing import List, Optional

from amg.config import (
    FINISH_HUNTER_ZONE_START_PCT,
    FINISH_HUNTER_TOP_N,
    SCORE_TIER_2_SUCCESS_FLOOR,
)
from amg.video.reader import VideoReader
from amg.video.frames import measure_sharpness, is_frame_too_dark
from amg.video.dedup import deduplicate_frames
from amg.scoring.orchestrator import score_frames_parallel
from amg.utils.logging import get_logger

log = get_logger("scanning.finish_hunter")


def run_finish_hunter(
    video_path: Path,
    duration_sec: float,
    calibration: dict,
    prompt: str,
    system_prompt: Optional[str] = None,
    deadline_sec: Optional[float] = None,
) -> dict:
    """
    Hunt for money-shot frames in the last 20% of the video.

    Returns:
        {
            'candidates': [scored frames],
            'aborted': bool,
            'abort_reason': str,
        }
    """
    start_sec = duration_sec * FINISH_HUNTER_ZONE_START_PCT
    sharpness_floor = calibration["tier_2_floor"]  # Use Tier 2 looseness for finish hunting

    log.info("Finish hunter: scanning last 20%",
             start_sec=start_sec, end_sec=duration_sec, sharp_floor=sharpness_floor)

    # Higher sampling density in finish zone (every 1 second)
    candidates = []
    interval = 1.0

    with VideoReader(video_path) as vr:
        for ts, frame in vr.iter_frames_sequential(start_sec, duration_sec, interval):
            if deadline_sec and time.time() > deadline_sec:
                log.warn("Finish hunter: deadline exceeded")
                return {
                    "candidates": [],
                    "aborted": True,
                    "abort_reason": "E_TIMEOUT_HARD",
                }

            if is_frame_too_dark(frame):
                continue
            sharp = measure_sharpness(frame)
            if sharp < sharpness_floor:
                continue

            candidates.append({
                "timestamp_sec": ts,
                "frame": frame,
                "sharpness": sharp,
                "tier": "finish_hunter",
            })

    log.info("Finish hunter: post-gate candidates", count=len(candidates))

    if not candidates:
        return {"candidates": [], "aborted": False}

    # Deduplicate
    deduped = deduplicate_frames(candidates)

    # Take top N by sharpness for AI scoring (cap to control AI call count)
    deduped.sort(key=lambda x: x["sharpness"], reverse=True)
    top = deduped[:FINISH_HUNTER_TOP_N]

    # Score in parallel
    scored = score_frames_parallel(top, prompt, system_prompt=system_prompt)
    log.info("Finish hunter: scored", count=len(scored))

    # Filter to passing
    passing = [
        f for f in scored
        if f.get("scored_frame")
        and f["scored_frame"].parse_succeeded
        and f["scored_frame"].score >= SCORE_TIER_2_SUCCESS_FLOOR
    ]

    return {
        "candidates": passing,
        "all_scored": scored,
        "aborted": False,
    }
