"""
Floor enforcement cascade.

Per user directive: never fewer than COVER_FLOOR covers per scene.

When the main pipeline plus cluster expansion delivers too few candidates,
this module runs progressive fallbacks until the floor is met or all
options are exhausted.

Cascade (from spec § VII):
    Fallback A: rescue band SCORE_FALLBACK_A_LOW..HIGH from already-scored pile
    Fallback B: Wider cluster expansion (already-seen timestamps re-evaluated)
    Fallback C: 30 random-distributed frames, simplified prompt
    Fallback D: Pure CV heuristics (top N by sharpness + skin dominance + face count)
"""
import random
import time
from pathlib import Path
from typing import List, Optional

from amg.config import (
    COVER_FLOOR,
    FALLBACK_C_SAMPLE_COUNT,
    FALLBACK_D_TOP_N,
    SCORE_FALLBACK_A_LOW,
    SCORE_FALLBACK_A_HIGH,
    SCORE_MAX,
)
from amg.video.reader import VideoReader
from amg.video.frames import measure_sharpness, is_frame_too_dark
from amg.video.faces import compute_skin_dominance, detect_faces_in_frame
from amg.video.dedup import deduplicate_frames
from amg.scoring.orchestrator import score_frames_parallel
from amg.scoring.prompt import build_simplified_prompt
from amg.scoring.parser import ScoredFrame
from amg.utils.logging import get_logger

log = get_logger("scanning.fallback")


def run_floor_enforcement_cascade(
    video_path: Path,
    duration_sec: float,
    current_candidates: List[dict],
    all_scored: List[dict],
    calibration: dict,
    target_count: int = COVER_FLOOR,
    deadline_sec: Optional[float] = None,
) -> dict:
    """
    Run fallback cascade to meet the cover floor.

    Args:
        current_candidates: Candidates already accumulated (passing tier threshold)
        all_scored: ALL frames scored so far (including failures, low scores)
        calibration: Threshold dict
        target_count: Minimum count required (default 10)
        deadline_sec: Absolute deadline

    Returns:
        {
            'final_candidates': List[dict] of scenes meeting floor,
            'fallbacks_used': List[str] of which fallbacks ran ('A','B','C','D'),
            'floor_met': bool,
            'aborted': bool,
        }
    """
    candidates = list(current_candidates)
    fallbacks_used = []

    def _run_d_rescue(reason: str, deadline_overrun: bool = False) -> dict:
        # Fallback D is deliberately CV-only. It is the emergency path when AI
        # scanning has already burned the budget but we still need a reviewable
        # package instead of a one-cover failure.
        log.warn(
            "Running Fallback D (pure CV rescue)",
            current=len(candidates),
            reason=reason,
            deadline_overrun=deadline_overrun,
        )
        d_added = _fallback_d(
            video_path, duration_sec,
            exclude_timestamps={c["timestamp_sec"] for c in candidates},
            target_count=target_count - len(candidates),
        )
        candidates.extend(d_added)
        if d_added:
            fallbacks_used.append("D")
            log.warn("Fallback D added candidates", added=len(d_added), total=len(candidates))

        floor_met = len(candidates) >= target_count
        return {
            "final_candidates": candidates[:target_count + 5],
            "fallbacks_used": fallbacks_used,
            "floor_met": floor_met,
            "aborted": deadline_overrun and not floor_met,
            "deadline_overrun": deadline_overrun,
        }

    if len(candidates) >= target_count:
        log.info("Floor already met, no fallback needed", count=len(candidates))
        return {
            "final_candidates": candidates,
            "fallbacks_used": [],
            "floor_met": True,
            "aborted": False,
        }

    # === FALLBACK A: lower the bar on existing scored frames ===
    log.info("Floor not met, running Fallback A", current=len(candidates), target=target_count)
    a_added = _fallback_a(all_scored, exclude=candidates)
    candidates.extend(a_added)
    if a_added:
        fallbacks_used.append("A")
        log.info("Fallback A added candidates", added=len(a_added), total=len(candidates))

    if len(candidates) >= target_count:
        return {
            "final_candidates": candidates[:target_count + 5],  # Slight overshoot allowed
            "fallbacks_used": fallbacks_used,
            "floor_met": True,
            "aborted": False,
        }

    if deadline_sec is not None and time.time() > deadline_sec:
        return _run_d_rescue("deadline_before_fallback_c", deadline_overrun=True)

    # === FALLBACK B: wider cluster expansion on existing high scorers ===
    # (We could implement this but it's similar to cluster.py with wider windows.
    # Skipping for v11.0 — Fallback A + C + D usually suffices)

    # === FALLBACK C: 30 random frames, simplified prompt ===
    log.info("Running Fallback C", current=len(candidates))
    c_added = _fallback_c(
        video_path, duration_sec, calibration,
        exclude_timestamps={c["timestamp_sec"] for c in candidates},
        deadline_sec=deadline_sec,
    )
    candidates.extend(c_added)
    if c_added:
        fallbacks_used.append("C")
        log.info("Fallback C added candidates", added=len(c_added), total=len(candidates))

    if len(candidates) >= target_count:
        return {
            "final_candidates": candidates[:target_count + 5],
            "fallbacks_used": fallbacks_used,
            "floor_met": True,
            "aborted": False,
        }

    if deadline_sec is not None and time.time() > deadline_sec:
        return _run_d_rescue("deadline_after_fallback_c", deadline_overrun=True)

    # === FALLBACK D: pure CV, no AI ===
    return _run_d_rescue("normal_floor_enforcement")


def _fallback_a(all_scored: List[dict], exclude: List[dict]) -> List[dict]:
    """Take frames in the low rescue band from the already-scored pile."""
    excluded_ts = {c["timestamp_sec"] for c in exclude}
    rescued = []
    for f in all_scored:
        ts = f.get("timestamp_sec")
        if ts in excluded_ts:
            continue
        scored = f.get("scored_frame")
        if not scored or not scored.parse_succeeded:
            continue
        if SCORE_FALLBACK_A_LOW <= scored.score < SCORE_FALLBACK_A_HIGH:
            rescued.append(f)
    rescued.sort(key=lambda x: x["scored_frame"].score, reverse=True)
    return rescued


def _fallback_c(
    video_path: Path,
    duration_sec: float,
    calibration: dict,
    exclude_timestamps: set,
    deadline_sec: Optional[float] = None,
) -> List[dict]:
    """Sample 30 random frames, score with simplified prompt."""
    # Random distributed timestamps
    timestamps = []
    for _ in range(FALLBACK_C_SAMPLE_COUNT * 2):  # Oversample, dedup against exclude
        ts = random.uniform(duration_sec * 0.05, duration_sec * 0.95)
        if not any(abs(ts - ex) < 5 for ex in exclude_timestamps):
            timestamps.append(ts)
        if len(timestamps) >= FALLBACK_C_SAMPLE_COUNT:
            break

    if not timestamps:
        return []

    candidates = []
    sharpness_floor = calibration["tier_3_floor"]

    with VideoReader(video_path) as vr:
        frames = vr.get_frames_at(timestamps)
        for ts, frame in zip(timestamps, frames):
            if frame is None or is_frame_too_dark(frame):
                continue
            sharp = measure_sharpness(frame)
            if sharp < sharpness_floor:
                continue
            candidates.append({
                "timestamp_sec": ts,
                "frame": frame,
                "sharpness": sharp,
                "tier": "fallback_c",
            })

    if not candidates:
        return []

    deduped = deduplicate_frames(candidates)
    simplified_prompt = build_simplified_prompt()
    scored = score_frames_parallel(deduped, simplified_prompt)

    # Take any that parsed and got non-zero score
    return [
        f for f in scored
        if f.get("scored_frame")
        and f["scored_frame"].parse_succeeded
        and f["scored_frame"].score > 0
    ]


def _fallback_d(
    video_path: Path,
    duration_sec: float,
    exclude_timestamps: set,
    target_count: int,
) -> List[dict]:
    """
    Pure CV rescue: sample many frames, rank by composite CV score.

    No AI calls. Frames assigned a synthetic score for filename only.
    """
    # Sample 80 evenly distributed frames
    n_samples = 80
    interval = duration_sec / n_samples
    timestamps = [
        i * interval for i in range(n_samples)
        if not any(abs(i * interval - ex) < 3 for ex in exclude_timestamps)
    ]

    candidates = []
    with VideoReader(video_path) as vr:
        frames = vr.get_frames_at(timestamps)
        for ts, frame in zip(timestamps, frames):
            if frame is None or is_frame_too_dark(frame):
                continue
            sharp = measure_sharpness(frame)
            skin = compute_skin_dominance(frame)
            faces = detect_faces_in_frame(frame)
            face_count = len(faces)

            # Composite CV score (0–100 scale, heuristic only — matches AI scale loosely)
            # Sharpness contributes 0-40 (normalized at 800)
            # Skin dominance contributes 0-40 (skin is signal of nudity)
            # Face presence contributes 0-20
            raw = min(4, sharp / 200) + min(4, skin * 8) + min(2, face_count * 1.5)
            cv_score = min(SCORE_MAX, 10.0 * raw)

            # Build a synthetic ScoredFrame for downstream uniformity
            synthetic = ScoredFrame(
                score=cv_score,
                tier_a_pass=True,
                type_="COMPOSITION",
                gaze="DIRECT" if face_count > 0 else "REAR",
                aesthetic="STANDARD",
                parse_succeeded=True,
            )

            candidates.append({
                "timestamp_sec": ts,
                "frame": frame,
                "sharpness": sharp,
                "tier": "fallback_d",
                "scored_frame": synthetic,
                "ai_response": None,
            })

    candidates.sort(key=lambda x: x["scored_frame"].score, reverse=True)
    return candidates[:target_count + 3]  # A few extras for safety
