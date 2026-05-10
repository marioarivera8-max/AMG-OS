"""
Cover saving — extract full-resolution frames at the chosen timestamps,
enhance them, save with descriptive filenames.

Filename schema (from spec):
    {rank:02d}_{performer}_{code}_{type}_{gaze}_{score:.1f}_{mins}m{secs:02d}s.jpg

Examples (scores on 0–100 scale since v11.1.5):
    01_Yasmina_BBGG_NUDE_DirectGaze_84.5_3m24s.jpg
    02_Yasmina_BBGG_FINISH_Direct_91.0_14m24s.jpg
    09_Yasmina_BBGG_FALLBACK-C_Front_58.0_22m15s.jpg
    10_Yasmina_BBGG_FALLBACK-D_Visual_-.--_18m04s.jpg
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import cv2
from PIL import Image, ImageEnhance

from amg.config import (
    COVER_FORMAT,
    COVER_QUALITY,
    ENHANCE_DEFAULT,
    FILENAME_SCHEMA_PATTERN,
    PROVIDED_THUMB_MAX_SCAN,
    PROVIDED_THUMB_MAX_ACCEPT,
    PROVIDED_THUMB_MIN_SCORE,
    COVER_NEARBY_POLISH_ENABLED,
    COVER_NEARBY_POLISH_MIN_SCORE,
    COVER_NEARBY_POLISH_OFFSETS_SEC,
    COVER_NEARBY_POLISH_MIN_SHARPNESS_GAIN,
    COVER_NEARBY_POLISH_MIN_SHARPNESS_GAIN_PCT,
    COVER_BLUR_RESCUE_ENABLED,
    COVER_BLUR_RESCUE_MIN_SCORE,
    COVER_BLUR_RESCUE_SHARPNESS_FLOOR,
    COVER_BLUR_RESCUE_OFFSETS_SEC,
)
from amg.video.reader import VideoReader
from amg.video.frames import measure_sharpness, is_frame_too_dark
from amg.output.enhance import auto_enhance
from amg.utils.timing import format_timestamp_mmss
from amg.utils.logging import get_logger

log = get_logger("output.covers")


def build_filename(
    rank: int,
    performer: str,
    code: str,
    type_: str,
    gaze: str,
    score: float,
    timestamp_sec: float,
    tier: Optional[str] = None,
) -> str:
    """
    Build a cover filename per the schema.

    Args:
        rank: Cover rank (1-based)
        performer: Lead performer first name (e.g., "Yasmina")
        code: Performer code (e.g., "BBGG")
        type_: Scene type (NUDE/SEX_ACT/PENETRATION/FINISH/BUILDUP/COMPOSITION)
        gaze: Gaze direction (DIRECT/AVERTED/CLOSED/REAR)
        score: AI score (0–100)
        timestamp_sec: Position in video
        tier: Origin tier (tier_1/tier_2/finish_hunter/cluster/fallback_c/etc.)
    """
    # Sanitize performer name (alphanumeric + hyphens)
    performer_clean = re.sub(r"[^A-Za-z0-9-]", "", performer)[:20] or "Unknown"

    # Tier override for fallback
    if tier and tier.startswith("fallback_"):
        type_ = f"FALLBACK-{tier.split('_')[-1].upper()}"

    # Mark special types based on tier
    if tier == "finish_hunter":
        type_ = "FINISH"
    elif tier == "buildup_hunter":
        type_ = "BUILDUP"
    elif tier == "cluster":
        type_ = "CLUSTER"

    # Score formatting (handle synthetic CV-only scores)
    if score == 0:
        score_str = "-.-"
    else:
        score_str = f"{score:.1f}"

    # Time
    seconds = int(timestamp_sec)
    mins, secs = divmod(seconds, 60)

    # Build
    name = (
        f"{rank:02d}_{performer_clean}_{code}_{type_}_{gaze.title()}_"
        f"{score_str}_{mins}m{secs:02d}s.jpg"
    )
    # Final safety: replace any remaining problematic chars
    name = re.sub(r"[^\w.\-]", "_", name)
    return name


def save_covers(
    candidates: List[dict],
    video_path: Path,
    output_dir: Path,
    performer_name: str = "Unknown",
    performer_code: str = "",
    enhance: bool = ENHANCE_DEFAULT,
    *,
    frame_cache: Optional[object] = None,
) -> List[dict]:
    """
    Save selected candidates as cover JPEGs.

    When ``frame_cache`` is provided (an ``amg.video.frame_cache.FrameCache``
    instance from a streaming scan), full-resolution frames are read from
    the cache before falling back to a fresh disk decode. This eliminates
    the redundant ``get_frames_at`` pass that dominated the output phase
    in the classic pipeline (6:28 of a 24:34 wall-time on Y&B_003,
    2026-05-08 audit).

    Returns list of saved cover info:
        {
            'rank': int,
            'path': Path,
            'timestamp_sec': float,
            'score': float,
            'verified': bool,
            'verification_error': str or None,
        }
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # v11.1.1: wipe stale covers from prior runs so we don't end up with two
    # files at rank 01 (one fresh, one leftover from a previous run with
    # different score/timestamp/scoring). Only deletes .jpg files in the
    # pipeline-managed covers/ subdir; any non-cover content survives.
    stale = list(output_dir.glob("*.jpg"))
    for old in stale:
        try:
            old.unlink()
        except OSError as e:
            log.warn("Could not remove stale cover", path=str(old), error=str(e))
    if stale:
        log.info("Cleared stale covers from prior run", count=len(stale), dir=str(output_dir))

    # Sort by score descending (highest first)
    sorted_candidates = sorted(
        candidates,
        key=lambda x: x.get("scored_frame").score if x.get("scored_frame") else 0,
        reverse=True,
    )

    saved = []
    base_timestamps = [float(entry.get("timestamp_sec", 0) or 0) for entry in sorted_candidates]

    cache_hits = 0
    cache_misses = 0

    base_frame_by_idx: Dict[int, Any] = {}
    nearby_frame_map: Dict[float, Any] = {}

    # First, satisfy whatever the streaming cache can give us for free.
    # Anything else is appended to the disk-decode list.
    base_disk_indices: List[int] = []
    base_disk_timestamps: List[float] = []
    if frame_cache is not None:
        for idx, ts in enumerate(base_timestamps):
            cached = frame_cache.get_full(ts) if ts is not None else None
            if cached is not None:
                base_frame_by_idx[idx] = cached
                cache_hits += 1
            else:
                base_disk_indices.append(idx)
                base_disk_timestamps.append(ts)
                cache_misses += 1
    else:
        base_disk_indices = list(range(len(base_timestamps)))
        base_disk_timestamps = list(base_timestamps)

    need_disk_pass = bool(base_disk_timestamps)
    if need_disk_pass:
        with VideoReader(video_path) as vr:
            if base_disk_timestamps:
                disk_frames = vr.get_frames_at(base_disk_timestamps)
                for idx_in_sorted, frame in zip(base_disk_indices, disk_frames):
                    base_frame_by_idx[idx_in_sorted] = frame

    base_sharpness_by_idx: Dict[int, float] = {}
    for idx, entry in enumerate(sorted_candidates):
        frame = base_frame_by_idx.get(idx)
        if frame is None and not entry.get("_analysis_frame_only"):
            frame = entry.get("frame")
        if frame is None:
            continue
        base_sharpness_by_idx[idx] = measure_sharpness(frame)

    nearby_timestamps = _collect_needed_nearby_timestamps(sorted_candidates, base_sharpness_by_idx)
    nearby_disk_timestamps: List[float] = []
    if nearby_timestamps:
        if frame_cache is not None:
            for ts in nearby_timestamps:
                cached = frame_cache.get_full(ts)
                if cached is not None:
                    nearby_frame_map[round(float(ts), 3)] = cached
                    cache_hits += 1
                else:
                    nearby_disk_timestamps.append(ts)
                    cache_misses += 1
        else:
            nearby_disk_timestamps = list(nearby_timestamps)
    if nearby_disk_timestamps:
        with VideoReader(video_path) as vr:
            nearby_frames = vr.get_frames_at(nearby_disk_timestamps)
            for ts, frame in zip(nearby_disk_timestamps, nearby_frames):
                if frame is None:
                    continue
                nearby_frame_map[round(float(ts), 3)] = frame

    if frame_cache is not None:
        log.info(
            "save_covers cache stats",
            hits=cache_hits,
            misses=cache_misses,
            re_decode=bool(base_disk_timestamps or nearby_disk_timestamps),
            nearby_requested=len(nearby_timestamps),
        )

    for rank, entry in enumerate(sorted_candidates, start=1):
        scored = entry.get("scored_frame")
        ts = entry.get("timestamp_sec", 0)

        full_frame = base_frame_by_idx.get(rank - 1)
        if full_frame is None and not entry.get("_analysis_frame_only"):
            full_frame = entry.get("frame")
        if full_frame is None:
            log.warn("Could not extract frame", timestamp=ts, rank=rank)
            continue

        score = scored.score if scored else 0
        blur_rescue = {}
        polish = {}
        if COVER_BLUR_RESCUE_ENABLED and score >= COVER_BLUR_RESCUE_MIN_SCORE:
            ts, full_frame, blur_rescue = _rescue_blurry_frame(
                ts,
                full_frame,
                nearby_frames_by_ts=nearby_frame_map,
            )
        if (
            COVER_NEARBY_POLISH_ENABLED
            and score >= COVER_NEARBY_POLISH_MIN_SCORE
            and not blur_rescue.get("applied")
        ):
            ts, full_frame, polish = _polish_nearby_frame(
                ts,
                full_frame,
                nearby_frames_by_ts=nearby_frame_map,
            )
        output_sharpness = measure_sharpness(full_frame)
        type_ = scored.type_ if scored else "UNKNOWN"
        gaze = scored.gaze if scored else "UNKNOWN"
        tier = entry.get("tier", "")

        filename = build_filename(
            rank=rank,
            performer=performer_name,
            code=performer_code,
            type_=type_,
            gaze=gaze,
            score=score,
            timestamp_sec=ts,
            tier=tier,
        )
        output_path = output_dir / filename

        success = _save_frame(full_frame, output_path, enhance=enhance)
        if not success:
            log.warn("Failed to save cover", path=str(output_path))
            continue

        verified, error = verify_cover(output_path)

        saved.append({
            "rank": rank,
            "path": output_path,
            "filename": filename,
            "timestamp_sec": ts,
            "score": score,
            "output_sharpness": round(float(output_sharpness), 2),
            "blur_rescue": blur_rescue or None,
            "nearby_polish": polish or None,
            "type": type_,
            "gaze": gaze,
            "tier": tier,
            "position_label": entry.get("position_label") or getattr(scored, "position_label", "OTHER"),
            "position_label_confidence": entry.get("position_label_confidence", getattr(scored, "position_confidence", 0.0)),
            "position_segment_id": entry.get("position_segment_id"),
            "position_segment_label": entry.get("position_segment_label"),
            "position_segment_start_sec": entry.get("position_segment_start_sec"),
            "position_segment_end_sec": entry.get("position_segment_end_sec"),
            "analysis_section_tag": entry.get("analysis_section_tag"),
            "genre_tags": list(entry.get("genre_tags", []) or []),
            "subgenre_tags": list(entry.get("subgenre_tags", []) or []),
            "sensitive_content_flags": list(
                entry.get("sensitive_content_flags", getattr(scored, "sensitive_content_flags", [])) or []
            ),
            "sensitive_content_confidence": entry.get(
                "sensitive_content_confidence",
                getattr(scored, "sensitive_content_confidence", 0.0),
            ),
            "penetration_visible": bool(getattr(scored, "penetration_visible", False)) if scored else False,
            "penetration_confidence": float(getattr(scored, "penetration_confidence", 0.0) or 0.0) if scored else 0.0,
            "action_evidence": getattr(scored, "action_evidence", "NONE") if scored else "NONE",
            "cover_validation": entry.get("cover_validation"),
            "verified": verified,
            "verification_error": error,
        })

    log.info("Saved covers", count=len(saved), verified=sum(1 for s in saved if s["verified"]))
    return saved


def _polish_nearby_frame(
    ts: float,
    base_frame,
    *,
    nearby_frames_by_ts: Optional[Dict[float, Any]] = None,
) -> tuple[float, Any, dict]:
    """
    Try a few nearby timestamps and keep the sharpest frame if meaningfully better.

    This is a localized rescue for near-miss blur on otherwise strong picks.
    """
    if base_frame is None:
        return ts, base_frame, {"applied": False, "reason": "missing_base_frame"}

    base_sharp = measure_sharpness(base_frame)
    best_ts = ts
    best_frame = base_frame
    best_sharp = base_sharp

    for dt in COVER_NEARBY_POLISH_OFFSETS_SEC:
        cand_ts = max(0.0, float(ts) + float(dt))
        cand = None
        if nearby_frames_by_ts:
            cand = nearby_frames_by_ts.get(round(cand_ts, 3))
        if cand is None or is_frame_too_dark(cand):
            continue
        sharp = measure_sharpness(cand)
        if sharp > best_sharp:
            best_ts = cand_ts
            best_frame = cand
            best_sharp = sharp

    sharp_gain = best_sharp - base_sharp
    pct_gain = (sharp_gain / base_sharp) if base_sharp > 0 else 1.0
    if sharp_gain >= COVER_NEARBY_POLISH_MIN_SHARPNESS_GAIN and pct_gain >= COVER_NEARBY_POLISH_MIN_SHARPNESS_GAIN_PCT:
        log.info(
            "Nearby-frame polish selected sharper frame",
            from_ts=round(float(ts), 3),
            to_ts=round(float(best_ts), 3),
            sharp_from=round(float(base_sharp), 1),
            sharp_to=round(float(best_sharp), 1),
        )
        return best_ts, best_frame, {
            "applied": True,
            "from_ts": round(float(ts), 3),
            "to_ts": round(float(best_ts), 3),
            "sharpness_before": round(float(base_sharp), 2),
            "sharpness_after": round(float(best_sharp), 2),
        }
    return ts, base_frame, {
        "applied": False,
        "sharpness_before": round(float(base_sharp), 2),
        "sharpness_after": round(float(best_sharp), 2),
    }


def _rescue_blurry_frame(
    ts: float,
    base_frame,
    *,
    nearby_frames_by_ts: Optional[Dict[float, Any]] = None,
) -> tuple[float, Any, dict]:
    if base_frame is None:
        return ts, base_frame, {"applied": False, "reason": "missing_base_frame"}
    floor = float(COVER_BLUR_RESCUE_SHARPNESS_FLOOR or 0.0)
    if floor <= 0:
        return ts, base_frame, {"applied": False, "reason": "disabled_floor"}

    base_sharp = measure_sharpness(base_frame)
    if base_sharp >= floor:
        return ts, base_frame, {
            "applied": False,
            "reason": "already_sharp",
            "sharpness_before": round(float(base_sharp), 2),
            "sharpness_floor": round(floor, 2),
        }

    for dt in sorted(COVER_BLUR_RESCUE_OFFSETS_SEC, key=lambda x: (abs(float(x)), float(x) < 0)):
        cand_ts = max(0.0, float(ts) + float(dt))
        cand = nearby_frames_by_ts.get(round(cand_ts, 3)) if nearby_frames_by_ts else None
        if cand is None or is_frame_too_dark(cand):
            continue
        sharp = measure_sharpness(cand)
        if sharp >= floor:
            log.info(
                "Blur rescue selected closest sharp nearby frame",
                from_ts=round(float(ts), 3),
                to_ts=round(float(cand_ts), 3),
                sharp_from=round(float(base_sharp), 1),
                sharp_to=round(float(sharp), 1),
                floor=round(floor, 1),
            )
            return cand_ts, cand, {
                "applied": True,
                "from_ts": round(float(ts), 3),
                "to_ts": round(float(cand_ts), 3),
                "offset_sec": round(float(dt), 3),
                "sharpness_before": round(float(base_sharp), 2),
                "sharpness_after": round(float(sharp), 2),
                "sharpness_floor": round(floor, 2),
            }

    return ts, base_frame, {
        "applied": False,
        "reason": "no_nearby_frame_cleared_floor",
        "sharpness_before": round(float(base_sharp), 2),
        "sharpness_floor": round(floor, 2),
    }


def _collect_nearby_timestamps(candidates: List[dict]) -> List[float]:
    out: List[float] = []
    seen: set[float] = set()
    for entry in candidates:
        scored = entry.get("scored_frame")
        score = float(scored.score) if scored and getattr(scored, "score", None) is not None else 0.0
        base_ts = float(entry.get("timestamp_sec", 0) or 0)
        offsets: List[float] = []
        if COVER_BLUR_RESCUE_ENABLED and score >= COVER_BLUR_RESCUE_MIN_SCORE:
            offsets.extend(float(x) for x in COVER_BLUR_RESCUE_OFFSETS_SEC)
        if COVER_NEARBY_POLISH_ENABLED and score >= COVER_NEARBY_POLISH_MIN_SCORE:
            offsets.extend(float(x) for x in COVER_NEARBY_POLISH_OFFSETS_SEC)
        for dt in offsets:
            ts = round(max(0.0, base_ts + float(dt)), 3)
            if ts in seen:
                continue
            seen.add(ts)
            out.append(ts)
    return out


def _collect_needed_nearby_timestamps(
    candidates: List[dict],
    base_sharpness_by_idx: Dict[int, float],
) -> List[float]:
    """Return only nearby timestamps that can affect final output.

    Blur rescue only needs neighbors when the full-res selected frame is
    actually below the rescue floor. This keeps fast streaming runs from
    decoding ten nearby frames for every already-sharp cover.
    """
    out: List[float] = []
    seen: set[float] = set()
    floor = float(COVER_BLUR_RESCUE_SHARPNESS_FLOOR or 0.0)
    for idx, entry in enumerate(candidates):
        scored = entry.get("scored_frame")
        score = float(scored.score) if scored and getattr(scored, "score", None) is not None else 0.0
        base_ts = float(entry.get("timestamp_sec", 0) or 0)
        base_sharp = float(base_sharpness_by_idx.get(idx, 0.0) or 0.0)
        offsets: List[float] = []
        if (
            COVER_BLUR_RESCUE_ENABLED
            and score >= COVER_BLUR_RESCUE_MIN_SCORE
            and floor > 0
            and base_sharp < floor
        ):
            offsets.extend(float(x) for x in COVER_BLUR_RESCUE_OFFSETS_SEC)
        if COVER_NEARBY_POLISH_ENABLED and score >= COVER_NEARBY_POLISH_MIN_SCORE:
            offsets.extend(float(x) for x in COVER_NEARBY_POLISH_OFFSETS_SEC)
        for dt in offsets:
            ts = round(max(0.0, base_ts + float(dt)), 3)
            if ts in seen:
                continue
            seen.add(ts)
            out.append(ts)
    return out


def score_and_save_provided_thumbnails(
    *,
    video_path: Path,
    output_dir: Path,
    ai_client,
    search_root: Optional[Path] = None,
    performer_name: str = "Unknown",
    performer_code: str = "",
    rank_start: int = 1,
    cover_cap: Optional[int] = None,
    max_scan: int = PROVIDED_THUMB_MAX_SCAN,
    max_accept: int = PROVIDED_THUMB_MAX_ACCEPT,
    min_score: float = PROVIDED_THUMB_MIN_SCORE,
    enhance: bool = ENHANCE_DEFAULT,
) -> Tuple[List[dict], dict]:
    """
    Score creator/agency-provided thumbnails and import good ones into covers/.

    This is intentionally conservative:
      - only local files near the source scene are scanned
      - only high-scoring images are imported
      - never exceeds the scene cover cap

    Returns:
      (saved_imports, stats_dict)
    """
    from amg.scoring.prompt import build_simplified_prompt
    from amg.scoring.parser import parse_ai_response

    discovered = _discover_provided_thumbnail_paths(
        video_path=video_path,
        output_dir=output_dir,
        max_scan=max_scan,
        search_root=search_root,
    )
    if not discovered:
        return [], {
            "discovered": 0,
            "scanned": 0,
            "accepted": 0,
            "imported": 0,
            "rejected": 0,
            "rows": [],
        }

    prompt = build_simplified_prompt()
    rows: List[Dict[str, Any]] = []
    accepted: List[Dict[str, Any]] = []

    for img_path in discovered:
        row: Dict[str, Any] = {
            "source_path": str(img_path),
            "filename": img_path.name,
            "score": 0.0,
            "accepted": False,
            "reason": "UNSET",
        }
        frame = cv2.imread(str(img_path))
        if frame is None:
            row["reason"] = "READ_FAIL"
            rows.append(row)
            continue

        ai_resp = ai_client.score_frame(frame, prompt)
        if not ai_resp.success:
            row["reason"] = ai_resp.error_code or "AI_FAIL"
            rows.append(row)
            continue

        scored = parse_ai_response(ai_resp.raw_text)
        row["score"] = float(scored.score or 0.0)
        row["type"] = scored.type_
        row["gaze"] = scored.gaze
        row["penetration_visible"] = bool(scored.penetration_visible)
        row["penetration_confidence"] = float(scored.penetration_confidence or 0.0)

        if not scored.parse_succeeded:
            row["reason"] = "PARSE_FAIL"
            rows.append(row)
            continue
        if scored.score < min_score:
            row["reason"] = f"LOW_SCORE<{min_score:.1f}"
            rows.append(row)
            continue

        row["accepted"] = True
        row["reason"] = "ACCEPT"
        rows.append(row)
        accepted.append({"path": img_path, "frame": frame, "scored": scored, "row": row})

    accepted.sort(key=lambda x: x["scored"].score, reverse=True)
    accepted_limit = max_accept
    if cover_cap is not None:
        accepted_limit = max(0, min(max_accept, cover_cap - rank_start + 1))
    to_import = accepted[:accepted_limit]

    saved: List[dict] = []
    for i, item in enumerate(to_import):
        scored = item["scored"]
        img_path = item["path"]
        rank = rank_start + i
        filename = _build_provided_filename(
            rank=rank,
            performer=performer_name,
            code=performer_code,
            type_=scored.type_,
            gaze=scored.gaze,
            score=scored.score,
            source_stem=img_path.stem,
        )
        out_path = output_dir / filename
        if not _save_frame(item["frame"], out_path, enhance=enhance):
            continue

        verified, error = verify_cover(out_path)
        saved.append(
            {
                "rank": rank,
                "path": out_path,
                "filename": filename,
                "timestamp_sec": 0.0,
                "score": float(scored.score or 0.0),
                "type": scored.type_,
                "gaze": scored.gaze,
                "tier": "provided_thumbnail",
                "position_label": getattr(scored, "position_label", "OTHER"),
                "position_label_confidence": float(getattr(scored, "position_confidence", 0.0) or 0.0),
                "position_segment_id": None,
                "position_segment_label": None,
                "position_segment_start_sec": None,
                "position_segment_end_sec": None,
                "genre_tags": list(getattr(scored, "genre_tags", []) or []),
                "subgenre_tags": list(getattr(scored, "subgenre_tags", []) or []),
                "sensitive_content_flags": list(getattr(scored, "sensitive_content_flags", []) or []),
                "sensitive_content_confidence": float(getattr(scored, "sensitive_content_confidence", 0.0) or 0.0),
                "penetration_visible": bool(scored.penetration_visible),
                "penetration_confidence": float(scored.penetration_confidence or 0.0),
                "action_evidence": scored.action_evidence,
                "provided_source_path": str(img_path),
                "verified": verified,
                "verification_error": error,
            }
        )

    stats = {
        "discovered": len(discovered),
        "scanned": len(rows),
        "accepted": len(accepted),
        "imported": len(saved),
        "rejected": max(0, len(rows) - len(accepted)),
        "rows": rows,
    }
    log.info(
        "Provided thumbnails graded",
        discovered=stats["discovered"],
        scanned=stats["scanned"],
        accepted=stats["accepted"],
        imported=stats["imported"],
        min_score=min_score,
    )
    return saved, stats


def select_soft_thumbnail(
    *,
    video_path: Path,
    output_dir: Path,
    ai_client,
    performer_name: str = "Unknown",
    performer_code: str = "",
    duration_sec: Optional[float] = None,
    sample_count: int = 24,
    min_score: float = 72.0,
    filename: str = "00_soft_thumbnail.jpg",
    enhance: bool = ENHANCE_DEFAULT,
) -> Optional[dict]:
    """
    Select and save one optional non-nude thumbnail for studio/platform use.
    """
    from amg.scoring.prompt import build_soft_thumbnail_prompt
    from amg.scoring.parser import parse_ai_response

    if duration_sec is None:
        from amg.video.metadata import get_metadata
        duration_sec = float(get_metadata(video_path).get("duration_sec", 0.0) or 0.0)
    if duration_sec <= 0:
        return None

    sample_count = max(8, int(sample_count or 24))
    prompt = build_soft_thumbnail_prompt()
    best: Optional[dict] = None
    rows: List[Dict[str, Any]] = []

    start = duration_sec * 0.05
    end = duration_sec * 0.95
    step = (end - start) / max(1, sample_count - 1)
    timestamps = [start + i * step for i in range(sample_count)]

    with VideoReader(video_path) as vr:
        frames = vr.get_frames_at(timestamps)
        for ts, frame in zip(timestamps, frames):
            if frame is None or is_frame_too_dark(frame):
                continue
            ai_resp = ai_client.score_frame(frame, prompt)
            if not ai_resp.success:
                continue
            scored = parse_ai_response(ai_resp.raw_text)
            if not scored.parse_succeeded:
                continue
            score = float(scored.score or 0.0)
            row = {
                "timestamp_sec": round(float(ts), 3),
                "score": round(score, 1),
                "type": scored.type_,
                "gaze": scored.gaze,
                "pen_visible": bool(scored.penetration_visible),
            }
            rows.append(row)
            # Enforce soft safety.
            if scored.penetration_visible:
                continue
            if scored.type_.upper() == "NUDE":
                continue
            if score < min_score:
                continue
            if best is None or score > float(best["score"]):
                best = {
                    "frame": frame,
                    "timestamp_sec": float(ts),
                    "score": score,
                    "type": scored.type_,
                    "gaze": scored.gaze,
                }

    report_path = output_dir / "soft_thumbnail.json"
    try:
        report_path.write_text(json.dumps({"rows": rows, "selected": best is not None}, indent=2))
    except Exception:
        pass

    if not best:
        log.info("Soft thumbnail skipped", reason="no_safe_candidate", scanned=len(rows), min_score=min_score)
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / filename
    if not _save_frame(best["frame"], out_path, enhance=enhance):
        return None
    verified, error = verify_cover(out_path)
    result = {
        "path": out_path,
        "filename": out_path.name,
        "timestamp_sec": best["timestamp_sec"],
        "score": round(float(best["score"]), 1),
        "type": best["type"],
        "gaze": best["gaze"],
        "verified": verified,
        "verification_error": error,
        "performer": performer_name,
        "code": performer_code,
    }
    log.info("Soft thumbnail selected", score=result["score"], timestamp_sec=result["timestamp_sec"], path=str(out_path))
    return result


def _discover_provided_thumbnail_paths(
    video_path: Path,
    output_dir: Path,
    max_scan: int,
    search_root: Optional[Path] = None,
) -> List[Path]:
    """
    Find likely creator-supplied thumbnail images next to the source scene.
    """
    parent = (search_root or video_path.parent).resolve()
    image_exts = {".jpg", ".jpeg", ".png", ".webp"}
    skip_stem_tokens = {"2257", "passport", "license", "id", "contact_sheet"}
    prefer_dir_names = {"thumbs", "thumbnails", "thumbnail", "covers", "cover", "agency_covers"}

    found: List[Path] = []

    # Direct siblings first.
    try:
        siblings = sorted(parent.iterdir())
    except Exception:
        siblings = []
    for p in siblings:
        if len(found) >= max_scan:
            break
        if not p.is_file():
            continue
        if p.suffix.lower() not in image_exts:
            continue
        if output_dir.resolve() in p.resolve().parents:
            continue
        stem = p.stem.lower()
        if any(tok in stem for tok in skip_stem_tokens):
            continue
        found.append(p)

    # Then scan a few common subfolders for supplied covers.
    for p in siblings:
        if len(found) >= max_scan:
            break
        if not p.is_dir() or p.name.lower() not in prefer_dir_names:
            continue
        for q in sorted(p.glob("*")):
            if len(found) >= max_scan:
                break
            if not q.is_file() or q.suffix.lower() not in image_exts:
                continue
            stem = q.stem.lower()
            if any(tok in stem for tok in skip_stem_tokens):
                continue
            found.append(q)

    return found[:max_scan]


def _build_provided_filename(
    *,
    rank: int,
    performer: str,
    code: str,
    type_: str,
    gaze: str,
    score: float,
    source_stem: str,
) -> str:
    performer_clean = re.sub(r"[^A-Za-z0-9-]", "", performer)[:20] or "Unknown"
    source_clean = re.sub(r"[^A-Za-z0-9_-]", "_", source_stem)[:30] or "source"
    score_str = f"{score:.1f}" if score else "-.-"
    code_clean = code or "NA"
    return (
        f"{rank:02d}_{performer_clean}_{code_clean}_PROVIDED_"
        f"{type_}_{gaze.title()}_{score_str}_{source_clean}.jpg"
    )


def _save_frame(frame_bgr, output_path: Path, enhance: bool) -> bool:
    """Save a BGR frame as JPEG with optional enhancement."""
    try:
        # Convert BGR -> RGB for PIL
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)

        if enhance:
            pil_img = auto_enhance(pil_img)

        pil_img.save(output_path, COVER_FORMAT, quality=COVER_QUALITY, optimize=True)
        return True
    except Exception as e:
        log.error("Save failed", error=str(e), path=str(output_path))
        return False


def verify_cover(path: Path) -> tuple:
    """
    Verify a saved cover is valid.

    Returns (is_valid, error_code_or_None).
    """
    try:
        if not path.exists():
            return False, "FILE_MISSING"
        if path.stat().st_size < 1024:
            return False, "FILE_TOO_SMALL"

        img = Image.open(path)
        img.verify()  # Verify integrity

        # Re-open for size check (verify() closes the file)
        img = Image.open(path)
        w, h = img.size
        if w < 640 or h < 360:
            return False, "DIMENSIONS_TOO_SMALL"

        # Not entirely solid color
        gray = img.convert("L")
        extrema = gray.getextrema()
        if extrema[1] - extrema[0] < 30:
            return False, "APPEARS_BLANK"

        return True, None
    except Exception as e:
        return False, f"VERIFY_ERROR: {e}"
