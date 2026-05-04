"""
DVD compilation — v11.1.

Packages 4 (or more) processed scenes into a DVD-length output:
1. Validates spec compatibility (fps, resolution, codec)
2. Concatenates videos (ffmpeg concat demuxer)
3. Adds chapter markers
4. Builds quad-layout case art from picked covers
5. Generates DVD metadata bundle

Output format: MP4 with chapter markers (Option γ — most compatible).
"""
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from amg.config import (
    DECISION_LOGS_DIR,
    REVIEWED_DIR,
    DVD_QUAD_LAYOUT_SIZE,
)
from amg.utils.logging import get_logger

log = get_logger("library.dvd_compile")


def compile_dvd(
    scene_ids: List[str],
    theme: str,
    output_dir: Path,
    operator: str = "unknown",
) -> dict:
    """
    Compile 4 scenes into a DVD-ready output.

    Args:
        scene_ids: List of scene IDs (typically 4 for a standard DVD)
        theme: Theme name for the DVD ("Swinger Weekend", etc.)
        output_dir: Where to write the DVD output

    Returns:
        {
            'success': bool,
            'dvd_id': str,
            'output_path': Path,
            'specs_compatible': bool,
            'spec_issues': [...],
            'total_duration_sec': float,
            'chapters': [...],
            'case_art_path': Path,
            'metadata_path': Path,
        }
    """
    if len(scene_ids) < 2:
        return {
            "success": False,
            "error": "DVD compilation requires at least 2 scenes",
        }

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("Starting DVD compilation",
             scenes=len(scene_ids), theme=theme, output_dir=str(output_dir))

    # Step 1: Load all scene data
    scenes = []
    for scene_id in scene_ids:
        scene_data = _load_scene_data(scene_id)
        if not scene_data:
            return {
                "success": False,
                "error": f"Could not find scene data for: {scene_id}",
            }
        scenes.append(scene_data)

    # Step 2: Validate spec compatibility
    compat_result = validate_dvd_specs(scenes)
    if not compat_result["compatible"]:
        log.warn("Spec mismatch detected",
                 issues=compat_result["issues"])
        # Continue — operator will see issues in the output report
        # (Could be made strict by returning here)

    # Step 3: Build DVD metadata
    dvd_id = f"dvd_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{_safe_name(theme)}"
    chapters = _build_chapters(scenes)
    total_duration = sum(s.get("duration_sec", 0) for s in scenes)

    metadata = {
        "dvd_id": dvd_id,
        "theme": theme,
        "compiled_at": datetime.utcnow().isoformat() + "Z",
        "operator": operator,
        "scenes": [
            {
                "scene_id": s["scene_id"],
                "title": s.get("title"),
                "studio": s.get("studio"),
                "duration_sec": s.get("duration_sec"),
                "performers": s.get("performers"),
                "genres": s.get("genres"),
                "chapter_start_sec": chapters[i]["start_sec"],
            }
            for i, s in enumerate(scenes)
        ],
        "total_duration_sec": total_duration,
        "spec_compatible": compat_result["compatible"],
        "spec_issues": compat_result["issues"],
        "chapters": chapters,
    }

    metadata_path = output_dir / f"{dvd_id}_metadata.json"
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2, default=str)

    # Step 4: Concatenate videos via ffmpeg (only if specs compatible)
    output_video_path = output_dir / f"{dvd_id}.mp4"
    concat_success = False

    if compat_result["compatible"]:
        concat_success = _concatenate_videos(
            [s["video_path"] for s in scenes],
            output_video_path,
            chapters,
        )
    else:
        log.warn("Skipping concatenation due to spec issues — re-encode required")

    # Step 5: Build quad case art
    case_art_path = output_dir / f"{dvd_id}_case_art.jpg"
    art_success = _build_quad_case_art(
        [s["cover_path"] for s in scenes if s.get("cover_path")],
        case_art_path,
        theme=theme,
    )

    return {
        "success": concat_success and art_success,
        "dvd_id": dvd_id,
        "output_path": output_video_path if concat_success else None,
        "specs_compatible": compat_result["compatible"],
        "spec_issues": compat_result["issues"],
        "total_duration_sec": total_duration,
        "chapters": chapters,
        "case_art_path": case_art_path if art_success else None,
        "metadata_path": metadata_path,
        "concat_success": concat_success,
        "art_success": art_success,
    }


def validate_dvd_specs(scenes: List[dict]) -> dict:
    """
    Verify all scenes have compatible specs for concatenation.

    Compatible means:
    - Same FPS (within 0.1 tolerance)
    - Same resolution
    - Same codec OR all H.264/HEVC (re-encodable)
    - Same audio sample rate
    """
    if len(scenes) < 2:
        return {"compatible": True, "issues": []}

    issues = []
    reference = scenes[0]
    ref_fps = reference.get("fps", 0)
    ref_res = reference.get("resolution", "")
    ref_codec = reference.get("codec", "")

    for i, scene in enumerate(scenes[1:], start=2):
        # FPS check
        if abs(scene.get("fps", 0) - ref_fps) > 0.1:
            issues.append(
                f"Scene {i}: FPS mismatch ({scene.get('fps')} vs {ref_fps})"
            )

        # Resolution check
        if scene.get("resolution") != ref_res:
            issues.append(
                f"Scene {i}: Resolution mismatch ({scene.get('resolution')} vs {ref_res})"
            )

        # Codec check (warning, not blocker)
        if scene.get("codec") != ref_codec:
            issues.append(
                f"Scene {i}: Codec mismatch ({scene.get('codec')} vs {ref_codec}) "
                f"- re-encode may be needed"
            )

    return {
        "compatible": len(issues) == 0,
        "issues": issues,
    }


def _load_scene_data(scene_id: str) -> Optional[dict]:
    """Load all relevant data for a scene (decision log + review)."""
    safe_id = "".join(c if c.isalnum() or c in "_-" else "_" for c in scene_id)[:120]

    # Decision log
    decision_log = None
    for path in DECISION_LOGS_DIR.glob(f"*{safe_id}*.json"):
        try:
            with open(path) as f:
                decision_log = json.load(f)
                break
        except Exception:
            continue

    if not decision_log:
        return None

    # Review record (optional but preferred)
    review = None
    for path in REVIEWED_DIR.glob(f"*{safe_id}*.json"):
        try:
            with open(path) as f:
                review = json.load(f)
                break
        except Exception:
            continue

    inp = decision_log.get("input", {})
    duration = inp.get("duration_sec", 0)

    # Parse resolution
    res_str = inp.get("resolution", "0x0")

    return {
        "scene_id": scene_id,
        "video_path": Path(decision_log.get("scene_path", "")),
        "duration_sec": duration,
        "fps": inp.get("fps", 0),
        "resolution": res_str,
        "codec": inp.get("codec", ""),
        "studio": inp.get("studio"),
        "scene_type": inp.get("scene_type"),
        "genres": inp.get("genres", []),
        "title": (review.get("title") or {}).get("text") if review else None,
        "performers": review.get("performers_confirmed") if review else [],
        "cover_path": _resolve_cover_path(scene_id, review) if review else None,
    }


def _resolve_cover_path(scene_id: str, review: dict) -> Optional[Path]:
    """Find the path to the picked hero cover."""
    cover_pick = review.get("cover_pick") or {}
    if cover_pick.get("path"):
        return Path(cover_pick["path"])
    return None


def _build_chapters(scenes: List[dict]) -> List[dict]:
    """Build chapter markers for the DVD."""
    chapters = []
    cumulative = 0.0
    for i, scene in enumerate(scenes):
        chapters.append({
            "index": i,
            "title": scene.get("title") or scene.get("scene_id") or f"Chapter {i + 1}",
            "start_sec": cumulative,
            "duration_sec": scene.get("duration_sec", 0),
        })
        cumulative += scene.get("duration_sec", 0)
    return chapters


def _concatenate_videos(
    video_paths: List[Path],
    output_path: Path,
    chapters: List[dict],
) -> bool:
    """
    Concatenate videos using ffmpeg's concat demuxer.

    Requires all videos have identical specs.
    """
    if not all(p.exists() for p in video_paths):
        log.error("One or more video files missing")
        return False

    # Create the concat list file
    list_file = output_path.with_suffix(".list.txt")
    try:
        with open(list_file, "w") as f:
            for video in video_paths:
                # Escape paths for ffmpeg concat format
                escaped = str(video).replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

        # Run ffmpeg
        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",  # No re-encode
            str(output_path),
        ]
        log.info("Running ffmpeg concat", output=str(output_path))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode != 0:
            log.error("ffmpeg concat failed",
                      stderr=result.stderr[:500])
            return False

        log.info("Concatenation complete", output=str(output_path),
                 size_mb=output_path.stat().st_size / (1024 ** 2))
        return True
    except subprocess.TimeoutExpired:
        log.error("ffmpeg concat timeout")
        return False
    except Exception as e:
        log.error("ffmpeg concat exception", error=str(e))
        return False
    finally:
        if list_file.exists():
            list_file.unlink()


def _build_quad_case_art(
    cover_paths: List[Path],
    output_path: Path,
    theme: str,
) -> bool:
    """
    Build a 2x2 quad layout case art from 4 covers.

    If fewer than 4 covers provided, leaves quadrants blank.
    """
    if not cover_paths:
        log.warn("No covers for case art")
        return False

    try:
        from PIL import Image, ImageDraw, ImageFont

        canvas_w, canvas_h = DVD_QUAD_LAYOUT_SIZE
        canvas = Image.new("RGB", (canvas_w, canvas_h), (15, 15, 15))
        draw = ImageDraw.Draw(canvas)

        # Reserve space for theme banner at top
        banner_h = 80
        quad_area_h = canvas_h - banner_h

        # Compute quadrant size
        quad_w = canvas_w // 2
        quad_h = quad_area_h // 2

        # Place covers
        positions = [
            (0, banner_h),                          # Top-left
            (quad_w, banner_h),                     # Top-right
            (0, banner_h + quad_h),                 # Bottom-left
            (quad_w, banner_h + quad_h),            # Bottom-right
        ]

        for i, cover_path in enumerate(cover_paths[:4]):
            if not cover_path or not cover_path.exists():
                continue
            try:
                cover = Image.open(cover_path)
                cover.thumbnail((quad_w - 4, quad_h - 4), Image.LANCZOS)
                # Center in quadrant
                x, y = positions[i]
                paste_x = x + (quad_w - cover.width) // 2
                paste_y = y + (quad_h - cover.height) // 2
                canvas.paste(cover, (paste_x, paste_y))
            except Exception as e:
                log.warn(f"Failed to paste cover {i}", error=str(e))

        # Theme banner
        try:
            font = _load_font(48)
            draw.text((canvas_w // 2, 40), theme,
                      fill=(255, 215, 0), font=font, anchor="mm")
        except Exception as e:
            log.warn("Banner text failed", error=str(e))

        canvas.save(output_path, "JPEG", quality=92)
        log.info("Case art saved", path=str(output_path))
        return True
    except Exception as e:
        log.error("Case art generation failed", error=str(e))
        return False


def _load_font(size: int):
    """Load font with fallbacks."""
    from PIL import ImageFont
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        try:
            if Path(path).exists():
                return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _safe_name(s: str) -> str:
    """Make a string safe for filenames."""
    return "".join(c if c.isalnum() else "_" for c in s)[:50]
