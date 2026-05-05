"""
Parallel scoring orchestrator.

Uses ThreadPoolExecutor to send AI requests in parallel to Ollama.
Workers = AI_PARALLEL_WORKERS (matches OLLAMA_NUM_PARALLEL=4).

For I/O-bound HTTP calls, threading is correct (no GIL contention).

v11.1.1: instrumentation added — per-call start/end timestamps and worker IDs
are captured, and a parallelism-factor summary is logged at INFO at the end of
each batch. parallelism_factor = sum(per_call_durations) / wall_time;
4.0 = perfect 4-way overlap, 1.0 = sequential. This is the diagnostic for the
v11.2 finding that 24 calls took 155s (~6.5s/call sequential-equivalent).
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Callable
import numpy as np

from amg.config import AI_PARALLEL_WORKERS, SCORE_TIER_3_SUCCESS_FLOOR, SCORE_MAX
from amg.scoring.ai_client import AIClient, AIResponse
from amg.scoring.parser import parse_ai_response, ScoredFrame, cap_score_for_excellence
from amg.video.frames import measure_sharpness
from amg.utils.logging import get_logger

log = get_logger("scoring.orchestrator")


def score_frames_parallel(
    frames: List[dict],
    prompt: str,
    system_prompt: Optional[str] = None,
    ai_client: Optional[AIClient] = None,
    max_workers: int = AI_PARALLEL_WORKERS,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> List[dict]:
    """
    Score a batch of frames in parallel.

    Args:
        frames: List of dicts, each must have 'frame' (BGR ndarray) and
                'timestamp_sec' (float). Other fields preserved.
        prompt: Scoring prompt to use for all frames.
        system_prompt: Optional system message.
        ai_client: Optional reusable client. Created if not provided.
        max_workers: Concurrent workers (default 4 = OLLAMA_NUM_PARALLEL).
        on_progress: Optional callback(completed, total) for progress reporting.

    Returns:
        Frames with added fields: 'scored_frame' (ScoredFrame), 'ai_response' (AIResponse).
    """
    if not frames:
        return []

    if ai_client is None:
        ai_client = AIClient()

    results = list(frames)  # Copy so we don't mutate input
    total = len(results)
    completed = 0

    # v11.1.1: per-call timing instrumentation. Each tuple = (worker_tid, t_start, t_end).
    call_timings: List[tuple] = []
    timings_lock = threading.Lock()

    def score_one(idx_and_entry):
        idx, entry = idx_and_entry
        frame = entry.get("frame")
        if frame is None:
            entry["scored_frame"] = ScoredFrame()
            entry["ai_response"] = AIResponse(
                success=False,
                error_code="E_NO_FRAME",
                error_message="Frame data missing",
            )
            return idx, entry

        tid = threading.get_ident()
        t0 = time.time()
        ai_resp = ai_client.score_frame(frame, prompt, system_prompt=system_prompt)
        t1 = time.time()
        with timings_lock:
            call_timings.append((tid, t0, t1))

        if ai_resp.success:
            scored = parse_ai_response(ai_resp.raw_text)
            if scored.parse_succeeded and scored.score > 0:
                refined = _refine_score_with_frame_quality(
                    scored,
                    frame,
                    sharpness=entry.get("sharpness"),
                )
                scored.score = round(cap_score_for_excellence(scored, refined), 1)
        else:
            scored = ScoredFrame(parse_succeeded=False)

        entry["scored_frame"] = scored
        entry["ai_response"] = ai_resp
        return idx, entry

    # Submit all jobs
    batch_t0 = time.time()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(score_one, (i, e)): i
            for i, e in enumerate(results)
        }
        for fut in as_completed(futures):
            try:
                idx, entry = fut.result()
                results[idx] = entry
                completed += 1
                if on_progress:
                    on_progress(completed, total)
            except Exception as e:
                log.error("Worker exception", error=str(e))
                completed += 1
                if on_progress:
                    on_progress(completed, total)
    batch_wall = time.time() - batch_t0

    # v11.1.1: parallelism diagnostic. parallelism_factor approaches max_workers when
    # truly parallel; equals ~1.0 when serialized somewhere downstream of this thread pool.
    if call_timings and batch_wall > 0:
        sum_call_time = sum(t1 - t0 for _, t0, t1 in call_timings)
        unique_workers = len({tid for tid, _, _ in call_timings})
        parallelism_factor = sum_call_time / batch_wall
        log.info(
            "Parallel scoring complete",
            calls=len(call_timings),
            wall_sec=round(batch_wall, 2),
            sum_call_sec=round(sum_call_time, 2),
            mean_call_sec=round(sum_call_time / len(call_timings), 2),
            unique_worker_threads=unique_workers,
            max_workers_configured=max_workers,
            parallelism_factor=round(parallelism_factor, 2),
        )

    return results


def count_successes(
    scored_frames: List[dict],
    min_score: float = SCORE_TIER_3_SUCCESS_FLOOR,
) -> int:
    """Count frames that successfully parsed AND scored above threshold."""
    return sum(
        1 for f in scored_frames
        if f.get("scored_frame")
        and f["scored_frame"].parse_succeeded
        and f["scored_frame"].score >= min_score
    )


def count_tier_a_failures(scored_frames: List[dict]) -> dict:
    """Count Tier A failures by code."""
    counts = {"DB1": 0, "DB2": 0, "DB3": 0, "DB4": 0}
    for f in scored_frames:
        scored = f.get("scored_frame")
        if scored and scored.tier_a_fail_code:
            counts[scored.tier_a_fail_code] = counts.get(scored.tier_a_fail_code, 0) + 1
    return counts


def get_zero_rate(scored_frames: List[dict]) -> float:
    """Compute fraction of frames that scored 0 (Tier A fail)."""
    if not scored_frames:
        return 0.0
    zero_count = sum(
        1 for f in scored_frames
        if f.get("scored_frame")
        and f["scored_frame"].score == 0
    )
    return zero_count / len(scored_frames)


def _refine_score_with_frame_quality(
    scored: ScoredFrame,
    frame: np.ndarray,
    *,
    sharpness: Optional[float] = None,
) -> float:
    """
    Add continuous per-frame quality spread to avoid clustered tie scores.

    Base score is still rubric-driven (Tier B/C/D). This adds small, bounded
    adjustments from objective frame quality and confidence signals.
    """
    base = float(scored.score or 0.0)
    sharp = float(sharpness) if sharpness is not None else measure_sharpness(frame)
    adj = _sharpness_score_adjustment(sharp)

    # Action visibility nuance.
    evidence = (scored.action_evidence or "NONE").upper()
    if evidence == "ORAL_CONTACT":
        adj += 1.0
    elif evidence == "EXPLICIT_PENETRATION":
        adj += 0.6
    elif evidence in {"OCCLUDED", "WATER_OCCLUSION"}:
        adj -= 1.2

    # Confidence nuance for penetration-heavy frames.
    if scored.penetration_visible:
        conf = float(scored.penetration_confidence or 0.0)
        adj += max(-1.0, min(2.0, (conf - 0.70) * 4.0))

    # Keep some signal from model's raw score as a bounded tie-breaker.
    if scored.model_score_raw is not None:
        delta_raw = float(scored.model_score_raw) - base
        adj += max(-2.0, min(2.0, delta_raw * 0.20))

    final = max(0.0, min(SCORE_MAX, round(base + adj, 1)))
    return final


def _sharpness_score_adjustment(sharpness: float) -> float:
    """
    Map Laplacian sharpness into a bounded score adjustment.
    """
    if sharpness < 120:
        return -10.0
    if sharpness < 180:
        return -7.0
    if sharpness < 250:
        return -4.0
    if sharpness < 330:
        return -2.0
    if sharpness < 450:
        return 0.0
    if sharpness < 650:
        return 1.5
    if sharpness < 900:
        return 3.0
    return 4.5
