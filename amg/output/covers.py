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
)
from amg.video.reader import VideoReader
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
) -> List[dict]:
    """
    Save selected candidates as cover JPEGs.

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
    with VideoReader(video_path) as vr:
        for rank, entry in enumerate(sorted_candidates, start=1):
            scored = entry.get("scored_frame")
            ts = entry.get("timestamp_sec", 0)

            # Re-extract at full resolution from video (not the analysis frame)
            full_frame = vr.get_frame_at(ts)
            if full_frame is None:
                # Fall back to the analysis frame we already have
                full_frame = entry.get("frame")
            if full_frame is None:
                log.warn("Could not extract frame", timestamp=ts, rank=rank)
                continue

            score = scored.score if scored else 0
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

            # Save (with optional enhancement)
            success = _save_frame(full_frame, output_path, enhance=enhance)
            if not success:
                log.warn("Failed to save cover", path=str(output_path))
                continue

            # Verify
            verified, error = verify_cover(output_path)

            saved.append({
                "rank": rank,
                "path": output_path,
                "filename": filename,
                "timestamp_sec": ts,
                "score": score,
                "type": type_,
                "gaze": gaze,
                "tier": tier,
                "position_label": entry.get("position_label", "OTHER"),
                "position_label_confidence": entry.get("position_label_confidence", 0.0),
                "penetration_visible": bool(getattr(scored, "penetration_visible", False)) if scored else False,
                "penetration_confidence": float(getattr(scored, "penetration_confidence", 0.0) or 0.0) if scored else 0.0,
                "action_evidence": getattr(scored, "action_evidence", "NONE") if scored else "NONE",
                "verified": verified,
                "verification_error": error,
            })

    log.info("Saved covers", count=len(saved), verified=sum(1 for s in saved if s["verified"]))
    return saved


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
                "position_label": "OTHER",
                "position_label_confidence": 0.0,
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
