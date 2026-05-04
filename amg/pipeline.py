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
)
from amg.ingest.inventory import find_companion_files, make_work_dir, make_covers_dir
from amg.ingest.studio_profiles import detect_studio, get_or_create_profile
from amg.ingest.performer_code import (
    parse_performer_code,
    get_authoritative_performer_count,
    detect_scene_type_from_code,
)
from amg.ingest.title_parser import parse_title, derive_primary_scene_type
from amg.video.metadata import get_metadata
from amg.video.frames import calibrate_thresholds
from amg.scoring.ai_client import AIClient
from amg.scoring.prompt import build_scoring_prompt, SYSTEM_PROMPT
from amg.scoring.position_classifier import classify_candidate_positions
from amg.scanning.tiered import run_tiered_scan
from amg.scanning.finish_hunter import run_finish_hunter
from amg.scanning.buildup_hunter import run_buildup_hunter
from amg.scanning.cluster import expand_clusters
from amg.scanning.fallback import run_floor_enforcement_cascade
from amg.compliance.doc_2257 import verify_2257
from amg.compliance.audit_log import audit_event
from amg.output.covers import save_covers
from amg.output.contact_sheet import build_contact_sheet
from amg.output.decision_log import write_decision_log
from amg.output.quota_fill import select_quota_fill, quota_satisfied, quota_progress
from amg.learning.recorder import record_scene_outcome
from amg.utils.timing import phase_timer, format_duration
from amg.utils.logging import get_logger, init_logging
from amg.__version__ import __version__

log = get_logger("pipeline")


def process_scene(
    video_path: Path,
    operator: Optional[str] = None,
    machine_id: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """
    Process one scene end-to-end.

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
    scene_id = video_path.parent.name  # Use folder name as scene ID
    init_logging(run_log_name=scene_id)

    log.info("=" * 64)
    log.info(f"AMG OS {__version__} — Processing Scene")
    log.info("=" * 64)
    log.info(f"Scene: {scene_id}")
    log.info(f"Path: {video_path}")
    if dry_run:
        log.info("DRY RUN MODE — no files will be saved")

    pipeline_start = time.time()
    error_codes = []
    warnings = []
    phase_results = {}

    # --- PHASE 1: INVENTORY ---
    studio_name = detect_studio(video_path)
    studio_profile = get_or_create_profile(studio_name) if studio_name else None
    code_info = parse_performer_code(video_path)
    title_info = parse_title(video_path)

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
    log.info("Inventory complete",
             studio=studio_name,
             code=code_info.get("code") if code_info else None,
             scene_type=primary_type,
             genres=title_info.get("detected_genres", []))

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

    # --- PHASE 4: CALIBRATION ---
    with phase_timer("calibration", PHASE_HARD_TIMEOUT_SEC["calibration"]) as t:
        calibration = calibrate_thresholds(video_path, duration_sec)

    phase_results["calibration"] = {
        "duration_sec": t.elapsed,
        "tier_1_floor": calibration["tier_1_floor"],
    }
    log.info("Calibration complete",
             tier_1=int(calibration["tier_1_floor"]),
             tier_2=int(calibration["tier_2_floor"]),
             tier_3=int(calibration["tier_3_floor"]),
             samples=calibration["samples_collected"])

    if calibration["all_blurry"]:
        warnings.append("Source appears entirely blurry")
        error_codes.append("E_CALIB_ALL_BLURRY")

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
    phase_results["tier_scan"] = {
        "duration_sec": t.elapsed,
        "tier_used": tier_result["tier_used"],
        "candidates_found": len(tier_result["candidates"]),
        "frames_scored": len(tier_result["all_scored"]),
        "passing_count": len(tier_result["candidates"]),
        "aborted": tier_result.get("aborted", False),
    }
    if tier_result.get("aborted"):
        error_codes.append(tier_result.get("abort_reason", "E_TIMEOUT_HARD"))

    all_scored = list(tier_result["all_scored"])
    candidates = list(tier_result["candidates"])

    # --- PHASE 6: FINISH HUNTER ---
    if time.time() < deadline and not quota_satisfied(candidates):
        with phase_timer("finish_hunter") as t:
            finish_result = run_finish_hunter(
                video_path, duration_sec, calibration, prompt,
                system_prompt=SYSTEM_PROMPT, deadline_sec=deadline,
            )
        phase_results["finish_hunter"] = {
            "duration_sec": t.elapsed,
            "candidates_found": len(finish_result["candidates"]),
            "passing_count": len(finish_result["candidates"]),
        }
        all_scored.extend(finish_result.get("all_scored", []))
        candidates.extend(finish_result["candidates"])
    elif time.time() < deadline:
        phase_results["finish_hunter"] = {
            "duration_sec": 0.0,
            "skipped": True,
            "reason": "quota_satisfied",
            "quota_progress": quota_progress(candidates),
        }

    # --- PHASE 7: BUILDUP HUNTER ---
    if time.time() < deadline and not quota_satisfied(candidates):
        with phase_timer("buildup_hunter") as t:
            buildup_result = run_buildup_hunter(
                video_path, duration_sec, calibration, prompt,
                system_prompt=SYSTEM_PROMPT, deadline_sec=deadline,
            )
        phase_results["buildup_hunter"] = {
            "duration_sec": t.elapsed,
            "candidates_found": len(buildup_result["candidates"]),
            "passing_count": len(buildup_result["candidates"]),
        }
        all_scored.extend(buildup_result.get("all_scored", []))
        candidates.extend(buildup_result["candidates"])
    elif time.time() < deadline:
        phase_results["buildup_hunter"] = {
            "duration_sec": 0.0,
            "skipped": True,
            "reason": "quota_satisfied",
            "quota_progress": quota_progress(candidates),
        }

    # --- PHASE 8: CLUSTER EXPANSION ---
    if time.time() < deadline and candidates and not quota_satisfied(candidates):
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
                                     and c["scored_frame"].score >= 5.0]),
        }
        # Add cluster results that scored well
        for c in cluster_result["cluster_candidates"]:
            scored = c.get("scored_frame")
            if scored and scored.parse_succeeded and scored.score >= 5.0:
                candidates.append(c)
            all_scored.append(c)
    elif time.time() < deadline and candidates:
        phase_results["cluster"] = {
            "duration_sec": 0.0,
            "skipped": True,
            "reason": "quota_satisfied",
            "quota_progress": quota_progress(candidates),
        }

    # --- PHASE 9: FLOOR ENFORCEMENT ---
    fallbacks_used = []
    if len(candidates) < COVER_FLOOR and time.time() < deadline:
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
        }
        candidates = cascade_result["final_candidates"]
        fallbacks_used = cascade_result["fallbacks_used"]
        if "D" in fallbacks_used:
            warnings.append("Fallback D used (pure CV rescue)")
            error_codes.append("E_FLOOR_FALLBACK_D")
        if not cascade_result["floor_met"]:
            error_codes.append("E_FLOOR_NOT_MET")

    # Apply adaptive cover cap (review-burden control).
    cover_cap = get_cover_cap(duration_sec)
    if cover_cap < COVER_FLOOR:
        cover_cap = COVER_FLOOR

    # Position classifier pass (bounded): attach `position_label` to top
    # position-like candidates so quota-fill can target 3-per-position.
    if candidates and time.time() < deadline:
        with phase_timer("position_classifier") as t:
            pos_stats = classify_candidate_positions(candidates, ai_client=ai_client)
        phase_results["position_classifier"] = {"duration_sec": t.elapsed, **pos_stats}

    # v11.1.5+: quota-fill selection. This is a v0 version that uses existing
    # scoring metadata (tier + TYPE) and keeps a minimum time gap between picks.
    # It is intentionally conservative: if we can't fill the target buckets,
    # we top off by score to at least meet COVER_FLOOR.
    before_select = len(candidates)
    candidates, quota_stats = select_quota_fill(
        candidates,
        max_total=cover_cap,
        min_total=COVER_FLOOR,
    )
    phase_results["quota_fill"] = {"before": before_select, **quota_stats}
    if before_select != len(candidates):
        log.info("Quota-fill selected covers", before=before_select, after=len(candidates), cap=cover_cap)

    # --- PHASE 10: OUTPUT ---
    work_dir = make_work_dir(video_path, version="v11")
    covers_dir = make_covers_dir(work_dir)

    saved_covers = []
    contact_sheet_path = None

    if not dry_run:
        with phase_timer("output") as t:
            performer_name = (studio_profile or {}).get("performers", {}).get("regular", ["Unknown"])
            performer_name = performer_name[0] if performer_name else "Unknown"

            saved_covers = save_covers(
                candidates, video_path, covers_dir,
                performer_name=performer_name,
                performer_code=code_info.get("code", "") if code_info else "",
            )

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
        phase_results["output"] = {"duration_sec": t.elapsed}

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
        operator=operator,
        machine_id=machine_id,
    )

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

    return _build_result(
        success=success,
        partial_success=partial_success,
        scene_path=video_path,
        scene_id=scene_id,
        covers_saved=n_saved,
        top_pick_score=saved_covers[0]["score"] if saved_covers else 0,
        total_duration_sec=total_duration,
        error_codes=error_codes,
        warnings=warnings,
        fallbacks_used=fallbacks_used,
        work_dir=work_dir,
        decision_log_path=decision_log_path,
    )


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
