"""
Streaming scan — single-pass producer/consumer pipeline.

Replaces the classic ``tier_scan + finish_hunter + buildup_hunter +
cluster + floor_enforcement`` chain with one continuous flow:

    [decoder + CV sieve thread]
             │   pushes survivors into a bounded priority queue
             ▼
    [AI dispatcher pump (this thread)]
             │   keeps ``max_workers`` requests in flight to Ollama
             ▼
    [LiveSelector] — fused score + post-AI sharpness gate

The motivating diagnosis (2026-05-08 audit on Y&B_003): the classic path
spent 24:34 wall-time, made 15 AI calls, and used the GPU for ~16
seconds. Each phase decoded the whole video and applied an over-strict
percentile-based sharpness gate (tier_1_floor=674 — calibration was
dominated by a few static frames). Almost nothing reached the AI; the
fallback cascade then shipped 15 blurry covers via a *looser* gate plus
the *simplified* prompt — the classic too-strict-then-too-lenient
failure mode.

The streaming scan fixes both:
  1. ONE decode pass, sieve uses a *relative* sharpness floor (10th
     percentile of a rolling 200-sample window) rather than a brittle
     absolute calibration percentile. This keeps the GPU fed.
  2. The selector fuses AI score with sharpness and applies a post-AI
     sharpness gate at the *25th percentile of actually-scored frames*,
     so the AI can't outvote real blur.

This module is feature-flagged behind ``AMG_STREAMING_SCAN=1``. The
classic pipeline stays in tree until the new path has shipped enough
scenes to trust.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from amg.config import (
    AI_PARALLEL_WORKERS,
    ANALYSIS_FRAME_SIZE,
    BUILDUP_HUNTER_ZONE_END_PCT,
    BUILDUP_HUNTER_ZONE_START_PCT,
    DEDUP_HAMMING_THRESHOLD,
    FINISH_HUNTER_ZONE_START_PCT,
    MOTION_CAP_TIER_3,
    SHARPNESS_HARD_FLOOR,
    STREAMING_FRAME_CACHE_MAX_MB,
    STREAMING_ANALYSIS_MAX_HEIGHT,
    STREAMING_ANALYSIS_MAX_WIDTH,
    STREAMING_LOW_RES_ANALYSIS_ENABLED,
    STREAMING_SCAN_INTERVAL_SEC,
    STREAMING_SCAN_MAX_AI_CALLS,
    STREAMING_SCAN_MAX_QUEUED,
    STREAMING_SEGMENT_COUNT,
    STREAMING_SEGMENT_MIN_DURATION_SEC,
    normalize_position_label,
)
from amg.scanning.selector import LiveSelector
from amg.scoring.ai_client import AIClient, AIResponse
from amg.scoring.parser import ScoredFrame, cap_score_for_excellence, parse_ai_response
from amg.video.dedup import are_near_duplicates, compute_perceptual_hash
from amg.video.frame_cache import FrameCache
from amg.video.frames import (
    analysis_gray,
    is_frame_too_dark,
    measure_motion,
    measure_sharpness,
    runtime_info as cv_runtime_info,
)
from amg.video.reader import VideoReader
from amg.utils.logging import get_logger

log = get_logger("scanning.stream")


_SENTINEL = object()


def _zone_tags(ts: float, duration_sec: float) -> List[str]:
    if duration_sec <= 0:
        return []
    pct = float(ts) / float(duration_sec)
    tags: List[str] = []
    if pct >= FINISH_HUNTER_ZONE_START_PCT:
        tags.append("finish_zone")
    if BUILDUP_HUNTER_ZONE_START_PCT <= pct <= BUILDUP_HUNTER_ZONE_END_PCT:
        tags.append("buildup_zone")
    return tags


def _candidate_priority(
    *,
    sharpness: float,
    motion: float,
    zone_tags: List[str],
) -> float:
    """Higher = scored sooner. Sharpness dominates because the GPU's job
    is easier on sharp frames, plus the eventual fused-score winners are
    almost always sharp. Zone bonuses give finish/buildup a head start."""
    score = float(sharpness or 0.0)
    if motion is not None:
        # Light motion is fine; heavy motion already failed the gate.
        score -= min(50.0, max(0.0, float(motion) - 1.0) * 10.0)
    if "finish_zone" in zone_tags:
        score += 200.0
    if "buildup_zone" in zone_tags:
        score += 120.0
    return score


def run_stream_scan(
    video_path: Path,
    duration_sec: float,
    prompt: str,
    *,
    target_count: int,
    cover_cap: int,
    deadline_sec: Optional[float] = None,
    system_prompt: Optional[str] = None,
    ai_client: Optional[AIClient] = None,
    on_log: Callable[[str], None] = lambda _s: None,
    on_progress: Callable[[int], None] = lambda _p: None,
    interval_sec: Optional[float] = None,
    max_workers: Optional[int] = None,
    max_ai_calls: Optional[int] = None,
    max_queued: Optional[int] = None,
    frame_cache_max_mb: Optional[int] = None,
    low_res_analysis: Optional[bool] = None,
    segment_count: Optional[int] = None,
    selector_overrides: Optional[Dict[str, Any]] = None,
    progress_low: int = 20,
    progress_high: int = 88,
) -> Dict[str, Any]:
    """Run the streaming scan and return final candidates + stats.

    Returns dict shaped like::

        {
          'picks':           [candidate dicts, len <= cover_cap],
          'all_scored':      [every candidate sent to AI, with results],
          'frame_cache':     FrameCache (caller passes to save_covers),
          'stats':           { 'submitted', 'completed', 'skipped', ... },
          'selector_stats':  { ... },
          'sharpness_floor_used': float,
          'gate_relaxed':    bool,
          'aborted':         bool,
          'abort_reason':    Optional[str],
        }

    Caller is responsible for clearing ``frame_cache`` once the output
    phase has consumed it (or just letting it fall out of scope).
    """
    interval = float(interval_sec or STREAMING_SCAN_INTERVAL_SEC)
    workers = int(max_workers or AI_PARALLEL_WORKERS or 1)
    ai_call_cap = int(max_ai_calls if max_ai_calls is not None else STREAMING_SCAN_MAX_AI_CALLS)
    queue_cap = int(max_queued or STREAMING_SCAN_MAX_QUEUED)
    cache_mb = int(frame_cache_max_mb or STREAMING_FRAME_CACHE_MAX_MB)
    low_res = (
        STREAMING_LOW_RES_ANALYSIS_ENABLED
        if low_res_analysis is None
        else bool(low_res_analysis)
    )
    analysis_size = (
        int(STREAMING_ANALYSIS_MAX_WIDTH),
        int(STREAMING_ANALYSIS_MAX_HEIGHT),
    )
    if analysis_size[0] <= 0 or analysis_size[1] <= 0:
        low_res = False
    requested_segments = int(segment_count or STREAMING_SEGMENT_COUNT or 1)
    effective_segments = max(1, requested_segments)
    if duration_sec < float(STREAMING_SEGMENT_MIN_DURATION_SEC):
        effective_segments = 1
    # Keep pathological env values from spawning hundreds of decoders.
    effective_segments = min(effective_segments, 16)

    selector_kwargs = dict(selector_overrides or {})
    selector = LiveSelector(target_k=cover_cap, **selector_kwargs)
    cache = FrameCache(max_bytes=cache_mb * 1024 * 1024)
    cand_q: "queue.PriorityQueue[Any]" = queue.PriorityQueue(maxsize=queue_cap)
    sieve_done = threading.Event()
    sieve_done_lock = threading.Lock()
    sieve_done_count = 0
    stop_sieve = threading.Event()
    sieve_error: Dict[str, Any] = {}
    sieve_stats: Dict[str, Any] = {
        "frames_seen": 0,
        "frames_dark": 0,
        "frames_below_floor": 0,
        "frames_high_motion": 0,
        "frames_dup": 0,
        "candidates_emitted": 0,
        "queue_overflows": 0,
        "segment_count": effective_segments,
        "low_res_analysis": bool(low_res),
        "analysis_frame_size": list(analysis_size) if low_res else None,
        "full_res_cached": not bool(low_res),
        "segment_stats": [],
        "video_backend": None,
        "gpu_cv_mode": cv_runtime_info().get("mode"),
        "gpu_cv_backend": cv_runtime_info().get("backend"),
        "gpu_cv_reason": cv_runtime_info().get("reason"),
        # Decode vs CV wall split — exposes whether the CPU video decoder
        # is the actual bottleneck (it almost always is on long-form
        # 1080p HEVC). Sum of time spent waiting for the PyAV iterator
        # to yield the next frame (decode + colorspace convert) vs sum
        # of time spent on CV ops (sharpness, motion, dedup hash).
        "decode_wall_sec": 0.0,
        "cv_wall_sec": 0.0,
    }
    # Capture raw AI responses for diagnosis when scoring silently
    # produces zero pickable frames (parse_succeeded=False or score==0).
    # Bounded so a long scan can't blow the JSON sidecar; the first
    # handful of failures is enough to read the model output.
    debug_ai = (
        str(os.environ.get("AMG_STREAMING_DEBUG_AI_RESPONSES", "0")).strip().lower()
        in {"1", "true", "yes", "on"}
    )
    raw_ai_samples: List[Dict[str, Any]] = []
    raw_ai_lock = threading.Lock()
    raw_ai_cap = int(os.environ.get("AMG_STREAMING_DEBUG_AI_RESPONSES_CAP", "8"))

    # Tie-breaker for PriorityQueue when two candidates have identical
    # priority — using a monotonically increasing int ensures we never
    # try to compare candidate dicts (which would fail).
    seq_lock = threading.Lock()
    seq_counter = [0]

    def _next_seq() -> int:
        with seq_lock:
            seq_counter[0] += 1
            return seq_counter[0]

    if ai_client is None:
        ai_client = AIClient()

    stats_lock = threading.Lock()

    # ----- sieve thread(s) -----
    def sieve_loop(segment_idx: int, start_sec: float, end_sec: float) -> None:
        nonlocal sieve_done_count
        local_stats: Dict[str, Any] = {
            "segment_idx": segment_idx,
            "start_sec": float(start_sec),
            "end_sec": float(end_sec),
            "video_backend": None,
            "frames_seen": 0,
            "frames_dark": 0,
            "frames_below_floor": 0,
            "frames_high_motion": 0,
            "frames_dup": 0,
            "candidates_emitted": 0,
            "queue_overflows": 0,
            "decode_wall_sec": 0.0,
            "cv_wall_sec": 0.0,
        }
        try:
            sharpness_window: List[float] = []
            seen_hashes: List[Any] = []
            prev_gray: Optional[np.ndarray] = None

            with VideoReader(video_path) as vr:
                local_stats["video_backend"] = getattr(vr, "backend_name", "unknown")
                # Decode wall = time spent waiting for the PyAV iterator
                # to yield the next frame. CV wall = time spent on the
                # synchronous CV ops between yields. The two together
                # should sum to ~the sieve thread wall time; the
                # decode/CV ratio is what tells us whether NVDEC will
                # actually move the needle.
                _yield_t = time.time()
                if low_res and hasattr(vr, "iter_frames_sequential_scaled"):
                    frame_iter = vr.iter_frames_sequential_scaled(
                        start_sec,
                        end_sec,
                        interval,
                        analysis_size,
                    )
                else:
                    frame_iter = vr.iter_frames_sequential(start_sec, end_sec, interval)
                for ts, frame in frame_iter:
                    if stop_sieve.is_set():
                        break
                    local_stats["decode_wall_sec"] += time.time() - _yield_t
                    _cv_t0 = time.time()
                    try:
                        if stop_sieve.is_set():
                            break
                        if deadline_sec is not None and time.time() >= deadline_sec:
                            break

                        local_stats["frames_seen"] += 1
                        if is_frame_too_dark(frame):
                            local_stats["frames_dark"] += 1
                            continue

                        sharp = measure_sharpness(frame)

                        # Relative sharpness floor: 10th percentile of the
                        # last 200 samples, never below half the absolute
                        # hard floor. Bootstraps very loose so we don't
                        # starve the GPU at the start of the scene.
                        sharpness_window.append(sharp)
                        if len(sharpness_window) > 200:
                            sharpness_window = sharpness_window[-200:]
                        if len(sharpness_window) >= 30:
                            rel_floor = float(np.percentile(sharpness_window, 10))
                        else:
                            rel_floor = SHARPNESS_HARD_FLOOR / 4.0
                        rel_floor = max(rel_floor, SHARPNESS_HARD_FLOOR / 2.0)

                        if sharp < rel_floor:
                            local_stats["frames_below_floor"] += 1
                            continue

                        gray = analysis_gray(frame)
                        motion = measure_motion(prev_gray, gray) if prev_gray is not None else 0.0
                        prev_gray = gray

                        if motion > MOTION_CAP_TIER_3:
                            local_stats["frames_high_motion"] += 1
                            continue

                        phash = compute_perceptual_hash(frame)
                        if phash is not None:
                            if any(
                                are_near_duplicates(phash, prev, DEDUP_HAMMING_THRESHOLD)
                                for prev in seen_hashes
                            ):
                                local_stats["frames_dup"] += 1
                                continue
                            seen_hashes.append(phash)
                            # Bound dedup memory — 600 hashes ≈ 5KB and
                            # covers ~10 minutes worth of "recent" frames at
                            # 1 fps. Older frames drop out so a long-form
                            # scene's late candidates don't get falsely
                            # de-duped against ancient ones.
                            if len(seen_hashes) > 600:
                                seen_hashes = seen_hashes[-600:]

                        zone_tags = _zone_tags(ts, duration_sec)
                        priority = _candidate_priority(
                            sharpness=sharp,
                            motion=motion,
                            zone_tags=zone_tags,
                        )

                        candidate = {
                            "timestamp_sec": float(ts),
                            "frame": frame,  # analysis/AI frame; may be scaled
                            "sharpness": float(sharp),
                            "motion": float(motion or 0.0),
                            "tier": "stream",
                            "segment_idx": segment_idx,
                            "zone_tags": zone_tags,
                        }
                        if low_res:
                            candidate["_analysis_frame_only"] = True
                        if phash is not None:
                            candidate["_phash"] = str(phash)

                        # In low-res mode the frame is deliberately analysis-
                        # sized. Cache only that copy and let save_covers()
                        # re-extract full-res frames for the final picks.
                        if low_res:
                            cache.put(float(ts), analysis_frame=frame)
                        else:
                            cache.put(float(ts), full_frame=frame)

                        try:
                            cand_q.put(
                                (-priority, _next_seq(), candidate),
                                timeout=2.0,
                            )
                            local_stats["candidates_emitted"] += 1
                        except queue.Full:
                            local_stats["queue_overflows"] += 1
                            # Dispatcher is saturated; drop this one rather
                            # than block the producer. Selector will pick
                            # from the higher-priority backlog.
                            cache.discard(float(ts))
                    finally:
                        local_stats["cv_wall_sec"] += time.time() - _cv_t0
                        _yield_t = time.time()
        except Exception as exc:  # noqa: BLE001 - reported back to dispatcher
            sieve_error["exc"] = exc
            log.error("stream sieve failed", error=str(exc))
        finally:
            with stats_lock:
                for key in (
                    "frames_seen",
                    "frames_dark",
                    "frames_below_floor",
                    "frames_high_motion",
                    "frames_dup",
                    "candidates_emitted",
                    "queue_overflows",
                    "decode_wall_sec",
                    "cv_wall_sec",
                ):
                    sieve_stats[key] += local_stats[key]
                if local_stats.get("video_backend"):
                    if sieve_stats.get("video_backend") is None:
                        sieve_stats["video_backend"] = local_stats.get("video_backend")
                sieve_stats["segment_stats"].append(local_stats)
            with sieve_done_lock:
                sieve_done_count += 1
                if sieve_done_count >= effective_segments:
                    sieve_done.set()
            try:
                cand_q.put((float("inf"), _next_seq(), _SENTINEL), timeout=2.0)
            except queue.Full:
                pass

    def _segment_ranges() -> List[tuple[int, float, float]]:
        if effective_segments <= 1:
            return [(0, 0.0, float(duration_sec))]
        span = float(duration_sec) / float(effective_segments)
        ranges: List[tuple[int, float, float]] = []
        for idx in range(effective_segments):
            start = idx * span
            end = float(duration_sec) if idx == effective_segments - 1 else (idx + 1) * span
            ranges.append((idx, start, end))
        return ranges

    sieve_threads = [
        threading.Thread(
            target=sieve_loop,
            args=(idx, start, end),
            name=f"stream-sieve-{idx}",
            daemon=True,
        )
        for idx, start, end in _segment_ranges()
    ]
    for sieve_thread in sieve_threads:
        sieve_thread.start()

    # ----- AI dispatcher pump -----
    in_flight: Dict[Any, Dict[str, Any]] = {}
    submitted = 0
    completed = 0
    ai_failures = 0
    parse_failed_count = 0
    score_zero_count = 0
    ai_wall_total = 0.0
    skipped = 0
    aborted = False
    abort_reason: Optional[str] = None
    all_scored: List[Dict[str, Any]] = []
    dispatcher_started = time.time()

    def _capture_raw_ai_sample(
        candidate: Dict[str, Any],
        ai_resp: AIResponse,
        scored: ScoredFrame,
        reason: str,
    ) -> None:
        # Bounded sample of raw model text so we can read why the
        # selector pool is empty. Triggered automatically on every
        # parse_failed/score_zero (this is the failure mode we're
        # diagnosing), or always if AMG_STREAMING_DEBUG_AI_RESPONSES=1.
        with raw_ai_lock:
            if len(raw_ai_samples) >= raw_ai_cap and not debug_ai:
                return
            raw_text = ai_resp.raw_text or ""
            sample = {
                "timestamp_sec": candidate.get("timestamp_sec"),
                "reason": reason,
                "ai_success": bool(ai_resp.success),
                "ai_error_code": getattr(ai_resp, "error_code", None),
                "ai_error_message": (getattr(ai_resp, "error_message", "") or "")[:200],
                "raw_text_truncated": raw_text[:1500],
                "raw_text_len": len(raw_text),
                "parsed_score": float(scored.score) if scored.score is not None else None,
                "parse_succeeded": bool(scored.parse_succeeded),
            }
            raw_ai_samples.append(sample)

    def _score_one(candidate: Dict[str, Any]) -> Dict[str, Any]:
        frame = candidate.get("frame")
        if frame is None:
            candidate["scored_frame"] = ScoredFrame(parse_succeeded=False)
            candidate["ai_response"] = AIResponse(
                success=False,
                error_code="E_NO_FRAME",
                error_message="frame missing",
            )
            return candidate
        t0 = time.time()
        ai_resp = ai_client.score_frame(frame, prompt, system_prompt=system_prompt)
        candidate["ai_wall_sec"] = round(time.time() - t0, 3)
        if ai_resp.success:
            scored = parse_ai_response(ai_resp.raw_text)
            if scored.parse_succeeded and scored.score > 0:
                # Inline sharpness refinement (mirrors orchestrator). We
                # don't pull from the orchestrator helper because doing
                # so would trip a circular import at module load.
                scored.score = round(
                    cap_score_for_excellence(scored, scored.score),
                    1,
                )
        else:
            scored = ScoredFrame(parse_succeeded=False)
        candidate["scored_frame"] = scored
        candidate["ai_response"] = ai_resp
        label = normalize_position_label(getattr(scored, "position_label", "OTHER"))
        candidate["position_label"] = label
        candidate["position_label_confidence"] = round(float(getattr(scored, "position_confidence", 0.0) or 0.0), 3)
        candidate["genre_tags"] = list(getattr(scored, "genre_tags", []) or [])
        candidate["subgenre_tags"] = list(getattr(scored, "subgenre_tags", []) or [])
        candidate["sensitive_content_flags"] = list(getattr(scored, "sensitive_content_flags", []) or [])
        candidate["sensitive_content_confidence"] = round(
            float(getattr(scored, "sensitive_content_confidence", 0.0) or 0.0),
            3,
        )

        # Capture the response when scoring "succeeded" but produced
        # nothing the selector can use. This is the silent failure mode
        # that tanked Y_B_003: 49 successful AI calls, every one parsed
        # to score 0 or failed to parse. Without the raw text in the
        # decision log we have no way to diagnose the prompt regression.
        if ai_resp.success and (not scored.parse_succeeded or scored.score <= 0):
            reason = "parse_failed" if not scored.parse_succeeded else "score_zero"
            _capture_raw_ai_sample(candidate, ai_resp, scored, reason)
        elif debug_ai and ai_resp.success:
            _capture_raw_ai_sample(candidate, ai_resp, scored, "debug_ok")

        return candidate

    def _budget_exhausted() -> bool:
        if deadline_sec is not None and time.time() >= deadline_sec:
            return True
        if ai_call_cap > 0 and submitted >= ai_call_cap:
            return True
        return False

    def _emit_progress() -> None:
        # Linear interpolation between progress_low and progress_high
        # using "scored frames so far / target_k * heuristic" — we don't
        # know exact frame total without finishing the scan.
        if completed == 0:
            on_progress(progress_low)
            return
        # Scale: we expect ~10x cover_cap scored frames in a happy run.
        denom = max(1, cover_cap * 10)
        frac = min(1.0, completed / denom)
        on_progress(progress_low + int((progress_high - progress_low) * frac))

    pump_pulse_at = 0.0
    next_log_at = time.time() + 10.0

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="stream-ai") as executor:

        def _try_submit_more() -> None:
            nonlocal submitted
            while len(in_flight) < workers and not _budget_exhausted():
                try:
                    _, _, candidate = cand_q.get(timeout=0.05)
                except queue.Empty:
                    return
                if candidate is _SENTINEL:
                    # Producer is done. Don't requeue the sentinel; the
                    # outer loop will notice sieve_done + empty queue and
                    # break.
                    return
                fut = executor.submit(_score_one, candidate)
                in_flight[fut] = candidate
                submitted += 1

        # Initial fill so the H100 starts working as soon as the first
        # CV survivors arrive.
        while len(in_flight) < workers:
            if sieve_done.is_set() and cand_q.empty():
                break
            _try_submit_more()
            if not in_flight:
                # Sieve hasn't produced anything yet — yield briefly.
                time.sleep(0.05)
                if sieve_done.is_set() and cand_q.empty():
                    break

        while in_flight or not (sieve_done.is_set() and cand_q.empty()):
            if _budget_exhausted():
                aborted = True
                if deadline_sec is not None and time.time() >= deadline_sec:
                    abort_reason = "E_STREAM_DEADLINE"
                else:
                    abort_reason = "E_STREAM_AI_CAP"
                # Stop producer decode immediately when dispatcher budget
                # is exhausted (especially AI cap) instead of decoding until
                # deadline and then waiting for join timeout.
                stop_sieve.set()
                break

            done_set, _ = wait(in_flight, timeout=0.5, return_when=FIRST_COMPLETED)
            for fut in done_set:
                candidate = in_flight.pop(fut)
                completed += 1
                try:
                    result = fut.result()
                except Exception as exc:  # noqa: BLE001
                    ai_failures += 1
                    log.error("stream AI worker exception", error=str(exc))
                    candidate["scored_frame"] = ScoredFrame(parse_succeeded=False)
                    candidate["ai_response"] = AIResponse(
                        success=False,
                        error_code="E_AI_WORKER_EXCEPTION",
                        error_message=str(exc),
                    )
                    result = candidate
                ai_wall_total += float(result.get("ai_wall_sec") or 0.0)
                _scored = result.get("scored_frame")
                _ai_resp = result.get("ai_response")
                if _ai_resp is not None and getattr(_ai_resp, "success", False):
                    if _scored is None or not getattr(_scored, "parse_succeeded", False):
                        parse_failed_count += 1
                    elif (getattr(_scored, "score", 0) or 0) <= 0:
                        score_zero_count += 1
                all_scored.append(result)
                selector.add(result)

            now = time.time()
            if now >= next_log_at:
                stats = selector.stats()
                on_log(
                    f"[stream] submitted={submitted} completed={completed} "
                    f"pool={stats['scored_pool']} queued={cand_q.qsize()} "
                    f"in_flight={len(in_flight)}"
                )
                next_log_at = now + 10.0

            _try_submit_more()
            _emit_progress()

            # Pulse — yield briefly if everything is steady-state empty.
            if not done_set and not in_flight and sieve_done.is_set() and cand_q.empty():
                break
            if now - pump_pulse_at > 30.0:
                pump_pulse_at = now

        # Drain any in-flight whose deadline arrived mid-call. Bound the
        # per-future wait so a stuck Ollama call can't pin us forever.
        if in_flight:
            on_log(f"[stream] draining {len(in_flight)} in-flight after stop signal")
            drain_deadline = time.time() + 60.0
            for fut in list(in_flight.keys()):
                remaining = max(0.5, drain_deadline - time.time())
                try:
                    result = fut.result(timeout=remaining)
                    completed += 1
                    ai_wall_total += float(result.get("ai_wall_sec") or 0.0)
                    _scored = result.get("scored_frame")
                    _ai_resp = result.get("ai_response")
                    if _ai_resp is not None and getattr(_ai_resp, "success", False):
                        if _scored is None or not getattr(_scored, "parse_succeeded", False):
                            parse_failed_count += 1
                        elif (getattr(_scored, "score", 0) or 0) <= 0:
                            score_zero_count += 1
                    all_scored.append(result)
                    selector.add(result)
                except Exception as exc:  # noqa: BLE001
                    ai_failures += 1
                    log.warn("stream drain failed", error=str(exc))
                in_flight.pop(fut, None)

        # Anything left in the queue we never got to is "skipped".
        while True:
            try:
                _, _, candidate = cand_q.get_nowait()
            except queue.Empty:
                break
            if candidate is _SENTINEL:
                continue
            skipped += 1

    for sieve_thread in sieve_threads:
        sieve_thread.join(timeout=10.0)
    if sieve_error.get("exc") is not None and not aborted:
        # Sieve crashed mid-scan; we still kept whatever we managed to
        # score. Surface the cause so the operator can investigate.
        abort_reason = abort_reason or "E_STREAM_SIEVE_FAULT"
        aborted = True

    final = selector.finalize()

    # Round the wall-time accounting once so the decision log isn't
    # cluttered with float noise.
    sieve_stats["decode_wall_sec"] = round(sieve_stats["decode_wall_sec"], 2)
    sieve_stats["cv_wall_sec"] = round(sieve_stats["cv_wall_sec"], 2)
    for seg in sieve_stats.get("segment_stats", []):
        if isinstance(seg, dict):
            seg["decode_wall_sec"] = round(float(seg.get("decode_wall_sec") or 0.0), 2)
            seg["cv_wall_sec"] = round(float(seg.get("cv_wall_sec") or 0.0), 2)

    on_log(
        f"[stream] final: scored={completed} picks={len(final['picks'])} "
        f"sharpness_floor={final['sharpness_floor_used']:.0f} "
        f"gate_relaxed={final['gate_relaxed']}"
    )
    on_log(
        f"[stream] timing: decode_wall={sieve_stats['decode_wall_sec']:.1f}s "
        f"cv_wall={sieve_stats['cv_wall_sec']:.1f}s "
        f"ai_wall={ai_wall_total:.1f}s "
        f"parse_failed={parse_failed_count} score_zero={score_zero_count}"
    )

    return {
        "picks": final["picks"],
        "all_scored": all_scored,
        "frame_cache": cache,
        "sharpness_floor_used": final["sharpness_floor_used"],
        "gate_relaxed": final["gate_relaxed"],
        "aborted": aborted,
        "abort_reason": abort_reason,
        "stats": {
            "interval_sec": interval,
            "submitted": submitted,
            "completed": completed,
            "ai_failures": ai_failures,
            "parse_failed": parse_failed_count,
            "score_zero": score_zero_count,
            "ai_wall_sec": round(ai_wall_total, 2),
            "skipped": skipped,
            "wall_sec": round(time.time() - dispatcher_started, 2),
            "max_workers": workers,
            "queue_cap": queue_cap,
            "ai_call_cap": ai_call_cap,
            **sieve_stats,
        },
        "selector_stats": final["stats"],
        "frame_cache_stats": cache.stats(),
        "raw_ai_samples": raw_ai_samples,
    }
