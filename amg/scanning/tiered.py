"""
Tiered scan — adaptive 3-tier frame discovery.

Tier 1: Strict thresholds (75th percentile sharpness). Quick scan.
Tier 2: Loosened (50th percentile). Triggered if Tier 1 yields < min candidates.
Tier 3: Lenient (25th percentile). Last resort before fallback cascade.

Each tier:
1. Sequentially scan video
2. Apply CV gates (sharpness, motion, dark check)
3. Deduplicate candidates
4. Send survivors to AI scoring (parallel)
5. Check if min candidates achieved
"""
import time
from pathlib import Path
from typing import List, Optional

from amg.config import (
    TIER_1_INTERVAL,
    TIER_2_INTERVAL,
    TIER_3_INTERVAL,
    TIER_1_INTERVAL_MAX,
    TIER_2_INTERVAL_MAX,
    TIER_3_INTERVAL_MAX,
    MOTION_CAP_TIER_1,
    MOTION_CAP_TIER_2,
    MOTION_CAP_TIER_3,
    SCORE_TIER_1_SUCCESS_FLOOR,
    SCORE_TIER_2_SUCCESS_FLOOR,
    SCORE_TIER_3_SUCCESS_FLOOR,
    MIN_CANDIDATES_PER_TIER,
    TIER_SCAN_MAX_EXTRACTED_FRAMES_PER_TIER,
    TIER_SCAN_MAX_AI_FRAMES_PER_TIER,
    TIER_SCAN_MAX_WALL_SEC_PER_TIER,
    get_adaptive_interval,
)
from amg.video.reader import VideoReader
from amg.video.frames import (
    measure_sharpness,
    measure_motion,
    is_frame_too_dark,
)
from amg.video.dedup import deduplicate_frames
from amg.scoring.orchestrator import score_frames_parallel, count_successes
from amg.utils.logging import get_logger
import cv2

log = get_logger("scanning.tiered")


def run_tiered_scan(
    video_path: Path,
    duration_sec: float,
    calibration: dict,
    prompt: str,
    system_prompt: Optional[str] = None,
    deadline_sec: Optional[float] = None,
) -> dict:
    """
    Run tiered scan, escalating until success or all tiers exhausted.

    Args:
        video_path: Video file path
        duration_sec: Total video duration
        calibration: Output from calibrate_thresholds()
        prompt: AI scoring prompt
        system_prompt: Optional system message
        deadline_sec: Optional absolute deadline (time.time() comparison)

    Returns:
        {
            'tier_used': 1/2/3,
            'candidates': [scored frames at or above tier threshold],
            'all_scored': [all scored frames from all tiers],
            'aborted': bool,
            'abort_reason': str,
        }
    """
    log.info("Starting tiered scan",
             tier_1_floor=calibration["tier_1_floor"],
             tier_2_floor=calibration["tier_2_floor"],
             tier_3_floor=calibration["tier_3_floor"])

    all_scored = []
    seen_timestamps = set()
    tier_stats = {}
    tier_1_interval = get_adaptive_interval(TIER_1_INTERVAL, duration_sec, TIER_1_INTERVAL_MAX)
    tier_2_interval = get_adaptive_interval(TIER_2_INTERVAL, duration_sec, TIER_2_INTERVAL_MAX)
    tier_3_interval = get_adaptive_interval(TIER_3_INTERVAL, duration_sec, TIER_3_INTERVAL_MAX)

    # Tier 1
    tier_1 = _run_tier(
        video_path, duration_sec,
        sharpness_floor=calibration["tier_1_floor"],
        motion_cap=MOTION_CAP_TIER_1,
        interval=tier_1_interval,
        score_floor=SCORE_TIER_1_SUCCESS_FLOOR,
        prompt=prompt,
        system_prompt=system_prompt,
        seen_timestamps=seen_timestamps,
        deadline_sec=deadline_sec,
        tier_name="tier_1",
    )
    tier_stats["tier_1"] = _tier_stat_payload(tier_1)
    all_scored.extend(tier_1["scored_frames"])

    if tier_1.get("aborted"):
        return {
            "tier_used": 1,
            "candidates": _filter_passing(all_scored, SCORE_TIER_1_SUCCESS_FLOOR),
            "all_scored": all_scored,
            "aborted": True,
            "abort_reason": tier_1.get("abort_reason"),
            "tier_stats": tier_stats,
        }

    if count_successes(tier_1["scored_frames"], SCORE_TIER_1_SUCCESS_FLOOR) >= MIN_CANDIDATES_PER_TIER:
        log.info("Tier 1 sufficient, skipping Tier 2/3", count=len(tier_1["scored_frames"]))
        return {
            "tier_used": 1,
            "candidates": _filter_passing(all_scored, SCORE_TIER_1_SUCCESS_FLOOR),
            "all_scored": all_scored,
            "aborted": False,
            "tier_stats": tier_stats,
        }

    # Tier 2
    log.info("Tier 1 insufficient, escalating to Tier 2")
    tier_2 = _run_tier(
        video_path, duration_sec,
        sharpness_floor=calibration["tier_2_floor"],
        motion_cap=MOTION_CAP_TIER_2,
        interval=tier_2_interval,
        score_floor=SCORE_TIER_2_SUCCESS_FLOOR,
        prompt=prompt,
        system_prompt=system_prompt,
        seen_timestamps=seen_timestamps,
        deadline_sec=deadline_sec,
        tier_name="tier_2",
    )
    tier_stats["tier_2"] = _tier_stat_payload(tier_2)
    all_scored.extend(tier_2["scored_frames"])

    if tier_2.get("aborted"):
        return {
            "tier_used": 2,
            "candidates": _filter_passing(all_scored, SCORE_TIER_2_SUCCESS_FLOOR),
            "all_scored": all_scored,
            "aborted": True,
            "abort_reason": tier_2.get("abort_reason"),
            "tier_stats": tier_stats,
        }

    total_passing = count_successes(all_scored, SCORE_TIER_2_SUCCESS_FLOOR)
    if total_passing >= MIN_CANDIDATES_PER_TIER:
        log.info("Tier 2 sufficient", total_passing=total_passing)
        return {
            "tier_used": 2,
            "candidates": _filter_passing(all_scored, SCORE_TIER_2_SUCCESS_FLOOR),
            "all_scored": all_scored,
            "aborted": False,
            "tier_stats": tier_stats,
        }

    # Tier 3
    log.info("Tier 2 insufficient, escalating to Tier 3")
    tier_3 = _run_tier(
        video_path, duration_sec,
        sharpness_floor=calibration["tier_3_floor"],
        motion_cap=MOTION_CAP_TIER_3,
        interval=tier_3_interval,
        score_floor=SCORE_TIER_3_SUCCESS_FLOOR,
        prompt=prompt,
        system_prompt=system_prompt,
        seen_timestamps=seen_timestamps,
        deadline_sec=deadline_sec,
        tier_name="tier_3",
    )
    tier_stats["tier_3"] = _tier_stat_payload(tier_3)
    all_scored.extend(tier_3["scored_frames"])

    return {
        "tier_used": 3,
        "candidates": _filter_passing(all_scored, SCORE_TIER_3_SUCCESS_FLOOR),
        "all_scored": all_scored,
        "aborted": tier_3.get("aborted", False),
        "abort_reason": tier_3.get("abort_reason"),
        "tier_stats": tier_stats,
    }


def _run_tier(
    video_path,
    duration_sec,
    sharpness_floor,
    motion_cap,
    interval,
    score_floor,
    prompt,
    system_prompt,
    seen_timestamps,
    deadline_sec,
    tier_name,
):
    """Run one tier: extract candidates, dedupe, score in parallel."""
    candidates = []
    prev_gray = None
    tier_started = time.time()
    extracted_capped = False
    ai_capped = False

    log.info(f"{tier_name}: extracting candidates",
             sharp_floor=sharpness_floor, motion_cap=motion_cap, interval=interval)

    with VideoReader(video_path) as vr:
        for ts, frame in vr.iter_frames_sequential(0, duration_sec, interval):
            # Tier hard wall timeout guard (separate from whole-scene deadline).
            if (
                TIER_SCAN_MAX_WALL_SEC_PER_TIER > 0
                and (time.time() - tier_started) > TIER_SCAN_MAX_WALL_SEC_PER_TIER
            ):
                log.warn(
                    f"{tier_name}: tier wall-time cap exceeded",
                    wall_sec=round(time.time() - tier_started, 2),
                    cap_sec=TIER_SCAN_MAX_WALL_SEC_PER_TIER,
                )
                return {
                    "scored_frames": [],
                    "aborted": True,
                    "abort_reason": "E_TIER_SCAN_TIMEOUT",
                    "frames_extracted": len(candidates),
                    "candidates_after_cv": len(candidates),
                    "candidates_after_dedup": 0,
                    "ai_scored_count": 0,
                    "passing_count": 0,
                    "extracted_capped": extracted_capped,
                    "ai_capped": ai_capped,
                    "wall_sec": round(time.time() - tier_started, 3),
                }

            # Deadline check
            if deadline_sec and time.time() > deadline_sec:
                log.warn(f"{tier_name}: deadline exceeded during extraction",
                         frames_seen=len(candidates))
                return {
                    "scored_frames": [],
                    "aborted": True,
                    "abort_reason": "E_TIMEOUT_HARD",
                    "frames_extracted": len(candidates),
                    "candidates_after_cv": len(candidates),
                    "candidates_after_dedup": 0,
                    "ai_scored_count": 0,
                    "passing_count": 0,
                    "extracted_capped": extracted_capped,
                    "ai_capped": ai_capped,
                    "wall_sec": round(time.time() - tier_started, 3),
                }

            # Skip if we've already seen this timestamp (from prior tier)
            ts_key = round(ts, 1)
            if ts_key in seen_timestamps:
                continue

            # Quick gates
            if is_frame_too_dark(frame):
                continue

            sharp = measure_sharpness(frame)
            if sharp < sharpness_floor:
                continue

            # Motion check (skip first frame, no prev to compare)
            if prev_gray is not None:
                small = cv2.resize(frame, (640, 360))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                motion = measure_motion(prev_gray, gray)
                if motion > motion_cap:
                    prev_gray = gray
                    continue
                prev_gray = gray
            else:
                small = cv2.resize(frame, (640, 360))
                prev_gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

            # Frame survives all gates
            candidates.append({
                "timestamp_sec": ts,
                "frame": frame,
                "sharpness": sharp,
                "tier": tier_name,
            })
            seen_timestamps.add(ts_key)

            if (
                TIER_SCAN_MAX_EXTRACTED_FRAMES_PER_TIER > 0
                and len(candidates) >= TIER_SCAN_MAX_EXTRACTED_FRAMES_PER_TIER
            ):
                extracted_capped = True
                log.warn(
                    f"{tier_name}: extracted-frame cap hit",
                    cap=TIER_SCAN_MAX_EXTRACTED_FRAMES_PER_TIER,
                )
                break

    log.info(f"{tier_name}: post-CV-gate candidates", count=len(candidates))

    # Deduplicate before AI scoring (saves AI calls)
    deduped = deduplicate_frames(candidates)
    log.info(f"{tier_name}: post-dedup candidates", count=len(deduped))

    if not deduped:
        return {
            "scored_frames": [],
            "aborted": False,
            "frames_extracted": len(candidates),
            "candidates_after_cv": len(candidates),
            "candidates_after_dedup": len(deduped),
            "ai_scored_count": 0,
            "passing_count": 0,
            "extracted_capped": extracted_capped,
            "ai_capped": ai_capped,
            "wall_sec": round(time.time() - tier_started, 3),
        }

    ai_batch = deduped
    if TIER_SCAN_MAX_AI_FRAMES_PER_TIER > 0 and len(ai_batch) > TIER_SCAN_MAX_AI_FRAMES_PER_TIER:
        ai_capped = True
        ai_batch = sorted(
            ai_batch,
            key=lambda x: (
                -float(x.get("sharpness") or 0.0),
                float(x.get("timestamp_sec") or 0.0),
            ),
        )[:TIER_SCAN_MAX_AI_FRAMES_PER_TIER]
        log.warn(
            f"{tier_name}: AI scoring cap hit",
            deduped=len(deduped),
            ai_scored=len(ai_batch),
            cap=TIER_SCAN_MAX_AI_FRAMES_PER_TIER,
        )

    # Score in parallel
    scored = score_frames_parallel(ai_batch, prompt, system_prompt=system_prompt)
    passing_count = count_successes(scored, score_floor)
    log.info(f"{tier_name}: AI-scored", count=len(scored), passing=passing_count)

    return {
        "scored_frames": scored,
        "aborted": False,
        "frames_extracted": len(candidates),
        "candidates_after_cv": len(candidates),
        "candidates_after_dedup": len(deduped),
        "ai_scored_count": len(ai_batch),
        "passing_count": passing_count,
        "extracted_capped": extracted_capped,
        "ai_capped": ai_capped,
        "wall_sec": round(time.time() - tier_started, 3),
    }


def _tier_stat_payload(tier_result: dict) -> dict:
    if not isinstance(tier_result, dict):
        return {}
    return {
        "aborted": bool(tier_result.get("aborted", False)),
        "abort_reason": tier_result.get("abort_reason"),
        "frames_extracted": int(tier_result.get("frames_extracted", 0) or 0),
        "candidates_after_cv": int(tier_result.get("candidates_after_cv", 0) or 0),
        "candidates_after_dedup": int(tier_result.get("candidates_after_dedup", 0) or 0),
        "ai_scored_count": int(tier_result.get("ai_scored_count", 0) or 0),
        "passing_count": int(tier_result.get("passing_count", 0) or 0),
        "extracted_capped": bool(tier_result.get("extracted_capped", False)),
        "ai_capped": bool(tier_result.get("ai_capped", False)),
        "wall_sec": float(tier_result.get("wall_sec", 0.0) or 0.0),
    }


def _filter_passing(scored_frames, min_score):
    """Filter frames that scored at or above threshold."""
    return [
        f for f in scored_frames
        if f.get("scored_frame")
        and f["scored_frame"].parse_succeeded
        and f["scored_frame"].score >= min_score
    ]
