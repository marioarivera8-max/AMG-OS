"""Short preview clip generation.

Preview generation is operator-facing only: it creates local artifacts for
review and never publishes them anywhere.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from amg.config import (
    PREVIEW_CLIP_DURATION_SEC,
    PREVIEW_CLIP_COUNT,
    PREVIEW_FFMPEG_BIN,
    PREVIEW_GIF_ENABLED,
)
from amg.utils.logging import get_logger

log = get_logger("output.previews")


def generate_preview_outputs(
    *,
    video_path: Path,
    work_dir: Path,
    analysis: Optional[dict],
    clip_count: int = PREVIEW_CLIP_COUNT,
    clip_duration_sec: float = PREVIEW_CLIP_DURATION_SEC,
    gif_enabled: bool = PREVIEW_GIF_ENABLED,
    ffmpeg_bin: str = PREVIEW_FFMPEG_BIN,
    timeout_sec: int = 90,
) -> Dict[str, Any]:
    """Generate MP4 preview clips and optional GIFs.

    Returns a manifest dict and writes ``previews/preview_manifest.json``. All
    failures are captured in the manifest; callers should not treat a failed
    preview as a pipeline failure.
    """
    work_dir = Path(work_dir)
    preview_dir = work_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)

    manifest: Dict[str, Any] = {
        "enabled": True,
        "video_path": str(video_path),
        "clip_count_requested": int(max(0, clip_count)),
        "clip_duration_sec": float(max(1.0, clip_duration_sec)),
        "gif_enabled": bool(gif_enabled),
        "outputs": [],
        "skipped": [],
        "errors": [],
    }

    ffmpeg = _resolve_ffmpeg(ffmpeg_bin)
    if not ffmpeg:
        manifest["errors"].append({"stage": "probe", "error": "ffmpeg_not_found"})
        _write_manifest(preview_dir, manifest)
        return manifest

    timestamps = _select_preview_timestamps(analysis or {}, max_count=max(0, int(clip_count)))
    if not timestamps:
        manifest["skipped"].append({"reason": "no_preview_timestamps"})
        _write_manifest(preview_dir, manifest)
        return manifest

    duration = float(max(1.0, clip_duration_sec))
    for idx, ts in enumerate(timestamps, start=1):
        out_mp4 = preview_dir / f"preview_{idx:02d}.mp4"
        start = max(0.0, float(ts) - duration * 0.35)
        row: Dict[str, Any] = {
            "index": idx,
            "timestamp_sec": round(float(ts), 3),
            "start_sec": round(start, 3),
            "duration_sec": round(duration, 3),
            "path": str(out_mp4),
            "status": "pending",
        }
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(video_path),
            "-vf",
            "scale=1280:-2:force_original_aspect_ratio=decrease",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-movflags",
            "+faststart",
            str(out_mp4),
        ]
        ok, err = _run(cmd, timeout_sec=timeout_sec)
        if ok and out_mp4.is_file() and out_mp4.stat().st_size > 0:
            row["status"] = "ok"
            row["bytes"] = out_mp4.stat().st_size
        else:
            row["status"] = "failed"
            row["error"] = err or "mp4_missing"
            manifest["outputs"].append(row)
            continue

        if gif_enabled:
            out_gif = preview_dir / f"preview_{idx:02d}.gif"
            gif_cmd = [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{min(duration, 5.0):.3f}",
                "-i",
                str(video_path),
                "-vf",
                "fps=10,scale=480:-1:flags=lanczos",
                str(out_gif),
            ]
            gif_ok, gif_err = _run(gif_cmd, timeout_sec=max(20, timeout_sec // 2))
            if gif_ok and out_gif.is_file() and out_gif.stat().st_size > 0:
                row["gif_path"] = str(out_gif)
                row["gif_bytes"] = out_gif.stat().st_size
            else:
                row["gif_error"] = gif_err or "gif_missing"
        manifest["outputs"].append(row)

    _write_manifest(preview_dir, manifest)
    log.info(
        "Preview generation complete",
        outputs=sum(1 for r in manifest["outputs"] if r.get("status") == "ok"),
        errors=len(manifest["errors"]),
    )
    return manifest


def _select_preview_timestamps(analysis: dict, *, max_count: int) -> List[float]:
    values: List[float] = []

    # Prefer distinct semantic sections.
    for sec in analysis.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        if _section_has_policy_flag(sec, analysis):
            continue
        _append_distinct(values, sec.get("thumbnail_timestamp"))
        if len(values) >= max_count:
            return values

    # Fall back to saved cover moments.
    for moment in analysis.get("thumbnail_moments") or []:
        if not isinstance(moment, dict):
            continue
        _append_distinct(values, moment.get("timestamp_sec"))
        if len(values) >= max_count:
            return values

    return values


def _section_has_policy_flag(section: dict, analysis: dict) -> bool:
    evidence_ids = set(section.get("evidence_ids") or [])
    if not evidence_ids:
        return False
    for flag in analysis.get("policy_flags") or []:
        if isinstance(flag, dict) and flag.get("evidence_id") in evidence_ids:
            return True
    return False


def _append_distinct(values: List[float], raw: Any) -> None:
    try:
        ts = float(raw)
    except (TypeError, ValueError):
        return
    if ts < 0:
        return
    if any(abs(ts - existing) < 20.0 for existing in values):
        return
    values.append(ts)


def _resolve_ffmpeg(ffmpeg_bin: str) -> Optional[str]:
    direct = Path(str(ffmpeg_bin or ""))
    if direct.is_file():
        return str(direct)
    return shutil.which(str(ffmpeg_bin or "ffmpeg"))


def _run(cmd: List[str], *, timeout_sec: int) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or f"exit={proc.returncode}")[:500]
    return True, ""


def _write_manifest(preview_dir: Path, manifest: Dict[str, Any]) -> None:
    try:
        with (preview_dir / "preview_manifest.json").open("w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
    except Exception as exc:  # noqa: BLE001
        log.warn("Failed to write preview manifest", error=str(exc))
