"""
Decision log writer.

Writes per-scene JSON capturing everything the learning system needs:
- Input characteristics
- Per-phase execution metrics
- Outcomes (covers, scores, fallbacks)
- Resource usage
- Hooks for future operator feedback

These logs are NEVER auto-deleted (see config.DECISION_LOG_RETENTION_DAYS = 365).
They form the historical corpus for v11.1+ learning features.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from amg.config import DECISION_LOGS_DIR, DEFAULT_OPERATOR, DEFAULT_MACHINE_ID
from amg.__version__ import __version__
from amg.utils.logging import get_logger

log = get_logger("output.decision_log")


def write_decision_log(
    scene_id: str,
    scene_path: Path,
    metadata: dict,
    studio_info: dict,
    performer_info: dict,
    title_info: dict,
    calibration: dict,
    phase_results: dict,
    final_candidates: List[dict],
    saved_covers: List[dict],
    fallbacks_used: List[str],
    error_codes: List[str],
    total_duration_sec: float,
    analysis_path: Optional[Path] = None,
    analysis_summary: Optional[dict] = None,
    operator: str = None,
    machine_id: str = None,
) -> Optional[Path]:
    """
    Compose and write the decision log JSON.

    Returns path to the written file. Returns None on write failure.
    """
    DECISION_LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Sanitize scene_id for filename
    safe_id = "".join(c if c.isalnum() or c in "_-" else "_" for c in scene_id)[:120]
    log_path = DECISION_LOGS_DIR / f"{safe_id}.json"

    record = _build_record(
        scene_id=scene_id,
        scene_path=scene_path,
        metadata=metadata,
        studio_info=studio_info,
        performer_info=performer_info,
        title_info=title_info,
        calibration=calibration,
        phase_results=phase_results,
        final_candidates=final_candidates,
        saved_covers=saved_covers,
        fallbacks_used=fallbacks_used,
        error_codes=error_codes,
        total_duration_sec=total_duration_sec,
        analysis_path=analysis_path,
        analysis_summary=analysis_summary,
        operator=operator or DEFAULT_OPERATOR,
        machine_id=machine_id or DEFAULT_MACHINE_ID,
    )

    try:
        with open(log_path, "w") as f:
            json.dump(record, f, indent=2, default=str)
        log.info("Decision log written", path=str(log_path))
        return log_path
    except Exception as e:
        log.error("Failed to write decision log", error=str(e), path=str(log_path))
        return None


def _build_record(
    scene_id, scene_path, metadata, studio_info, performer_info, title_info,
    calibration, phase_results, final_candidates, saved_covers,
    fallbacks_used, error_codes, total_duration_sec, analysis_path,
    analysis_summary, operator, machine_id,
) -> dict:
    """Assemble the full decision log record."""
    return {
        "scene_id": scene_id,
        "scene_path": str(scene_path),
        "version": __version__,
        "timestamp_processed": datetime.utcnow().isoformat() + "Z",
        "operator": operator,
        "machine_id": machine_id,

        "input": _summarize_input(metadata, studio_info, performer_info, title_info),

        "execution": _summarize_execution(
            total_duration_sec, calibration, phase_results, error_codes,
        ),

        "outcomes": _summarize_outcomes(final_candidates, saved_covers, fallbacks_used, phase_results),

        "review_flags": _summarize_review_flags(phase_results),
        "cover_validation_summary": _summarize_cover_validation(phase_results),

        "analysis_path": str(analysis_path) if analysis_path else None,
        "analysis_summary": analysis_summary or {},

        "resource_usage": _summarize_resources(phase_results),

        # Placeholder for future operator feedback (Phase 4 of learning roadmap)
        "human_feedback": {
            "operator_selected_cover": None,
            "operator_rejected_covers": [],
            "operator_notes": None,
            "platform_uploaded_to": None,
            "platform_performance_30d": None,
            "rule_pack_id": None,
            "rule_pack_applied": None,
            "rule_pack_mode": None,
        },
    }


def _summarize_input(metadata, studio_info, performer_info, title_info):
    if metadata is None:
        metadata = {}
    return {
        "duration_sec": metadata.get("duration_sec", 0),
        "fps": metadata.get("fps", 0),
        "resolution": f"{metadata.get('width', 0)}x{metadata.get('height', 0)}",
        "codec": metadata.get("codec", "unknown"),
        "size_gb": metadata.get("size_gb", 0),
        "has_audio": metadata.get("has_audio", False),

        "studio": studio_info.get("name") if studio_info else None,
        "performer_code": performer_info.get("code") if performer_info else None,
        "performer_count": performer_info.get("total") if performer_info else 0,
        "performer_count_source": performer_info.get("source", "unknown"),

        "scene_type": title_info.get("primary_scene_type") if title_info else "STANDARD",
        "genres": title_info.get("detected_genres", []) if title_info else [],
        "description": title_info.get("description") if title_info else "",
    }


def _summarize_execution(total_duration_sec, calibration, phase_results, error_codes):
    phases = {}
    for name, result in (phase_results or {}).items():
        if isinstance(result, dict):
            phase_payload = dict(result)
            phase_payload.setdefault("duration_sec", 0)
            phase_payload.setdefault("candidates_found", 0)
            phase_payload.setdefault("frames_scored", 0)
            phase_payload.setdefault("passing_count", 0)
            phase_payload.setdefault("aborted", False)
            phases[name] = phase_payload

    return {
        "total_duration_sec": round(total_duration_sec, 2),
        "phases": phases,
        "calibration": {
            "tier_1_floor": calibration.get("tier_1_floor"),
            "tier_2_floor": calibration.get("tier_2_floor"),
            "tier_3_floor": calibration.get("tier_3_floor"),
            "samples_collected": calibration.get("samples_collected"),
        } if calibration else None,
        "error_codes": error_codes or [],
    }


def _summarize_outcomes(final_candidates, saved_covers, fallbacks_used, phase_results=None):
    if not saved_covers:
        return {
            "covers_delivered": 0,
            "scores_distribution": {},
            "top_pick_score": 0,
            "top_pick_type": None,
            "top_pick_timestamp_sec": None,
            "fallbacks_used": fallbacks_used or [],
            "cover_validation_summary": _summarize_cover_validation(phase_results),
        }

    scores = [c.get("score", 0) for c in saved_covers]
    top = saved_covers[0] if saved_covers else None

    distribution = {
        "100": sum(1 for s in scores if s >= 100),
        "90-99": sum(1 for s in scores if 90 <= s < 100),
        "80-89": sum(1 for s in scores if 80 <= s < 90),
        "70-79": sum(1 for s in scores if 70 <= s < 80),
        "60-69": sum(1 for s in scores if 60 <= s < 70),
        "50-59": sum(1 for s in scores if 50 <= s < 60),
        "<50": sum(1 for s in scores if s < 50),
    }

    return {
        "covers_delivered": len(saved_covers),
        "covers_verified": sum(1 for c in saved_covers if c.get("verified")),
        "saved_covers": saved_covers,
        "scores_distribution": distribution,
        "top_pick_score": top.get("score") if top else 0,
        "top_pick_type": top.get("type") if top else None,
        "top_pick_timestamp_sec": top.get("timestamp_sec") if top else None,
        "fallbacks_used": fallbacks_used or [],
        "cover_validation_summary": _summarize_cover_validation(phase_results),
    }


def _summarize_cover_validation(phase_results):
    data = (phase_results or {}).get("cover_validation")
    if not isinstance(data, dict):
        return {
            "enabled": False,
            "total_checked": 0,
            "rejected": 0,
            "suspicious": 0,
            "rechecked": 0,
        }
    recheck = data.get("recheck") if isinstance(data.get("recheck"), dict) else {}
    return {
        "enabled": bool(data.get("enabled")),
        "mode": data.get("mode"),
        "total_checked": int(data.get("total_checked", 0) or 0),
        "eligible": int(data.get("eligible", 0) or 0),
        "rejected": int(data.get("rejected", 0) or 0),
        "suspicious": int(data.get("suspicious", 0) or 0),
        "rechecked": int(recheck.get("attempted", data.get("rechecked", 0)) or 0),
        "recheck_rejected": int(recheck.get("rejected", data.get("recheck_rejected", 0)) or 0),
        "reject_counts": data.get("reject_counts", {}),
        "warning_counts": data.get("warning_counts", {}),
    }


def _summarize_review_flags(phase_results):
    content = (phase_results or {}).get("content_flags")
    cover_validation = _summarize_cover_validation(phase_results)
    cover_needs_review = bool(
        cover_validation.get("rejected")
        or cover_validation.get("suspicious")
        or cover_validation.get("recheck_rejected")
    )
    if not isinstance(content, dict):
        return {
            "requires_review": cover_needs_review,
            "sensitive_content": {
                "flagged": False,
                "flags": [],
            },
            "cover_validation": cover_validation,
        }
    return {
        "requires_review": bool(content.get("flagged")) or cover_needs_review,
        "sensitive_content": {
            "flagged": bool(content.get("flagged")),
            "flags": list(content.get("flags", []) or []),
            "max_confidence": content.get("max_confidence", 0.0),
            "max_confidence_by_flag": content.get("max_confidence_by_flag", {}),
            "counts_by_flag": content.get("counts_by_flag", {}),
            "evidence": content.get("evidence", []),
        },
        "cover_validation": cover_validation,
    }


def _summarize_resources(phase_results):
    """Aggregate resource counters from phase results."""
    total_ai_calls = 0
    total_ai_succeeded = 0
    total_ai_retries = 0
    total_frames_extracted = 0

    for name, result in (phase_results or {}).items():
        if not isinstance(result, dict):
            continue
        ai_calls = result.get("ai_calls_total")
        if ai_calls is None:
            ai_calls = result.get("ai_scored_count", result.get("frames_scored", 0))
        total_ai_calls += int(ai_calls or 0)
        total_ai_succeeded += result.get("ai_calls_succeeded", 0)
        total_ai_retries += result.get("ai_calls_retried", 0)
        frames_extracted = result.get("frames_extracted")
        if frames_extracted is None:
            frames_extracted = 0
        total_frames_extracted += int(frames_extracted or 0)

        tier_breakdown = result.get("tier_breakdown")
        if isinstance(tier_breakdown, dict) and result.get("ai_scored_count") is None:
            for tier_result in tier_breakdown.values():
                if not isinstance(tier_result, dict):
                    continue
                total_ai_calls += int(tier_result.get("ai_scored_count", 0) or 0)
        if isinstance(tier_breakdown, dict) and result.get("frames_extracted") is None:
            for tier_result in tier_breakdown.values():
                if not isinstance(tier_result, dict):
                    continue
                total_frames_extracted += int(tier_result.get("frames_extracted", 0) or 0)

    return {
        "ai_calls_total": total_ai_calls,
        "ai_calls_succeeded": total_ai_succeeded,
        "ai_calls_retried": total_ai_retries,
        "frames_extracted": total_frames_extracted,
    }
