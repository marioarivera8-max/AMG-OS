"""
Main pipeline — orchestrates processing of one scene.

This is the heart of v11. It runs the phases in order:
1. Inventory (read scene, detect studio, parse code/title)
2. Compliance (verify 2257 doc)
3. Metadata (ffprobe)
4. Calibration (per-source thresholds)
5. Tiered scan (1, 2, 3 with escalation)
6. Finish hunter
7. Buildup hunter
8. Cluster expansion
9. Floor enforcement (fallbacks A/B/C/D if needed)
10. Output (covers, contact sheet, decision log)

Time budget enforced throughout. Per-phase timeouts honored.
"""
import json
import time
from pathlib import Path
from typing import Optional, List, Tuple
from datetime import datetime

from amg.config import (
    TIME_BUDGET_HARD_PCT,
    TIME_BUDGET_ABSOLUTE_MAX,
    PHASE_HARD_TIMEOUT_SEC,
    COVER_FLOOR,
    get_cover_cap,
    BATCH_LOCK_FILE,
    MIN_FREE_SPACE_GB,
    REQUIRE_2257_DOC,
    SCORE_TIER_3_SUCCESS_FLOOR,
    TITLE_TONE_DEFAULT,
    SOFT_THUMB_ENABLED,
    PROCESSING_PROFILE,
    TIER_SCAN_MODE,
    ENABLE_FINISH_HUNTER,
    ENABLE_BUILDUP_HUNTER,
    ENABLE_CLUSTER_EXPANSION,
    ENABLE_POSITION_CLASSIFIER,
    ENABLE_SCENE_INSIGHT,
    ENABLE_PROVIDED_THUMBNAIL_SCORING,
    ENABLE_SCENE_ANALYSIS,
    ENABLE_ANALYSIS_OCR_POLICY,
    ANALYSIS_OCR_MAX_FRAMES,
    ENABLE_PREVIEW_GENERATION,
    PREVIEW_CLIP_COUNT,
    PREVIEW_CLIP_DURATION_SEC,
    PREVIEW_GIF_ENABLED,
    STREAMING_COMPACT_PROMPT_ENABLED,
    STREAMING_DEFER_CALIBRATION,
    STREAMING_EARLY_STOP_ENABLED,
    COVER_SUSPICIOUS_RECHECK_MAX,
    SOFT_THUMB_SAMPLE_COUNT,
    SOFT_THUMB_MIN_SCORE,
    SOFT_THUMB_FILENAME,
    STREAMING_SCAN_ENABLED,
    SENSITIVE_CONTENT_FLAG_MIN_CONF,
    normalize_position_label,
    normalize_sensitive_content_flag,
)
from amg.analysis.scene_analysis import (
    build_scene_analysis,
    run_ocr_policy_scan,
    write_scene_analysis as write_scene_analysis_sidecar,
)
from amg.ingest.inventory import find_companion_files, make_work_dir, make_covers_dir
from amg.ingest.studio_profiles import detect_studio, get_or_create_profile
from amg.ingest.folder_context import resolve_folder_context
from amg.ingest.performer_code import (
    parse_performer_code_with_context,
    get_authoritative_performer_count,
    detect_scene_type_from_code,
)
from amg.ingest.title_parser import parse_title_with_context, derive_primary_scene_type
from amg.video.metadata import get_metadata
from amg.video.frames import calibrate_thresholds
from amg.scoring.ai_client import AIClient
from amg.scoring.insight_pipeline import generate_scene_insight_payload
from amg.scoring.prompt import (
    build_scoring_prompt,
    build_streaming_scoring_prompt,
    SYSTEM_PROMPT,
)
from amg.scoring.position_classifier import classify_candidate_positions
from amg.scanning.tiered import run_tiered_scan
from amg.scanning.finish_hunter import run_finish_hunter
from amg.scanning.buildup_hunter import run_buildup_hunter
from amg.scanning.cluster import expand_clusters
from amg.scanning.fallback import run_floor_enforcement_cascade
from amg.scanning.stream import run_stream_scan
from amg.compliance.doc_2257 import verify_2257
from amg.compliance.audit_log import audit_event
from amg.output.covers import save_covers, score_and_save_provided_thumbnails
from amg.output.contact_sheet import build_contact_sheet
from amg.output.cover_validation import (
    apply_cover_validation,
    recheck_suspicious_selected,
    summarize_cover_validation,
)
from amg.output.decision_log import write_decision_log
from amg.output.previews import generate_preview_outputs
from amg.output.quota_fill import select_quota_fill, quota_satisfied, quota_progress
from amg.learning.recorder import record_scene_outcome
from amg.utils.timing import phase_timer, format_duration
from amg.utils.logging import get_logger, init_logging
from amg.__version__ import __version__

log = get_logger("pipeline")


def _sync_candidate_taxonomy(candidates: List[dict]) -> dict:
    """Copy parser taxonomy fields onto candidate dicts for selection/output."""
    synced = 0
    position_labeled = 0
    genre_labeled = 0
    for entry in candidates:
        scored = entry.get("scored_frame")
        if not scored:
            continue

        label = normalize_position_label(getattr(scored, "position_label", "OTHER"))
        conf = float(getattr(scored, "position_confidence", 0.0) or 0.0)
        if label and label != "OTHER":
            entry["position_label"] = label
            entry["position_label_confidence"] = round(conf, 3)
            position_labeled += 1
            synced += 1
        else:
            entry.setdefault("position_label", "OTHER")
            entry.setdefault("position_label_confidence", round(conf, 3))

        genres = list(getattr(scored, "genre_tags", []) or [])
        subgenres = list(getattr(scored, "subgenre_tags", []) or [])
        if genres:
            entry["genre_tags"] = genres
            genre_labeled += 1
            synced += 1
        else:
            entry.setdefault("genre_tags", [])
        if subgenres:
            entry["subgenre_tags"] = subgenres
            synced += 1
        else:
            entry.setdefault("subgenre_tags", [])

        flags = list(getattr(scored, "sensitive_content_flags", []) or [])
        conf = float(getattr(scored, "sensitive_content_confidence", 0.0) or 0.0)
        if flags:
            entry["sensitive_content_flags"] = flags
            entry["sensitive_content_confidence"] = round(conf, 3)
            synced += 1
        else:
            entry.setdefault("sensitive_content_flags", [])
            entry.setdefault("sensitive_content_confidence", round(conf, 3))

    return {
        "candidates_seen": len(candidates),
        "taxonomy_synced": synced,
        "position_labeled": position_labeled,
        "genre_labeled": genre_labeled,
    }


def _entry_sensitive_flags(entry: dict) -> tuple[list[str], float]:
    flags = list(entry.get("sensitive_content_flags") or [])
    conf = entry.get("sensitive_content_confidence")
    scored = entry.get("scored_frame")
    if not flags and scored is not None:
        flags = list(getattr(scored, "sensitive_content_flags", []) or [])
    flags = [normalize_sensitive_content_flag(flag) for flag in flags]
    flags = [flag for flag in flags if flag]
    if conf is None and scored is not None:
        conf = getattr(scored, "sensitive_content_confidence", 0.0)
    try:
        conf_f = max(0.0, min(1.0, float(conf or 0.0)))
    except (TypeError, ValueError):
        conf_f = 0.0
    return flags, conf_f


def _summarize_sensitive_content(entries: List[dict]) -> dict:
    seen = set()
    frames_checked = 0
    counts: dict[str, int] = {}
    max_conf: dict[str, float] = {}
    evidence = []

    for entry in entries or []:
        key = id(entry)
        if key in seen:
            continue
        seen.add(key)
        scored = entry.get("scored_frame")
        if scored is not None and not getattr(scored, "parse_succeeded", False):
            continue
        frames_checked += 1
        flags, conf = _entry_sensitive_flags(entry)
        strong_flags = [f for f in flags if conf >= SENSITIVE_CONTENT_FLAG_MIN_CONF]
        if not strong_flags:
            continue
        for flag in strong_flags:
            counts[flag] = counts.get(flag, 0) + 1
            max_conf[flag] = max(max_conf.get(flag, 0.0), conf)
        if len(evidence) < 12:
            evidence.append({
                "timestamp_sec": entry.get("timestamp_sec"),
                "flags": strong_flags,
                "confidence": round(conf, 3),
                "score": float(getattr(scored, "score", 0.0) or 0.0) if scored else 0.0,
                "type": getattr(scored, "type_", None) if scored else None,
            })

    flags = sorted(counts.keys())
    return {
        "enabled": True,
        "flagged": bool(flags),
        "flags": flags,
        "min_confidence": SENSITIVE_CONTENT_FLAG_MIN_CONF,
        "max_confidence": round(max(max_conf.values()), 3) if max_conf else 0.0,
        "max_confidence_by_flag": {k: round(v, 3) for k, v in sorted(max_conf.items())},
        "counts_by_flag": {k: counts[k] for k in sorted(counts)},
        "frames_checked": frames_checked,
        "evidence": evidence,
    }


def process_scene(
    video_path: Path,
    operator: Optional[str] = None,
    machine_id: Optional[str] = None,
    dry_run: bool = False,
    on_progress=None,
) -> dict:
    """
    Process one scene end-to-end.

    Args:
        on_progress: optional callable ``f(pct: int) -> None`` invoked at
            phase boundaries with a coarse 0..100 estimate of overall
            completion. Used by the cloud edition to drive a progress bar
            in the operator UI; local CLI runs leave it ``None``. Each
            call is wrapped so a hook raising never breaks the pipeline.

    Returns:
        {
            'success': bool,
            'scene_path': str,
            'scene_id': str,
            'covers_saved': int,
            'top_pick_score': float,
            'total_duration_sec': float,
            'error_codes': List[str],
            'warnings': List[str],
            'work_dir': Path,
            'decision_log_path': Path,
        }
    """
    video_path = Path(video_path).resolve()
    parent = (video_path.parent.name or "").strip()
    stem = (video_path.stem or "").strip()
    # Use a per-video id to avoid collisions when one folder has many scenes.
    if parent and stem and parent.lower() != stem.lower():
        scene_id = f"{parent}_{stem}"
    else:
        scene_id = stem or parent or video_path.name
    init_logging(run_log_name=scene_id)

    def _emit_progress(pct: int) -> None:
        if on_progress is None:
            return
        try:
            on_progress(int(max(0, min(100, pct))))
        except Exception as exc:  # noqa: BLE001 - hooks must never break the pipeline
            log.warn("on_progress hook raised; continuing", error=str(exc))

    log.info("=" * 64)
    log.info(f"AMG OS {__version__} — Processing Scene")
    log.info("=" * 64)
    log.info(f"Scene: {scene_id}")
    log.info(f"Path: {video_path}")
    log.info("Processing profile", profile=PROCESSING_PROFILE, tier_scan_mode=TIER_SCAN_MODE)
    if dry_run:
        log.info("DRY RUN MODE — no files will be saved")

    pipeline_start = time.time()
    error_codes = []
    warnings = []
    phase_results = {}

    # --- PHASE 1: INVENTORY ---
    folder_ctx = resolve_folder_context(video_path)
    if folder_ctx.is_generic_filename:
        log.info("[ingest] Generic scene filename detected — using folder-context fallback",
                 ancestors=folder_ctx.ancestor_names[:3],
                 source_folder=str(folder_ctx.source_folder) if folder_ctx.source_folder else None,
                 metadata_docs=len(folder_ctx.metadata_documents))
    studio_name = folder_ctx.studio or detect_studio(video_path)
    studio_profile = get_or_create_profile(studio_name) if studio_name else None
    code_info = parse_performer_code_with_context(video_path, folder_ctx)
    title_info = parse_title_with_context(video_path, folder_ctx)
    title_info["folder_performers"] = folder_ctx.performers
    title_info["folder_location"] = folder_ctx.location

    primary_type = derive_primary_scene_type(
        title_info.get("detected_genres", []),
        code_info.get("total") if code_info else None,
    )
    if code_info:
        scene_type_from_code = detect_scene_type_from_code(code_info)
        # Code-based type overrides genre-based for multi-performer scenes
        if scene_type_from_code != "STANDARD":
            primary_type = scene_type_from_code

    title_info["primary_scene_type"] = primary_type
    log.info("[ingest] Inventory complete",
             studio=studio_name,
             code=code_info.get("code") if code_info else None,
             scene_type=primary_type,
             genres=title_info.get("detected_genres", []),
             folder_context=str(folder_ctx.source_folder) if folder_ctx.source_folder else None)
    _emit_progress(5)

    # --- PHASE 2: COMPLIANCE ---
    compliance = verify_2257(video_path)
    if compliance["should_block"]:
        error_codes.append(compliance["error_code"])
        log.error("BLOCKED: 2257 compliance failure")
        audit_event("scene_blocked",
                    scene_id=scene_id,
                    details={"reason": "2257 missing", "path": str(video_path)})
        return _build_result(
            success=False,
            scene_path=video_path,
            scene_id=scene_id,
            covers_saved=0,
            error_codes=error_codes,
            warnings=warnings,
            total_duration_sec=time.time() - pipeline_start,
        )

    # --- PHASE 3: METADATA ---
    metadata = get_metadata(video_path)
    if metadata is None:
        error_codes.append("E_SOURCE_UNREADABLE")
        log.error("Cannot read video metadata")
        return _build_result(
            success=False, scene_path=video_path, scene_id=scene_id,
            covers_saved=0, error_codes=error_codes, warnings=warnings,
            total_duration_sec=time.time() - pipeline_start,
        )

    duration_sec = metadata["duration_sec"]
    if duration_sec < 60:
        error_codes.append("E_SOURCE_TOO_SHORT")
        log.error("Video too short", duration_sec=duration_sec)
        return _build_result(
            success=False, scene_path=video_path, scene_id=scene_id,
            covers_saved=0, error_codes=error_codes, warnings=warnings,
            total_duration_sec=time.time() - pipeline_start,
        )

    log.info("Video metadata",
             duration=format_duration(duration_sec),
             resolution=f"{metadata['width']}x{metadata['height']}",
             codec=metadata["codec"],
             size_gb=f"{metadata['size_gb']:.2f}")

    # Compute time budgets
    hard_budget = min(duration_sec * TIME_BUDGET_HARD_PCT, TIME_BUDGET_ABSOLUTE_MAX)
    deadline = pipeline_start + hard_budget
    log.info(f"Time budget: {format_duration(hard_budget)} hard cap")
    _emit_progress(10)

    # --- PHASE 4: CALIBRATION ---
    calibration = {
        "tier_1_floor": 0.0,
        "tier_2_floor": 0.0,
        "tier_3_floor": 0.0,
        "sharpness_samples": [],
        "samples_collected": 0,
        "no_variation": False,
        "all_blurry": False,
        "skipped": True,
    }

    def _ensure_calibration(reason: str = "initial") -> dict:
        nonlocal calibration
        if calibration and not calibration.get("skipped"):
            return calibration
        with phase_timer("calibration", PHASE_HARD_TIMEOUT_SEC["calibration"]) as t_cal:
            calibration = calibrate_thresholds(video_path, duration_sec)
        phase_results["calibration"] = {
            "duration_sec": t_cal.elapsed,
            "tier_1_floor": calibration["tier_1_floor"],
            "deferred": reason != "initial",
            "reason": reason,
        }
        log.info("Calibration complete",
                 tier_1=int(calibration["tier_1_floor"]),
                 tier_2=int(calibration["tier_2_floor"]),
                 tier_3=int(calibration["tier_3_floor"]),
                 samples=calibration["samples_collected"],
                 deferred=reason != "initial")
        if calibration.get("all_blurry") and "E_CALIB_ALL_BLURRY" not in error_codes:
            warnings.append("Source appears entirely blurry")
            error_codes.append("E_CALIB_ALL_BLURRY")
        return calibration

    if STREAMING_SCAN_ENABLED and STREAMING_DEFER_CALIBRATION:
        phase_results["calibration"] = {
            "duration_sec": 0.0,
            "skipped": True,
            "reason": "streaming_deferred",
        }
        log.info("Calibration deferred for streaming scan")
    else:
        _ensure_calibration("initial")
    _emit_progress(20)

    # Verify Ollama is alive before scoring
    ai_client = AIClient()
    if not ai_client.is_alive():
        error_codes.append("E_AI_UNAVAILABLE")
        log.error("Ollama service not responding")
        return _build_result(
            success=False, scene_path=video_path, scene_id=scene_id,
            covers_saved=0, error_codes=error_codes, warnings=warnings,
            total_duration_sec=time.time() - pipeline_start,
        )

    # Build scoring prompt with all context
    studio_lang = studio_profile.get("primary_language", "en") if studio_profile else "en"
    studio_hint = f"{studio_name} — {studio_profile.get('display_name', '')}" if studio_profile else None

    prompt = build_scoring_prompt(
        primary_scene_type=primary_type,
        detected_genres=title_info.get("detected_genres", []),
        performer_count=code_info.get("total") if code_info else None,
        studio_language=studio_lang,
        studio_hint=studio_hint,
    )

    # --- STREAMING SCAN BRANCH (replaces phases 5-8 when enabled) ---
    # When AMG_STREAMING_SCAN=1 the producer/consumer scan in
    # ``amg.scanning.stream`` runs in place of the classic
    # tier_scan + finish/buildup/cluster chain. It keeps the GPU
    # saturated through one decode pass and applies a post-AI sharpness
    # gate so blurry frames the AI loved can't ship as covers — the
    # FALLBACK-C failure mode the 2026-05-08 audit surfaced.
    #
    # Phase 9 (floor_enforcement) still runs as a safety net if the
    # stream produced fewer than COVER_FLOOR picks. The classic path
    # below is skipped entirely.
    frame_cache = None
    streaming_ran = False
    candidates: List[dict] = []
    all_scored: List[dict] = []
    fallbacks_used: List[str] = []
    stream_ai_unreliable = False

    if STREAMING_SCAN_ENABLED:
        streaming_ran = True
        if time.time() > deadline:
            error_codes.append("E_TIMEOUT_HARD")
            return _abort_with_partial(
                video_path, scene_id, error_codes, warnings,
                pipeline_start, phase_results, [],
                metadata, studio_profile, code_info, title_info, calibration,
                operator, machine_id, dry_run,
            )

        cover_cap_hint = max(get_cover_cap(duration_sec), COVER_FLOOR)
        stream_prompt = prompt
        if STREAMING_COMPACT_PROMPT_ENABLED:
            stream_prompt = build_streaming_scoring_prompt(
                primary_scene_type=primary_type,
                detected_genres=title_info.get("detected_genres", []),
                performer_count=code_info.get("total") if code_info else None,
                studio_language=studio_lang,
                studio_hint=studio_hint,
            )

        with phase_timer("stream_scan") as t:
            stream_result = run_stream_scan(
                video_path,
                duration_sec,
                stream_prompt,
                target_count=COVER_FLOOR,
                cover_cap=cover_cap_hint,
                deadline_sec=deadline,
                system_prompt=SYSTEM_PROMPT,
                ai_client=ai_client,
                on_log=lambda msg: log.info(msg),
                on_progress=lambda pct: _emit_progress(pct),
            )
        phase_results["stream_scan"] = {
            "duration_sec": t.elapsed,
            "candidates_found": len(stream_result.get("picks", [])),
            "passing_count": len(stream_result.get("picks", [])),
            "frames_scored": stream_result["stats"].get("completed", 0),
            "submitted": stream_result["stats"].get("submitted", 0),
            "completed": stream_result["stats"].get("completed", 0),
            "skipped": stream_result["stats"].get("skipped", 0),
            "ai_failures": stream_result["stats"].get("ai_failures", 0),
            "ai_response_failures": stream_result["stats"].get("ai_response_failures", 0),
            "ai_failure_rate": stream_result["stats"].get("ai_failure_rate", 0.0),
            "ai_error_counts": stream_result["stats"].get("ai_error_counts", {}),
            "ai_error_samples": stream_result["stats"].get("ai_error_samples", []),
            # New (2026-05-09): wall-time split between CPU video decode,
            # CV ops, and AI scoring inside the stream phase. The
            # decode-vs-AI ratio is what tells us whether NVDEC will
            # actually move the needle on the next iteration.
            "decode_wall_sec": stream_result["stats"].get("decode_wall_sec", 0.0),
            "cv_wall_sec": stream_result["stats"].get("cv_wall_sec", 0.0),
            "ai_wall_sec": stream_result["stats"].get("ai_wall_sec", 0.0),
            # New: silent-failure counters. parse_failed/score_zero
            # explain why selector_pool can be 0 even when 'completed'
            # is high — i.e. the AI returned successful HTTP responses
            # but the rubric prompt produced unparseable / zero-score
            # output. The classic stream_scan stats hid this case.
            "parse_failed": stream_result["stats"].get("parse_failed", 0),
            "score_zero": stream_result["stats"].get("score_zero", 0),
            "selector_parse_failed": stream_result["selector_stats"].get("parse_failed", 0),
            "selector_score_zero": stream_result["selector_stats"].get("score_zero", 0),
            "frames_seen": stream_result["stats"].get("frames_seen", 0),
            "candidates_emitted": stream_result["stats"].get("candidates_emitted", 0),
            "queue_overflows": stream_result["stats"].get("queue_overflows", 0),
            "interval_sec": stream_result["stats"].get("interval_sec", 0.0),
            "max_workers": stream_result["stats"].get("max_workers", 0),
            "segment_count": stream_result["stats"].get("segment_count", 1),
            "segment_stats": stream_result["stats"].get("segment_stats", []),
            "low_res_analysis": stream_result["stats"].get("low_res_analysis", False),
            "analysis_frame_size": stream_result["stats"].get("analysis_frame_size"),
            "full_res_cached": stream_result["stats"].get("full_res_cached", True),
            "video_backend": stream_result["stats"].get("video_backend"),
            "gpu_cv_mode": stream_result["stats"].get("gpu_cv_mode"),
            "gpu_cv_backend": stream_result["stats"].get("gpu_cv_backend"),
            "gpu_cv_reason": stream_result["stats"].get("gpu_cv_reason"),
            "selector_pool": stream_result["selector_stats"].get("scored_pool", 0),
            "sharpness_floor_used": stream_result.get("sharpness_floor_used", 0.0),
            "gate_relaxed": stream_result.get("gate_relaxed", False),
            "validation_early_stop_enabled": bool(STREAMING_EARLY_STOP_ENABLED),
            "early_stop_applied": bool(stream_result["stats"].get("early_stopped", False)),
            "early_stop_reason": stream_result["stats"].get("early_stop_reason"),
            "compact_prompt": bool(STREAMING_COMPACT_PROMPT_ENABLED),
            "ai_failure_early_stop_enabled": stream_result["stats"].get("ai_failure_early_stop_enabled", False),
            "ai_failure_early_stop_min_completed": stream_result["stats"].get("ai_failure_early_stop_min_completed"),
            "ai_failure_early_stop_rate": stream_result["stats"].get("ai_failure_early_stop_rate"),
            # NOTE: this snapshot is taken at end-of-scan; we re-snap
            # after save_covers() below so the operator can see the
            # actual hit-rate the output phase achieved.
            "frame_cache_stats": stream_result.get("frame_cache_stats", {}),
            # Bounded sample of raw AI responses for parse-failure
            # diagnosis. Capped at ~8 entries (≤ ~12 KB) so the
            # decision log stays small.
            "raw_ai_samples": stream_result.get("raw_ai_samples", []),
            "aborted": stream_result.get("aborted", False),
            "abort_reason": stream_result.get("abort_reason"),
            "tier_used": "stream",
        }
        if stream_result.get("aborted"):
            error_codes.append(stream_result.get("abort_reason", "E_STREAM_DEADLINE"))

        candidates = list(stream_result["picks"])
        all_scored = list(stream_result["all_scored"])
        frame_cache = stream_result.get("frame_cache")

        for skipped_phase in ("tier_scan", "finish_hunter", "buildup_hunter", "cluster"):
            phase_results.setdefault(skipped_phase, {
                "duration_sec": 0.0,
                "skipped": True,
                "reason": "streaming_scan_active",
            })

        if len(candidates) < COVER_FLOOR:
            stream_ai_unreliable = (
                stream_result.get("abort_reason") == "E_STREAM_AI_UNRELIABLE"
                or (
                    int(stream_result["stats"].get("completed", 0) or 0) >= int(
                        stream_result["stats"].get("ai_failure_early_stop_min_completed", 12) or 12
                    )
                    and int(stream_result["selector_stats"].get("scored_pool", 0) or 0) == 0
                    and float(stream_result["stats"].get("ai_failure_rate", 0.0) or 0.0) >= float(
                        stream_result["stats"].get("ai_failure_early_stop_rate", 0.75) or 0.75
                    )
                )
            )
            log.warn(
                f"Streaming scan produced {len(candidates)} < COVER_FLOOR={COVER_FLOOR}; "
                "running fallback cascade"
            )
            with phase_timer("floor_enforcement") as t:
                if stream_ai_unreliable:
                    log.warn(
                        "Skipping AI fallback C because streaming AI responses were unreliable"
                    )
                    cascade_calibration = calibration
                else:
                    calibration = _ensure_calibration("streaming_floor_enforcement")
                    cascade_calibration = calibration
                cascade_result = run_floor_enforcement_cascade(
                    video_path, duration_sec, candidates, all_scored, cascade_calibration,
                    target_count=COVER_FLOOR, deadline_sec=deadline,
                    ai_unreliable=stream_ai_unreliable,
                )
            phase_results["floor_enforcement"] = {
                "duration_sec": t.elapsed,
                "fallbacks_used": cascade_result["fallbacks_used"],
                "ai_unreliable": stream_ai_unreliable,
                "ai_fallback_skipped": cascade_result.get("ai_fallback_skipped", False),
                "skip_reason": cascade_result.get("skip_reason"),
                "floor_met": cascade_result["floor_met"],
                "aborted": cascade_result.get("aborted", False),
                "deadline_overrun": cascade_result.get("deadline_overrun", False),
                "final_count": len(cascade_result["final_candidates"]),
            }
            candidates = cascade_result["final_candidates"]
            fallbacks_used = cascade_result["fallbacks_used"]
            if "D" in fallbacks_used:
                warnings.append("Fallback D used (pure CV rescue)")
                error_codes.append("E_FLOOR_FALLBACK_D")
            if not cascade_result["floor_met"]:
                error_codes.append("E_FLOOR_NOT_MET")
        _emit_progress(85)

    # --- CLASSIC PHASES 5-9 (skipped when streaming branch is active) ---
    if not streaming_ran:
        # --- PHASE 5: TIERED SCAN ---
        if time.time() > deadline:
            error_codes.append("E_TIMEOUT_HARD")
            return _abort_with_partial(
                video_path, scene_id, error_codes, warnings,
                pipeline_start, phase_results, [],
                metadata, studio_profile, code_info, title_info, calibration,
                operator, machine_id, dry_run,
            )

        with phase_timer("tier_scan") as t:
            tier_result = run_tiered_scan(
                video_path, duration_sec, calibration, prompt,
                system_prompt=SYSTEM_PROMPT, deadline_sec=deadline,
            )
        tier_stats = tier_result.get("tier_stats") if isinstance(tier_result.get("tier_stats"), dict) else {}
        frames_extracted = sum(
            int((v or {}).get("frames_extracted", 0) or 0)
            for v in tier_stats.values()
            if isinstance(v, dict)
        )
        ai_scored = sum(
            int((v or {}).get("ai_scored_count", 0) or 0)
            for v in tier_stats.values()
            if isinstance(v, dict)
        )
        phase_results["tier_scan"] = {
            "duration_sec": t.elapsed,
            "tier_used": tier_result["tier_used"],
            "candidates_found": len(tier_result["candidates"]),
            "frames_scored": len(tier_result["all_scored"]),
            "passing_count": len(tier_result["candidates"]),
            "aborted": tier_result.get("aborted", False),
            "abort_reason": tier_result.get("abort_reason"),
            "frames_extracted": frames_extracted,
            "ai_scored_count": ai_scored,
            "tier_breakdown": tier_stats,
            "scan_mode": tier_result.get("scan_mode", TIER_SCAN_MODE),
            "processing_profile": PROCESSING_PROFILE,
        }
        if tier_result.get("aborted"):
            error_codes.append(tier_result.get("abort_reason", "E_TIMEOUT_HARD"))

        all_scored = list(tier_result["all_scored"])
        candidates = list(tier_result["candidates"])
        _emit_progress(45)

        # --- PHASE 6: FINISH HUNTER ---
        if ENABLE_FINISH_HUNTER and time.time() < deadline and not quota_satisfied(candidates):
            with phase_timer("finish_hunter") as t:
                finish_result = run_finish_hunter(
                    video_path, duration_sec, calibration, prompt,
                    system_prompt=SYSTEM_PROMPT, deadline_sec=deadline,
                )
            phase_results["finish_hunter"] = {
                "duration_sec": t.elapsed,
                "candidates_found": len(finish_result["candidates"]),
                "passing_count": len(finish_result["candidates"]),
                "aborted": finish_result.get("aborted", False),
                "abort_reason": finish_result.get("abort_reason"),
                "ai_submitted_count": finish_result.get("ai_submitted_count", 0),
                "ai_completed_count": finish_result.get("ai_completed_count", 0),
                "ai_skipped_count": finish_result.get("ai_skipped_count", 0),
                "ai_batch_wall_sec": finish_result.get("ai_batch_wall_sec", 0.0),
            }
            all_scored.extend(finish_result.get("all_scored", []))
            candidates.extend(finish_result["candidates"])
        elif time.time() < deadline:
            phase_results["finish_hunter"] = {
                "duration_sec": 0.0,
                "skipped": True,
                "reason": "disabled" if not ENABLE_FINISH_HUNTER else "quota_satisfied",
                "quota_progress": quota_progress(candidates),
            }
        _emit_progress(60)

        # --- PHASE 7: BUILDUP HUNTER ---
        if ENABLE_BUILDUP_HUNTER and time.time() < deadline and not quota_satisfied(candidates):
            with phase_timer("buildup_hunter") as t:
                buildup_result = run_buildup_hunter(
                    video_path, duration_sec, calibration, prompt,
                    system_prompt=SYSTEM_PROMPT, deadline_sec=deadline,
                )
            phase_results["buildup_hunter"] = {
                "duration_sec": t.elapsed,
                "candidates_found": len(buildup_result["candidates"]),
                "passing_count": len(buildup_result["candidates"]),
                "aborted": buildup_result.get("aborted", False),
                "abort_reason": buildup_result.get("abort_reason"),
                "ai_submitted_count": buildup_result.get("ai_submitted_count", 0),
                "ai_completed_count": buildup_result.get("ai_completed_count", 0),
                "ai_skipped_count": buildup_result.get("ai_skipped_count", 0),
                "ai_batch_wall_sec": buildup_result.get("ai_batch_wall_sec", 0.0),
            }
            all_scored.extend(buildup_result.get("all_scored", []))
            candidates.extend(buildup_result["candidates"])
        elif time.time() < deadline:
            phase_results["buildup_hunter"] = {
                "duration_sec": 0.0,
                "skipped": True,
                "reason": "disabled" if not ENABLE_BUILDUP_HUNTER else "quota_satisfied",
                "quota_progress": quota_progress(candidates),
            }
        _emit_progress(72)

        # --- PHASE 8: CLUSTER EXPANSION ---
        if ENABLE_CLUSTER_EXPANSION and time.time() < deadline and candidates and not quota_satisfied(candidates):
            seen_ts = {round(c["timestamp_sec"], 1) for c in candidates}
            with phase_timer("cluster") as t:
                cluster_result = expand_clusters(
                    video_path, duration_sec, candidates, calibration, prompt,
                    system_prompt=SYSTEM_PROMPT, deadline_sec=deadline,
                    already_seen_timestamps=seen_ts,
                )
            phase_results["cluster"] = {
                "duration_sec": t.elapsed,
                "expansions": cluster_result.get("expansions_count", 0),
                "candidates_found": len([c for c in cluster_result["cluster_candidates"]
                                         if c.get("scored_frame")
                                         and c["scored_frame"].score >= SCORE_TIER_3_SUCCESS_FLOOR]),
                "aborted": cluster_result.get("aborted", False),
                "abort_reason": cluster_result.get("abort_reason"),
                "ai_submitted_count": cluster_result.get("ai_submitted_count", 0),
                "ai_completed_count": cluster_result.get("ai_completed_count", 0),
                "ai_skipped_count": cluster_result.get("ai_skipped_count", 0),
                "ai_batch_wall_sec": cluster_result.get("ai_batch_wall_sec", 0.0),
            }
            for c in cluster_result["cluster_candidates"]:
                scored = c.get("scored_frame")
                if scored and scored.parse_succeeded and scored.score >= SCORE_TIER_3_SUCCESS_FLOOR:
                    candidates.append(c)
                all_scored.append(c)
        elif time.time() < deadline and candidates:
            phase_results["cluster"] = {
                "duration_sec": 0.0,
                "skipped": True,
                "reason": "disabled" if not ENABLE_CLUSTER_EXPANSION else "quota_satisfied",
                "quota_progress": quota_progress(candidates),
            }

        # --- PHASE 9: FLOOR ENFORCEMENT ---
        if len(candidates) < COVER_FLOOR:
            if time.time() >= deadline:
                log.warn(
                    f"Floor not met ({len(candidates)} < {COVER_FLOOR}) after deadline, "
                    "running emergency cascade"
                )
            else:
                log.warn(f"Floor not met ({len(candidates)} < {COVER_FLOOR}), running cascade")
            with phase_timer("floor_enforcement") as t:
                cascade_result = run_floor_enforcement_cascade(
                    video_path, duration_sec, candidates, all_scored, calibration,
                    target_count=COVER_FLOOR, deadline_sec=deadline,
                )
            phase_results["floor_enforcement"] = {
                "duration_sec": t.elapsed,
                "fallbacks_used": cascade_result["fallbacks_used"],
                "floor_met": cascade_result["floor_met"],
                "aborted": cascade_result.get("aborted", False),
                "deadline_overrun": cascade_result.get("deadline_overrun", False),
                "final_count": len(cascade_result["final_candidates"]),
            }
            candidates = cascade_result["final_candidates"]
            fallbacks_used = cascade_result["fallbacks_used"]
            if "D" in fallbacks_used:
                warnings.append("Fallback D used (pure CV rescue)")
                error_codes.append("E_FLOOR_FALLBACK_D")
            if not cascade_result["floor_met"]:
                error_codes.append("E_FLOOR_NOT_MET")
        _emit_progress(85)

    # Apply adaptive cover cap (review-burden control).
    cover_cap = get_cover_cap(duration_sec)
    if cover_cap < COVER_FLOOR:
        cover_cap = COVER_FLOOR

    taxonomy_stats = _sync_candidate_taxonomy(candidates)
    phase_results["taxonomy_metadata"] = taxonomy_stats
    content_flag_stats = _summarize_sensitive_content(list(all_scored or []) + list(candidates or []))
    phase_results["content_flags"] = content_flag_stats
    if content_flag_stats.get("flagged"):
        flagged = ", ".join(content_flag_stats.get("flags", []))
        warnings.append(f"Sensitive content flagged for review: {flagged}")

    validation_pool = list(candidates)
    validation_stats = apply_cover_validation(validation_pool)
    eligible_candidates = [
        c for c in validation_pool
        if isinstance(c.get("cover_validation"), dict)
        and c["cover_validation"].get("eligible")
    ]
    rejected_count = len(validation_pool) - len(eligible_candidates)
    phase_results["cover_validation"] = {
        **validation_stats,
        "pre_quota_candidates": len(validation_pool),
        "pre_quota_eligible": len(eligible_candidates),
        "pre_quota_rejected": rejected_count,
    }
    if rejected_count:
        warnings.append(f"Cover validation rejected {rejected_count} invalid candidate(s)")
        log.warn(
            "Cover validation removed invalid candidates",
            before=len(validation_pool),
            eligible=len(eligible_candidates),
            rejected=rejected_count,
        )
    if len(eligible_candidates) < COVER_FLOOR:
        warnings.append(
            f"Cover validation left {len(eligible_candidates)}/{COVER_FLOOR} eligible candidates before quota-fill"
        )

    # Position classifier pass (bounded): attach `position_label` to top
    # position-like candidates so quota-fill can target 3-per-position.
    if ENABLE_POSITION_CLASSIFIER and eligible_candidates and time.time() < deadline and not stream_ai_unreliable:
        with phase_timer("position_classifier") as t:
            pos_stats = classify_candidate_positions(eligible_candidates, ai_client=ai_client)
        phase_results["position_classifier"] = {"duration_sec": t.elapsed, **pos_stats}
    elif eligible_candidates:
        phase_results["position_classifier"] = {
            "duration_sec": 0.0,
            "skipped": True,
            "reason": (
                "disabled"
                if not ENABLE_POSITION_CLASSIFIER
                else ("stream_ai_unreliable" if stream_ai_unreliable else "deadline")
            ),
        }

    # v11.1.5+: quota-fill selection. This is a v0 version that uses existing
    # scoring metadata (tier + TYPE) and keeps a minimum time gap between picks.
    # It is intentionally conservative: if we can't fill the target buckets,
    # we top off by score to at least meet COVER_FLOOR.
    before_select = len(eligible_candidates)
    candidates, quota_stats = select_quota_fill(
        eligible_candidates,
        max_total=cover_cap,
        min_total=COVER_FLOOR,
    )
    pre_recheck_selected = len(candidates)
    recheck_totals = {
        "enabled": False,
        "attempted": 0,
        "rejected": 0,
        "errors": 0,
        "passes": 0,
        "skipped_reason": "stream_ai_unreliable" if stream_ai_unreliable else None,
    }
    max_recheck_passes = 2
    if not stream_ai_unreliable:
        for _pass_idx in range(max_recheck_passes):
            remaining_rechecks = max(0, int(COVER_SUSPICIOUS_RECHECK_MAX) - recheck_totals["attempted"])
            if remaining_rechecks <= 0:
                break
            recheck_stats = recheck_suspicious_selected(
                candidates,
                ai_client=ai_client,
                system_prompt=SYSTEM_PROMPT,
                max_rechecks=remaining_rechecks,
            )
            recheck_totals["enabled"] = bool(recheck_stats.get("enabled"))
            recheck_totals["attempted"] += int(recheck_stats.get("attempted", 0) or 0)
            recheck_totals["rejected"] += int(recheck_stats.get("rejected", 0) or 0)
            recheck_totals["errors"] += int(recheck_stats.get("errors", 0) or 0)
            if int(recheck_stats.get("attempted", 0) or 0) > 0:
                recheck_totals["passes"] += 1
            if int(recheck_stats.get("rejected", 0) or 0) <= 0:
                break
            eligible_candidates = [
                c for c in eligible_candidates
                if isinstance(c.get("cover_validation"), dict)
                and c["cover_validation"].get("eligible")
            ]
            candidates, quota_stats = select_quota_fill(
                eligible_candidates,
                max_total=cover_cap,
                min_total=COVER_FLOOR,
            )
    phase_results["cover_validation"].update(summarize_cover_validation(validation_pool))
    phase_results["cover_validation"]["recheck"] = recheck_totals
    phase_results["quota_fill"] = {
        "before": before_select,
        **quota_stats,
        "pre_recheck_selected": pre_recheck_selected,
        "post_recheck_selected": len(candidates),
        "recheck_rejected": recheck_totals["rejected"],
        "topoff_after_recheck": max(0, len(candidates) - (pre_recheck_selected - recheck_totals["rejected"])),
        "validation_rejected": rejected_count,
    }
    if before_select != len(candidates):
        log.info("Quota-fill selected covers", before=before_select, after=len(candidates), cap=cover_cap)

    # --- PHASE 10: OUTPUT ---
    work_dir = make_work_dir(video_path, version="v11")
    covers_dir = make_covers_dir(work_dir)

    saved_covers = []
    contact_sheet_path = None
    provided_thumb_stats = None

    if not dry_run:
        with phase_timer("output") as t:
            performer_name = (studio_profile or {}).get("performers", {}).get("regular", ["Unknown"])
            performer_name = performer_name[0] if performer_name else "Unknown"

            saved_covers = save_covers(
                candidates, video_path, covers_dir,
                performer_name=performer_name,
                performer_code=code_info.get("code", "") if code_info else "",
                frame_cache=frame_cache,
            )

            # Optional: if creator/agency supplied thumbnails in the scene folder,
            # score them with AI and import only strong ones.
            if ENABLE_PROVIDED_THUMBNAIL_SCORING:
                imported_from_provided, provided_thumb_stats = score_and_save_provided_thumbnails(
                    video_path=video_path,
                    output_dir=covers_dir,
                    ai_client=ai_client,
                    search_root=folder_ctx.source_folder or video_path.parent,
                    performer_name=performer_name,
                    performer_code=code_info.get("code", "") if code_info else "",
                    rank_start=len(saved_covers) + 1,
                    cover_cap=cover_cap,
                )
            else:
                imported_from_provided, provided_thumb_stats = ([], {"skipped": True, "reason": "disabled"})
            if imported_from_provided:
                saved_covers.extend(imported_from_provided)
                log.info(
                    "[provided_thumbs] imported",
                    imported=len(imported_from_provided),
                    total_after=len(saved_covers),
                )

            # Persist full grading results for audit/review.
            if provided_thumb_stats:
                try:
                    with open(work_dir / "provided_thumbnails.json", "w") as f:
                        json.dump(provided_thumb_stats, f, indent=2)
                except Exception as e:
                    log.warn(f"[provided_thumbs] could not write grading file: {e}")

            # Contact sheet
            if saved_covers:
                contact_sheet_path = work_dir / f"00_{studio_name or 'Unknown'}_contact_sheet.jpg"
                quality_flag = "GOOD"
                if "D" in fallbacks_used:
                    quality_flag = "REVIEW_NEEDED"
                elif "C" in fallbacks_used:
                    quality_flag = "AI_GENERATED"

                build_contact_sheet(
                    contact_sheet_path,
                    [c["path"] for c in saved_covers],
                    scene_info={
                        "studio": studio_name or "Unknown",
                        "performers": performer_name,
                        "scene_type": primary_type,
                        "genres": title_info.get("detected_genres", []),
                        "cover_count": len(saved_covers),
                        "tier_used": phase_results.get("tier_scan", {}).get("tier_used", "?"),
                        "version": __version__,
                    },
                    quality_flag=quality_flag,
                )
        # Re-snap the frame cache stats AFTER save_covers so we can see
        # the actual hit-rate the output phase achieved. The pre-output
        # snapshot on stream_scan only shows what the producer cached;
        # the meaningful number is whether save_covers consumed it
        # instead of re-decoding the video.
        post_output_cache_stats = None
        if frame_cache is not None:
            try:
                post_output_cache_stats = frame_cache.stats()
            except Exception:  # noqa: BLE001
                post_output_cache_stats = None

        phase_results["output"] = {
            "duration_sec": t.elapsed,
            "provided_thumbnails_discovered": (provided_thumb_stats or {}).get("discovered", 0),
            "provided_thumbnails_scanned": (provided_thumb_stats or {}).get("scanned", 0),
            "provided_thumbnails_accepted": (provided_thumb_stats or {}).get("accepted", 0),
            "provided_thumbnails_imported": (provided_thumb_stats or {}).get("imported", 0),
            "frame_cache_stats_after_save": post_output_cache_stats,
        }
    _emit_progress(92)

    # --- PHASE 10b: CANONICAL SCENE ANALYSIS SIDECAR ---
    analysis_path = None
    analysis_summary = {}
    analysis_ocr_results: List[dict] = []
    analysis_policy_flags: List[dict] = []
    preview_outputs: List[dict] = []
    analysis_payload = None

    if not dry_run and ENABLE_SCENE_ANALYSIS and work_dir:
        try:
            with phase_timer("scene_analysis") as t_analysis:
                analysis_payload = build_scene_analysis(
                    scene_id=scene_id,
                    video_path=video_path,
                    metadata=metadata,
                    phase_results=phase_results,
                    all_scored=all_scored,
                    candidates=candidates,
                    saved_covers=saved_covers,
                )
                analysis_path, analysis_summary = write_scene_analysis_sidecar(
                    work_dir=work_dir,
                    scene_id=scene_id,
                    video_path=video_path,
                    metadata=metadata,
                    phase_results=phase_results,
                    all_scored=all_scored,
                    candidates=candidates,
                    saved_covers=saved_covers,
                )
            phase_results["scene_analysis"] = {
                "duration_sec": t_analysis.elapsed,
                "written": bool(analysis_path),
                "path": str(analysis_path) if analysis_path else None,
                "sections_count": analysis_summary.get("sections_count", 0),
                "evidence_count": analysis_summary.get("evidence_count", 0),
            }
            if analysis_path is None:
                warnings.append("Scene analysis sidecar write failed")
        except Exception as e:  # noqa: BLE001
            log.warn(f"[scene_analysis] failed: {e}")
            phase_results["scene_analysis"] = {"duration_sec": 0.0, "error": str(e)}

        if analysis_payload and ENABLE_ANALYSIS_OCR_POLICY and ANALYSIS_OCR_MAX_FRAMES > 0:
            try:
                with phase_timer("ocr_policy") as t_ocr:
                    ocr_payload = run_ocr_policy_scan(
                        video_path=video_path,
                        analysis=analysis_payload,
                        ai_client=ai_client,
                        max_frames=ANALYSIS_OCR_MAX_FRAMES,
                        system_prompt=SYSTEM_PROMPT,
                    )
                analysis_ocr_results = list(ocr_payload.get("ocr_results", []) or [])
                analysis_policy_flags = list(ocr_payload.get("policy_flags", []) or [])
                phase_results["ocr_policy"] = {
                    "duration_sec": t_ocr.elapsed,
                    "frames_scanned": ocr_payload.get("frames_scanned", 0),
                    "ocr_results": len(analysis_ocr_results),
                    "policy_flags": len(analysis_policy_flags),
                    "skipped": bool(ocr_payload.get("skipped", False)),
                    "reason": ocr_payload.get("reason"),
                    "error": ocr_payload.get("error"),
                }
            except Exception as e:  # noqa: BLE001
                log.warn(f"[ocr_policy] failed: {e}")
                phase_results["ocr_policy"] = {"duration_sec": 0.0, "error": str(e)}
        elif analysis_payload:
            phase_results["ocr_policy"] = {
                "duration_sec": 0.0,
                "skipped": True,
                "reason": "disabled",
            }

        if analysis_payload:
            next_analysis_path, analysis_summary = write_scene_analysis_sidecar(
                work_dir=work_dir,
                scene_id=scene_id,
                video_path=video_path,
                metadata=metadata,
                phase_results=phase_results,
                all_scored=all_scored,
                candidates=candidates,
                saved_covers=saved_covers,
                ocr_results=analysis_ocr_results,
                policy_flags=analysis_policy_flags,
            )
            if next_analysis_path is not None:
                analysis_path = next_analysis_path
            analysis_payload = build_scene_analysis(
                scene_id=scene_id,
                video_path=video_path,
                metadata=metadata,
                phase_results=phase_results,
                all_scored=all_scored,
                candidates=candidates,
                saved_covers=saved_covers,
                ocr_results=analysis_ocr_results,
                policy_flags=analysis_policy_flags,
            )

        if analysis_payload and ENABLE_PREVIEW_GENERATION and PREVIEW_CLIP_COUNT > 0:
            try:
                with phase_timer("preview_generation") as t_preview:
                    preview_manifest = generate_preview_outputs(
                        video_path=video_path,
                        work_dir=work_dir,
                        analysis=analysis_payload,
                        clip_count=PREVIEW_CLIP_COUNT,
                        clip_duration_sec=PREVIEW_CLIP_DURATION_SEC,
                        gif_enabled=PREVIEW_GIF_ENABLED,
                    )
                preview_outputs = list(preview_manifest.get("outputs", []) or [])
                phase_results["preview_generation"] = {
                    "duration_sec": t_preview.elapsed,
                    "outputs": len([o for o in preview_outputs if o.get("status") == "ok"]),
                    "attempted": len(preview_outputs),
                    "errors": len(preview_manifest.get("errors", []) or []),
                    "skipped": len(preview_manifest.get("skipped", []) or []),
                }
            except Exception as e:  # noqa: BLE001
                log.warn(f"[preview_generation] failed: {e}")
                phase_results["preview_generation"] = {"duration_sec": 0.0, "error": str(e)}
        elif analysis_payload:
            phase_results["preview_generation"] = {
                "duration_sec": 0.0,
                "skipped": True,
                "reason": "disabled",
            }

        if analysis_payload:
            next_analysis_path, analysis_summary = write_scene_analysis_sidecar(
                work_dir=work_dir,
                scene_id=scene_id,
                video_path=video_path,
                metadata=metadata,
                phase_results=phase_results,
                all_scored=all_scored,
                candidates=candidates,
                saved_covers=saved_covers,
                ocr_results=analysis_ocr_results,
                policy_flags=analysis_policy_flags,
                preview_outputs=preview_outputs,
            )
            if next_analysis_path is not None:
                analysis_path = next_analysis_path

    # --- PHASE 11: SCENE INSIGHT + AI TITLE/DESCRIPTION ---
    # Best-effort. Always degrades safely on AI offline / parse fail.
    insight_dict = None
    title_payload = None
    if not dry_run and saved_covers and ENABLE_SCENE_INSIGHT:
        try:
            with phase_timer("scene_insight") as t_ins:
                insight_dict, title_payload = _generate_scene_insight_and_titles(
                    video_path=video_path,
                    saved_covers=saved_covers,
                    work_dir=work_dir,
                    ai_client=ai_client,
                    title_tone=TITLE_TONE_DEFAULT,
                )
            phase_results["scene_insight"] = {
                "duration_sec": t_ins.elapsed,
                "insight_ok": insight_dict is not None,
                "ai_titles_ok": bool(title_payload and title_payload.get("ai_used")),
                "n_titles": len(title_payload.get("titles", [])) if title_payload else 0,
            }
        except Exception as e:
            log.warn(f"[scene_insight] failed: {e}")
            phase_results["scene_insight"] = {"duration_sec": 0, "error": str(e)}
    elif not dry_run and saved_covers:
        phase_results["scene_insight"] = {"duration_sec": 0.0, "skipped": True, "reason": "disabled"}

    # --- PHASE 12: OPTIONAL SOFT THUMBNAIL (NON-NUDE) ---
    soft_thumb_info = None
    if not dry_run and SOFT_THUMB_ENABLED and work_dir:
        try:
            with phase_timer("soft_thumbnail") as t_soft:
                from amg.output.covers import select_soft_thumbnail
                soft_thumb_info = select_soft_thumbnail(
                    video_path=video_path,
                    output_dir=work_dir,
                    ai_client=ai_client,
                    performer_name=performer_name,
                    performer_code=code_info.get("code", "") if code_info else "",
                    duration_sec=duration_sec,
                    sample_count=SOFT_THUMB_SAMPLE_COUNT,
                    min_score=SOFT_THUMB_MIN_SCORE,
                    filename=SOFT_THUMB_FILENAME,
                )
            phase_results["soft_thumbnail"] = {
                "duration_sec": t_soft.elapsed,
                "selected": bool(soft_thumb_info),
                "score": (soft_thumb_info or {}).get("score"),
            }
        except Exception as e:
            log.warn(f"[soft_thumbnail] failed: {e}")
            phase_results["soft_thumbnail"] = {"duration_sec": 0, "error": str(e)}

    if insight_dict:
        title_info["insight"] = insight_dict
    if title_payload:
        title_info["ai_titles"] = title_payload.get("titles", [])
        title_info["long_description"] = title_payload.get("long_description", "")
        title_info["title_tone"] = title_payload.get("title_tone", TITLE_TONE_DEFAULT)
    if soft_thumb_info:
        title_info["soft_thumbnail"] = soft_thumb_info

    if not dry_run and ENABLE_SCENE_ANALYSIS and work_dir and analysis_payload is not None:
        try:
            next_analysis_path, analysis_summary = write_scene_analysis_sidecar(
                work_dir=work_dir,
                scene_id=scene_id,
                video_path=video_path,
                metadata=metadata,
                phase_results=phase_results,
                all_scored=all_scored,
                candidates=candidates,
                saved_covers=saved_covers,
                insight=insight_dict,
                ocr_results=analysis_ocr_results,
                policy_flags=analysis_policy_flags,
                preview_outputs=preview_outputs,
            )
            if next_analysis_path is not None:
                analysis_path = next_analysis_path
            if isinstance(phase_results.get("scene_analysis"), dict):
                phase_results["scene_analysis"].update(
                    {
                        "path": str(analysis_path) if analysis_path else None,
                        "sections_count": analysis_summary.get("sections_count", 0),
                        "evidence_count": analysis_summary.get("evidence_count", 0),
                        "preview_outputs_count": analysis_summary.get("preview_outputs_count", 0),
                    }
                )
        except Exception as e:  # noqa: BLE001
            log.warn(f"[scene_analysis] final rewrite failed: {e}")

    # --- DECISION LOG ---
    total_duration = time.time() - pipeline_start
    decision_log_path = write_decision_log(
        scene_id=scene_id,
        scene_path=video_path,
        metadata=metadata,
        studio_info={"name": studio_name, "profile": studio_profile},
        performer_info={**(code_info or {}), "source": "filename_code" if code_info else "none"},
        title_info=title_info,
        calibration=calibration,
        phase_results=phase_results,
        final_candidates=candidates,
        saved_covers=saved_covers,
        fallbacks_used=fallbacks_used,
        error_codes=error_codes,
        total_duration_sec=total_duration,
        analysis_path=analysis_path,
        analysis_summary=analysis_summary,
        operator=operator,
        machine_id=machine_id,
    )
    if decision_log_path is None:
        warnings.append("Decision log write failed")
        error_codes.append("E_DECISION_LOG_WRITE")

    # --- LEARNING UPDATE ---
    record_scene_outcome(
        studio_name=studio_name,
        calibration=calibration,
        performer_info=code_info,
        top_pick_score=saved_covers[0]["score"] if saved_covers else 0,
    )

    # --- AUDIT ---
    audit_event(
        "scene_processed",
        scene_id=scene_id,
        details={
            "covers_saved": len(saved_covers),
            "duration_sec": total_duration,
            "fallbacks_used": fallbacks_used,
            "error_codes": error_codes,
        },
    )

    # v11.1.1: three-state outcome instead of pass/fail.
    # - Full success:    covers >= floor                     → success=True,  partial=False
    # - Partial success: 0 < covers < floor, no fatal errors → success=True,  partial=True  (with warning)
    # - Hard failure:    covers == 0                         → success=False, partial=False
    # Previously, partial runs were marked success=False with empty error_codes, which
    # triggered the "INCOMPLETE / Unknown error" path in the CLI even though usable
    # covers existed. Floor-not-met is downgraded to a warning when partial output exists.
    n_saved = len(saved_covers)
    if n_saved == 0 and not dry_run:
        success = False
        partial_success = False
        outcome_label = "FAILED"
    elif n_saved < COVER_FLOOR and not dry_run:
        success = True
        partial_success = True
        warnings.append(
            f"Partial output: {n_saved}/{COVER_FLOOR} covers (floor not reached, but covers usable)"
        )
        # Downgrade E_FLOOR_NOT_MET to a warning state — it's not a fatal error when
        # we have usable output. The fact that we missed the floor is captured in
        # partial_success and the warning above.
        error_codes = [e for e in error_codes if e != "E_FLOOR_NOT_MET"]
        outcome_label = "PARTIAL"
    else:
        success = True
        partial_success = False
        outcome_label = "COMPLETE"

    log.info("=" * 64)
    mark = "✓" if success and not partial_success else ("⚠" if partial_success else "✗")
    log.info(f"{mark} {outcome_label} — {n_saved} covers in {format_duration(total_duration)}")
    log.info("=" * 64)
    _emit_progress(100)

    return _build_result(
        success=success,
        partial_success=partial_success,
        scene_path=video_path,
        scene_id=scene_id,
        covers_saved=n_saved,
        top_pick_score=saved_covers[0]["score"] if saved_covers else 0,
        total_duration_sec=total_duration,
        source_duration_sec=duration_sec,
        error_codes=error_codes,
        warnings=warnings,
        fallbacks_used=fallbacks_used,
        work_dir=work_dir,
        decision_log_path=decision_log_path,
        analysis_path=analysis_path,
        analysis_summary=analysis_summary,
        preview_outputs=preview_outputs,
    )


def _generate_scene_insight_and_titles(
    *,
    video_path,
    saved_covers,
    work_dir,
    ai_client,
    title_tone,
):
    """Phase 11 helper: vision insight + AI-driven titles & long description.

    Always returns ``(insight_dict_or_None, title_payload_or_None)`` and
    persists ``insight.json`` to the work dir for the UI / CLI to consume.
    Failures are logged and swallowed.
    """
    payload = generate_scene_insight_payload(
        video_path=video_path,
        saved_covers=saved_covers,
        work_dir=work_dir,
        title_tone=title_tone,
        ai_client=ai_client,
        persist=True,
    )
    insight_dict = payload.get("insight")
    title_payload = {
        "titles": payload.get("ai_titles", []),
        "long_description": payload.get("long_description", ""),
        "title_tone": payload.get("title_tone", title_tone),
        "categories": payload.get("ai_categories", []),
        "tags": payload.get("ai_tags", []),
        "ai_used": payload.get("ai_used", False),
    }
    return insight_dict, title_payload


def _build_result(**kwargs):
    """Build a uniform result dict."""
    return kwargs


def _abort_with_partial(
    video_path, scene_id, error_codes, warnings, pipeline_start, phase_results,
    candidates, metadata, studio_profile, code_info, title_info, calibration,
    operator, machine_id, dry_run,
):
    """Write partial decision log and return failure result."""
    total = time.time() - pipeline_start
    log.error("Pipeline aborted early", error_codes=error_codes, partial_candidates=len(candidates))

    decision_log_path = write_decision_log(
        scene_id=scene_id,
        scene_path=video_path,
        metadata=metadata,
        studio_info={"profile": studio_profile},
        performer_info=code_info,
        title_info=title_info,
        calibration=calibration,
        phase_results=phase_results,
        final_candidates=candidates,
        saved_covers=[],
        fallbacks_used=[],
        error_codes=error_codes,
        total_duration_sec=total,
        operator=operator,
        machine_id=machine_id,
    )
    if decision_log_path is None:
        warnings.append("Decision log write failed")
        error_codes.append("E_DECISION_LOG_WRITE")
    return _build_result(
        success=False,
        scene_path=video_path,
        scene_id=scene_id,
        covers_saved=0,
        total_duration_sec=total,
        error_codes=error_codes,
        warnings=warnings,
        decision_log_path=decision_log_path,
    )
