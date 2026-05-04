"""
Buildup hunter — search 25-50% of video for buildup/anticipation moments.

Adult VOD viewers often want covers showing the tease/setup (clothing being
removed, kissing, oral, etc.) — not just the climactic action. The 25-50%
zone reliably contains these moments.

Strategy mirrors finish hunter but in a different zone with appropriate
prompt context.
"""
import time
from pathlib import Path
from typing import Optional

from amg.config import (
    BUILDUP_HUNTER_ZONE_START_PCT,
    BUILDUP_HUNTER_ZONE_END_PCT,
    BUILDUP_HUNTER_TOP_N,
    BUILDUP_DEDUP_HAMMING_THRESHOLD,
    SCORE_TIER_2_SUCCESS_FLOOR,
)
from amg.video.reader import VideoReader
from amg.video.frames import measure_sharpness, is_frame_too_dark
from amg.video.dedup import deduplicate_frames
from amg.scoring.orchestrator import score_frames_parallel
from amg.utils.logging import get_logger

log = get_logger("scanning.buildup_hunter")


def run_buildup_hunter(
    video_path: Path,
    duration_sec: float,
    calibration: dict,
    prompt: str,
    system_prompt: Optional[str] = None,
    deadline_sec: Optional[float] = None,
) -> dict:
    """
    Hunt for buildup frames in the 25-50% zone.

    Returns:
        {
            'candidates': [scored frames],
            'aborted': bool,
            'abort_reason': str,
        }
    """
    start_sec = duration_sec * BUILDUP_HUNTER_ZONE_START_PCT
    end_sec = duration_sec * BUILDUP_HUNTER_ZONE_END_PCT
    sharpness_floor = calibration["tier_2_floor"]

    log.info("Buildup hunter: scanning 25-50%",
             start_sec=start_sec, end_sec=end_sec, sharp_floor=sharpness_floor)

    candidates = []
    interval = 2.0  # Sample every 2 sec in buildup zone

    with VideoReader(video_path) as vr:
        for ts, frame in vr.iter_frames_sequential(start_sec, end_sec, interval):
            if deadline_sec and time.time() > deadline_sec:
                log.warn("Buildup hunter: deadline exceeded")
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
                "tier": "buildup_hunter",
            })

    log.info("Buildup hunter: post-gate candidates", count=len(candidates))

    if not candidates:
        return {"candidates": [], "aborted": False}

    # v11.1.2: pass stricter dedup threshold (2 vs global 5). The buildup zone
    # is typically slow (kissing, undressing, oral) and the global threshold
    # collapses too many subtly-different frames to one.
    deduped = deduplicate_frames(candidates, threshold=BUILDUP_DEDUP_HAMMING_THRESHOLD)
    log.info("Buildup hunter: post-dedup candidates", count=len(deduped))

    # Take top N by sharpness
    deduped.sort(key=lambda x: x["sharpness"], reverse=True)
    top = deduped[:BUILDUP_HUNTER_TOP_N]

    # Score
    scored = score_frames_parallel(top, prompt, system_prompt=system_prompt)
    log.info("Buildup hunter: scored", count=len(scored))

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
