from __future__ import annotations

import json
import os
import re
import shutil
import socket
import tempfile
import threading
import time
import uuid
import zipfile
from collections import deque
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote_plus

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from amg.config import (
    DATA_DIR,
    DECISION_LOGS_DIR,
    REVIEWED_DIR,
    OPERATOR_FEEDBACK_DIR,
    OPERATOR_FEEDBACK_PATH,
    VISION_MODEL,
    TITLE_TONE_DEFAULT,
    PLATFORM_REQUIREMENTS,
)
from amg.ingest.inventory import VIDEO_EXTENSIONS, discover_scenes
from amg.learning.feedback_eval import evaluate_feedback, load_feedback_rows
from amg.learning.example_bank import export_approved_example_bank
from amg.scoring.insight_pipeline import generate_scene_insight_payload
from amg.review.distribution_gate import validate_metadata_for_platforms
from amg.utils.logging import get_logger
from amg.cloud.job_backend import get_backend
from amg.ui.auth import get_current_user, install_auth, is_auth_enabled
from amg.video.metadata import get_metadata

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Templates can call ``current_user(request)`` to render the topbar avatar
# and the logout button without every route having to thread the user dict
# through its TemplateResponse context.
templates.env.globals["current_user"] = get_current_user
templates.env.globals["auth_enabled"] = is_auth_enabled
UPLOADS_DIR = DATA_DIR / "ui_uploads"
RUN_LOGS_DIR = DATA_DIR / "logs" / "runs"
RUN_TIMINGS_PATH = DATA_DIR / "logs" / "run_timings.jsonl"
RERUN_SOURCES_PATH = DATA_DIR / "rerun_sources.json"

# Canonical pipeline phases (used to render phase pills + progress %).
PHASES: List[str] = [
    "ingest",
    "calibration",
    "tier_scan",
    "finish_hunter",
    "buildup_hunter",
    "cluster",
    "floor_enforcement",
    "position_classifier",
    "quota_fill",
    "output",
]
_PHASE_RE = re.compile(r"\[(" + "|".join(PHASES) + r")\]")

_jobs_lock = threading.Lock()
_jobs: Dict[str, dict] = {}
_job_fifo: List[str] = []
_job_seq_counter: int = 0
_dispatcher_thread: Optional[threading.Thread] = None
_backend_lock = threading.Lock()
_backend_instance = None

# Cached health snapshot (refreshed lazily; cheap probes).
_health_lock = threading.Lock()
_health_cache: dict = {}
_health_ts: float = 0.0
log = get_logger("ui.app")


# ---------- helpers ----------

def _safe_scene_id(scene_id: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in scene_id)[:120]


def _debug_log_dbg_mode(run_id: str, hypothesis_id: str, location: str, message: str, data: dict) -> None:
    try:
        row = {
            "sessionId": "a40662",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open("/data/debug-a40662.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")
    except Exception:
        pass


def _build_scene_id_from_video(video_path: Path) -> str:
    """
    Build a stable, human-readable scene id per video (not per folder).
    """
    parent = (video_path.parent.name or "").strip()
    stem = (video_path.stem or "").strip()
    if parent and stem and parent.lower() != stem.lower():
        return _safe_scene_id(f"{parent}_{stem}")
    if stem:
        return _safe_scene_id(stem)
    if parent:
        return _safe_scene_id(parent)
    return _safe_scene_id(video_path.name or "scene")


def _build_cloud_scene_id(
    *,
    one_path: str,
    download_root: Optional[str],
    relative_path: Optional[str],
    explicit_scene_id: str,
) -> str:
    """
    Build a deterministic scene id for one cloud-selected video.
    """
    if explicit_scene_id.strip():
        return _safe_scene_id(explicit_scene_id.strip())
    if relative_path:
        rel = Path(relative_path.strip().strip("/"))
        base = rel.with_suffix("").as_posix().replace("/", "_")
    else:
        base = Path(one_path).stem
    root_name = Path(download_root).name if download_root else Path(one_path).parent.name
    if root_name and base and root_name.lower() not in base.lower():
        return _safe_scene_id(f"{root_name}_{base}")
    if base:
        return _safe_scene_id(base)
    if root_name:
        return _safe_scene_id(root_name)
    return _safe_scene_id(Path(one_path).name or "scene")


def _utc_now_isoz() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _grad_idx(s: str) -> int:
    return sum(ord(c) for c in (s or "")) % 8


def _resolve_video_path(user_path: Path) -> Optional[Path]:
    path = user_path.expanduser().resolve()
    if not path.exists():
        return None
    if path.is_file():
        return path
    # Folder mode: choose the longest playable video, not the first filename.
    videos = discover_scenes(path, recursive=True)
    if not videos:
        return None
    return _pick_longest_video(videos)


def _pick_longest_video(videos: List[Path]) -> Optional[Path]:
    """
    Pick the longest video by ffprobe duration; fallback to largest file size.
    """
    best_path = None
    best_duration = -1.0
    best_size = -1
    for p in videos:
        dur = -1.0
        try:
            meta = get_metadata(p)
            if meta:
                dur = float(meta.get("duration_sec", 0) or 0)
        except Exception:
            dur = -1.0
        try:
            size = p.stat().st_size
        except Exception:
            size = -1
        if dur > best_duration or (dur == best_duration and size > best_size):
            best_duration = dur
            best_size = size
            best_path = p
    return best_path


def _sanitize_upload_relpath(raw_name: str) -> Path:
    """
    Sanitize browser-provided upload filenames/relative paths.
    """
    raw = (raw_name or "upload.bin").replace("\\", "/")
    parts = [p for p in raw.split("/") if p not in ("", ".", "..")]
    safe_parts = []
    for p in parts:
        cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", p)[:120]
        safe_parts.append(cleaned or "unnamed")
    return Path(*safe_parts) if safe_parts else Path("upload.bin")


def _dedupe_target_path(path: Path) -> Path:
    """
    Avoid overwrite when multiple uploaded files share the same name/path.
    """
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for i in range(2, 1000):
        candidate = parent / f"{stem}__{i}{suffix}"
        if not candidate.exists():
            return candidate
    # Extreme fallback.
    return parent / f"{stem}__{uuid.uuid4().hex[:8]}{suffix}"


def _batch_label_from_relpaths(rel_paths: List[Path]) -> str:
    tops = [p.parts[0] for p in rel_paths if len(p.parts) >= 2]
    if tops and all(t == tops[0] for t in tops):
        label = tops[0]
    elif rel_paths:
        label = rel_paths[0].stem
    else:
        label = "upload_batch"
    return re.sub(r"[^A-Za-z0-9._-]", "_", label)[:60] or "upload_batch"


async def _save_upload_batch_and_select_video(upload_files: List[UploadFile]) -> tuple[Optional[Path], dict]:
    """
    Save all uploaded files (folder-safe), then choose the longest video.

    Returns:
      (selected_video_path_or_none, stats)
    """
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    rel_paths = [_sanitize_upload_relpath(u.filename or "") for u in upload_files]
    label = _batch_label_from_relpaths(rel_paths)
    batch_dir = UPLOADS_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{label}"
    batch_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: List[Path] = []
    total_bytes = 0
    for upload, rel in zip(upload_files, rel_paths):
        target = _dedupe_target_path(batch_dir / rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as out:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                total_bytes += len(chunk)
        saved_paths.append(target)

    video_candidates = []
    for p in saved_paths:
        if p.suffix.lower() in VIDEO_EXTENSIONS:
            try:
                if p.stat().st_size >= 1024 * 1024:
                    video_candidates.append(p)
            except Exception:
                continue

    chosen = _pick_longest_video(video_candidates) if video_candidates else None
    stats = {
        "batch_dir": str(batch_dir),
        "files_uploaded": len(saved_paths),
        "videos_found": len(video_candidates),
        "bytes_uploaded": total_bytes,
        "chosen_video": str(chosen) if chosen else None,
    }
    return chosen, stats


def _load_decision_log(scene_id: str) -> Optional[dict]:
    sid = _safe_scene_id(scene_id)
    path = DECISION_LOGS_DIR / f"{sid}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        log.warn("Failed to read decision log", scene_id=scene_id, path=str(path), error=str(e))
        return None


def _load_reviewed(scene_id: str) -> Optional[dict]:
    sid = _safe_scene_id(scene_id)
    path = REVIEWED_DIR / f"{sid}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _build_review_form_state(
    *,
    reviewed: Optional[dict],
    insight: Optional[dict],
    cover_items: Optional[List[dict]] = None,
) -> dict:
    """
    Build prefilled UI form state from reviewed payload + insight defaults.
    """
    reviewed = reviewed if isinstance(reviewed, dict) else {}
    insight = insight if isinstance(insight, dict) else {}
    per_cover_raw = reviewed.get("per_cover")
    per_cover = per_cover_raw if isinstance(per_cover_raw, dict) else {}
    selected_set = set(str(x) for x in (reviewed.get("selected_covers") or []) if isinstance(x, str))
    kept_set = set(str(x) for x in (reviewed.get("kept_covers") or []) if isinstance(x, str))

    def _str_or_empty(v) -> str:
        if v is None:
            return ""
        return str(v)

    form_state = {
        "title": _str_or_empty(reviewed.get("title_override") or ""),
        "title_tone": _str_or_empty(reviewed.get("title_tone") or insight.get("title_tone") or TITLE_TONE_DEFAULT),
        "long_description": _str_or_empty(reviewed.get("long_description") or insight.get("long_description") or ""),
        "notes": _str_or_empty(reviewed.get("notes") or ""),
        "tags_csv": _str_or_empty(reviewed.get("tags_csv") or ", ".join(insight.get("ai_tags") or [])),
        "categories_csv": _str_or_empty(reviewed.get("categories_csv") or ", ".join(insight.get("ai_categories") or [])),
        "target_platforms": [
            str(p).upper().strip()
            for p in (reviewed.get("target_platforms") or list(PLATFORM_REQUIREMENTS.keys()))
            if str(p).strip()
        ],
        "soft_thumb_decision": _str_or_empty(((reviewed.get("soft_thumbnail_review") or {}).get("decision")) or ""),
        "soft_thumb_score": (
            _str_or_empty((reviewed.get("soft_thumbnail_review") or {}).get("score_100"))
            if (reviewed.get("soft_thumbnail_review") or {}).get("score_100") is not None
            else ""
        ),
        "soft_thumb_reason": _str_or_empty(((reviewed.get("soft_thumbnail_review") or {}).get("reason")) or ""),
        "per_cover": {},
    }

    if cover_items:
        for item in cover_items:
            fn = item.get("filename")
            if not fn:
                continue
            row = per_cover.get(fn) if isinstance(per_cover.get(fn), dict) else {}
            decision_default = ""
            if fn in kept_set:
                decision_default = "keep"
            elif fn in selected_set:
                decision_default = "maybe"
            form_state["per_cover"][fn] = {
                "decision": _str_or_empty(row.get("decision") or decision_default),
                "pen": _str_or_empty(row.get("pen") or ""),
                "pos": _str_or_empty(row.get("pos") or ""),
                "reason": _str_or_empty(row.get("reason") or ""),
                "score": _str_or_empty(row.get("score") if row.get("score") is not None else ""),
            }
    return form_state


def _build_kept_covers_package(
    *,
    scene_id: str,
    work_dir: Optional[Path],
    cover_items: List[dict],
    kept_filenames: List[str],
) -> dict:
    """
    Materialize reviewed keep-picks into a top-level folder + one-click zip.

    Returns:
      {
        "kept_count": int,
        "kept_folder_path": Optional[Path],
        "kept_zip_path": Optional[Path],
      }
    """
    out = {"kept_count": 0, "kept_folder_path": None, "kept_zip_path": None}
    if not work_dir or not kept_filenames:
        return out

    by_name = {
        str(item.get("filename", "")): Path(item.get("path"))
        for item in (cover_items or [])
        if item.get("filename") and item.get("path")
    }
    covers_dir = Path(work_dir) / "covers"
    unique_kept = []
    seen = set()
    for name in kept_filenames:
        key = str(name or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique_kept.append(key)

    kept_sources: List[Path] = []
    for name in unique_kept:
        src = by_name.get(name)
        if src and src.exists():
            kept_sources.append(src)
            continue
        # Fallback for cloud logs where mapping may be stale/missing.
        cand = covers_dir / name
        if cand.exists():
            kept_sources.append(cand)
    if not kept_sources:
        return out

    kept_folder = Path(work_dir) / "00_kept_for_publish"
    if kept_folder.exists():
        shutil.rmtree(kept_folder, ignore_errors=True)
    kept_folder.mkdir(parents=True, exist_ok=True)

    copied_paths = []
    for idx, src in enumerate(kept_sources, start=1):
        dst_name = f"{idx:02d}_{src.name}"
        dst = kept_folder / dst_name
        try:
            shutil.copy2(src, dst)
            copied_paths.append(dst)
        except Exception as e:
            log.warn("Failed to copy kept cover", scene_id=scene_id, src=str(src), error=str(e))

    if not copied_paths:
        return out

    zip_path = Path(work_dir) / "00_kept_for_publish.zip"
    try:
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in copied_paths:
                zf.write(p, arcname=p.name)
    except Exception as e:
        log.warn("Failed to create kept covers zip", scene_id=scene_id, path=str(zip_path), error=str(e))
        zip_path = None

    out["kept_count"] = len(copied_paths)
    out["kept_folder_path"] = kept_folder
    out["kept_zip_path"] = zip_path
    return out


def _all_decision_logs() -> List[dict]:
    if not DECISION_LOGS_DIR.exists():
        return []
    out = []
    for p in sorted(DECISION_LOGS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            with open(p) as f:
                out.append(json.load(f))
        except Exception as e:
            log.warn("Skipping unreadable decision log", path=str(p), error=str(e))
            continue
    return out


def _persist_review_to_decision_log(
    *,
    scene_id: str,
    title_tone: str,
    title_override: Optional[str],
    long_description: Optional[str],
    selected_covers: List[str],
    kept_covers: Optional[List[str]] = None,
    finalized_thumbnails: bool = False,
    per_cover: Optional[dict] = None,
    tags_csv: Optional[str] = None,
    categories_csv: Optional[str] = None,
    soft_thumbnail_review: Optional[dict] = None,
    rule_pack_id: Optional[str] = None,
    rule_pack_applied: Optional[bool] = None,
    rule_pack_mode: Optional[str] = None,
    metadata_learning_signals: Optional[dict] = None,
) -> None:
    """
    Persist review choices back into decision log for downstream learning.

    Resilience: if the decision log doesn't exist (e.g. cloud pull-back
    failed), we still write a stub log carrying the review block so the
    operator's feedback isn't silently dropped. The stub gets overwritten
    if the real log lands later.
    """
    sid = _safe_scene_id(scene_id)
    path = DECISION_LOGS_DIR / f"{sid}.json"
    DECISION_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    dlog: dict = {}
    if path.exists():
        try:
            with open(path, "r") as f:
                dlog = json.load(f)
                if not isinstance(dlog, dict):
                    dlog = {}
        except Exception as e:
            log.warn("Failed to read decision log for review persistence", error=str(e), scene_id=scene_id)
            dlog = {}
    if not dlog:
        dlog = {
            "scene_id": scene_id,
            "review_only_stub": True,
            "stub_reason": "decision_log not present at review time (cloud pull-back may have failed)",
        }

    review = dlog.get("review") if isinstance(dlog.get("review"), dict) else {}
    metadata_learning_signals = (
        metadata_learning_signals if isinstance(metadata_learning_signals, dict) else {}
    )
    review.update(
        {
            "timestamp": _utc_now_isoz(),
            "title_tone_selected": title_tone,
            "title_override": title_override or None,
            "long_description": long_description or None,
            "selected_covers": selected_covers,
            "kept_covers": kept_covers or [],
            "finalized_thumbnails": bool(finalized_thumbnails),
            "per_cover": per_cover or {},
            "tags_csv": tags_csv or None,
            "categories_csv": categories_csv or None,
            "soft_thumbnail_review": soft_thumbnail_review or None,
            "rule_pack_id": rule_pack_id or None,
            "rule_pack_applied": bool(rule_pack_applied) if rule_pack_applied is not None else None,
            "rule_pack_mode": rule_pack_mode or None,
            "title_edit_distance": metadata_learning_signals.get("title_edit_distance"),
            "description_edit_distance": metadata_learning_signals.get("description_edit_distance"),
            "tags_edit_distance": metadata_learning_signals.get("tags_edit_distance"),
            "categories_edit_distance": metadata_learning_signals.get("categories_edit_distance"),
            "metadata_acceptance_label": metadata_learning_signals.get("metadata_acceptance_label"),
            "metadata_edit_reason_codes": metadata_learning_signals.get("metadata_edit_reason_codes") or [],
        }
    )
    dlog["review"] = review

    try:
        with open(path, "w") as f:
            json.dump(dlog, f, indent=2)
    except Exception:
        return


def _humanize_ago(iso_ts: str) -> str:
    if not iso_ts:
        return ""
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - ts
    except Exception:
        return iso_ts
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def _humanize_duration(secs: float) -> str:
    if not secs or secs <= 0:
        return ""
    s = int(secs)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def _safe_float(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _scene_status(d: dict) -> tuple[str, str]:
    """Return (label, css_class) for a decision-log scene."""
    sid = _safe_scene_id(d.get("scene_id", ""))
    reviewed = REVIEWED_DIR / f"{sid}.json"
    covers = (d.get("outcomes") or {}).get("covers_delivered", 0)

    if reviewed.exists():
        return "REVIEWED", "ok"
    if covers and covers >= 6:
        return "REVIEW", "warn"
    if covers and covers > 0:
        return "DRAFT", "neutral"
    return "FAILED", "err"


def _scene_summary(d: dict) -> dict:
    out = (d.get("outcomes") or {})
    saved_covers = out.get("saved_covers") or []
    sid = d.get("scene_id") or ""
    work_dir = _resolve_scene_work_dir(sid, d)
    insight = _load_insight(work_dir)
    preview_path: Optional[Path] = None
    if saved_covers:
        raw_path = (saved_covers[0] or {}).get("path")
        if raw_path:
            p = Path(raw_path)
            if p.is_file():
                preview_path = p
    if preview_path is None:
        # Cloud runs often persist pod-local cover paths in decision logs.
        # Re-resolve against the controller's extracted work dir so library
        # cards still render thumbnails.
        covers_dir = (work_dir / "covers") if work_dir else None
        if covers_dir and covers_dir.is_dir():
            preferred = None
            if saved_covers:
                preferred_name = (saved_covers[0] or {}).get("filename")
                if preferred_name:
                    cand = covers_dir / str(preferred_name)
                    if cand.is_file():
                        preferred = cand
            preview_path = preferred or next(iter(sorted(covers_dir.glob("*.jpg"))), None)
    status, status_cls = _scene_status(d)
    duration = (d.get("input") or {}).get("duration_sec") or 0
    input_blob = d.get("input") or {}
    performers = input_blob.get("performers") or []
    if not isinstance(performers, list):
        performers = []
    title = (
        input_blob.get("title")
        or input_blob.get("scene_title")
        or d.get("scene_title")
        or d.get("scene_name")
        or ""
    )
    return {
        "scene_id": sid,
        "studio": (d.get("input") or {}).get("studio") or "",
        "covers": out.get("covers_delivered", 0),
        "top_score": _normalize_score_for_ui(out.get("top_pick_score")),
        "duration_sec": duration,
        "duration_str": _humanize_duration(duration),
        "tags": (d.get("input") or {}).get("genres") or [],
        "timestamp": d.get("timestamp_processed") or "",
        "processed_ago": _humanize_ago(d.get("timestamp_processed") or ""),
        "preview_path": preview_path,
        "grad_idx": _grad_idx(sid),
        "status": status,
        "status_cls": status_cls,
        "title": title,
        "performers": performers,
        "rule_pack_id": (insight or {}).get("rule_pack_id"),
        "rule_pack_mode": (insight or {}).get("rule_pack_mode"),
        "text_model_fallback_used": bool((insight or {}).get("text_model_fallback_used")),
    }


def _recent_scenes(limit: int = 20) -> list[dict]:
    return [_scene_summary(d) for d in _all_decision_logs()[:limit]]


def _find_work_dir(scene_id: str) -> Optional[Path]:
    from amg.config import INCOMING_ROOTS

    # Cloud edition: RunpodBackend extracts pod artifacts to
    # DATA_DIR/work_dirs/<scene_id>/ (covers/, contact sheet, insight.json
    # all directly underneath). Check there before falling back to the
    # legacy v11 layout.
    sid = _safe_scene_id(scene_id)
    cloud_extracted = DATA_DIR / "work_dirs" / sid
    if cloud_extracted.is_dir():
        return cloud_extracted

    roots = list(INCOMING_ROOTS) + [UPLOADS_DIR]
    pattern = f"{scene_id}_amg_v11"
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob(pattern):
            if p.is_dir():
                return p
        for p in root.rglob(f"*{scene_id[:40]}*_amg_v11*"):
            if p.is_dir():
                return p
    return None


def _work_dir_from_decision_log(decision_log: Optional[dict]) -> Optional[Path]:
    if not decision_log:
        return None
    scene_path = decision_log.get("scene_path")
    if not scene_path:
        return None
    try:
        video_path = Path(scene_path).expanduser().resolve()
    except Exception:
        return None
    return video_path.parent / f"{video_path.stem}_amg_v11"


def _resolve_scene_work_dir(scene_id: str, decision_log: Optional[dict]) -> Optional[Path]:
    """Pick a usable scene work dir for UI actions.

    Decision logs from pod-side runs often carry pod-local scene paths
    under /data/pod_uploads/... which do not exist on the controller.
    Always prefer an existing path; fall back to controller-extracted
    /data/work_dirs/<safe_scene_id>/ when needed.
    """
    dlog_work_dir = _work_dir_from_decision_log(decision_log)
    if dlog_work_dir and dlog_work_dir.is_dir():
        return dlog_work_dir
    fallback = _find_work_dir(scene_id)
    if fallback and fallback.is_dir():
        return fallback
    return dlog_work_dir or fallback


def _artifact_allowed_roots() -> List[Path]:
    """
    Limit file-serving to AMG data + configured incoming roots.
    """
    from amg.config import INCOMING_ROOTS

    roots = [DATA_DIR.resolve(), UPLOADS_DIR.resolve()]
    for root in INCOMING_ROOTS:
        try:
            roots.append(Path(root).expanduser().resolve())
        except Exception:
            continue
    unique = []
    seen = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _path_within_roots(path: Path, roots: List[Path]) -> bool:
    for root in roots:
        if path == root or root in path.parents:
            return True
    return False


def _load_insight(work_dir: Optional[Path]) -> Optional[dict]:
    if not work_dir:
        return None
    p = Path(work_dir) / "insight.json"
    if not p.exists():
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def _load_provided_thumb_report(work_dir: Optional[Path]) -> Optional[dict]:
    if not work_dir:
        return None
    p = Path(work_dir) / "provided_thumbnails.json"
    if not p.exists():
        return None
    try:
        with open(p) as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    rows = data.get("rows") or []
    if not isinstance(rows, list):
        rows = []
    accepted_rows = [r for r in rows if isinstance(r, dict) and r.get("accepted")]
    rejected_rows = [r for r in rows if isinstance(r, dict) and not r.get("accepted")]
    data["accepted_rows"] = accepted_rows
    data["rejected_rows"] = rejected_rows
    return data


def _load_soft_thumbnail(work_dir: Optional[Path]) -> Optional[dict]:
    if not work_dir:
        return None
    p = Path(work_dir) / "00_soft_thumbnail.jpg"
    if not p.exists():
        return None
    sidecar = Path(work_dir) / "soft_thumbnail.json"
    score = None
    timestamp_sec = None
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text())
            rows = data.get("rows") or []
            if isinstance(rows, list):
                # Keep best row only for summary.
                best = None
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    s = r.get("score")
                    if s is None:
                        continue
                    if best is None or float(s) > float(best.get("score", 0)):
                        best = r
                if best:
                    score = best.get("score")
                    timestamp_sec = best.get("timestamp_sec")
        except Exception:
            pass
    return {"path": p, "score": score, "timestamp_sec": timestamp_sec}


def _regenerate_insight_for_scene(decision_log: dict, work_dir: Path, title_tone: str = TITLE_TONE_DEFAULT) -> dict:
    """Run scene insight + AI titles for an already-processed scene and write
    the result back to ``insight.json``. Returns the merged dict."""
    scene_path = decision_log.get("scene_path")
    if not scene_path:
        raise RuntimeError("decision_log missing scene_path")
    video_path = Path(scene_path)

    saved_covers = (decision_log.get("outcomes") or {}).get("saved_covers", []) or []
    return generate_scene_insight_payload(
        video_path=video_path,
        saved_covers=saved_covers,
        work_dir=work_dir,
        title_tone=title_tone,
        persist=True,
    )


def _cover_items(scene_id: str, decision_log: Optional[dict], work_dir: Optional[Path]) -> list[dict]:
    if not work_dir:
        return []
    covers_dir = work_dir / "covers"
    if not covers_dir.exists():
        return []

    paths = sorted(covers_dir.glob("*.jpg"))
    by_name = {}
    if decision_log:
        for c in decision_log.get("outcomes", {}).get("saved_covers", []) or []:
            fn = c.get("filename")
            if fn:
                by_name[fn] = c

    out = []
    for p in paths:
        meta = by_name.get(p.name, {})
        score = _normalize_score_for_ui(meta.get("score", None))
        out.append(
            {
                "filename": p.name,
                "path": p,
                "model_type": meta.get("type", "UNKNOWN"),
                "model_position": meta.get("position_label", "OTHER"),
                "model_position_conf": meta.get("position_label_confidence", 0.0),
                "model_pen_visible": meta.get("penetration_visible", False),
                "model_pen_conf": meta.get("penetration_confidence", 0.0),
                "score": score,
                "score_raw": meta.get("score", None),
                "timestamp_sec": meta.get("timestamp_sec", 0) or 0,
            }
        )
    return out


def _normalize_score_for_ui(raw_score: Optional[float]) -> Optional[float]:
    """
    Normalize legacy 0-10 scores to current 0-100 display scale.
    """
    if raw_score is None:
        return None
    try:
        s = float(raw_score)
    except (TypeError, ValueError):
        return None
    if 0 < s <= 10.0:
        return round(s * 10.0, 1)
    return round(s, 1)


def _parse_user_score_100(raw_score: Optional[str]) -> Optional[float]:
    if not raw_score:
        return None
    try:
        parsed = float(raw_score)
        if 0 <= parsed <= 10:
            parsed *= 10.0
        if 0 <= parsed <= 100:
            return round(parsed, 1)
    except (TypeError, ValueError):
        return None
    return None


def _append_feedback_rows(
    *,
    scene_id: str,
    cover_items: list[dict],
    soft_thumbnail: Optional[dict],
    form,
    title_override: Optional[str],
    title_tone: Optional[str],
    notes: Optional[str],
    rule_pack_id: Optional[str] = None,
    rule_pack_applied: Optional[bool] = None,
    rule_pack_mode: Optional[str] = None,
) -> int:
    OPERATOR_FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    rows_written = 0
    item_by_name = {i["filename"]: i for i in cover_items}
    ts = _utc_now_isoz()

    with open(OPERATOR_FEEDBACK_PATH, "a") as f:
        for filename, item in item_by_name.items():
            user_pen = (form.get(f"pen_{filename}") or "").strip().lower()
            user_pos = (form.get(f"pos_{filename}") or "").strip().upper()
            user_decision = (form.get(f"decision_{filename}") or "").strip().lower()
            user_reason = (form.get(f"reason_{filename}") or "").strip()
            user_score_raw = (form.get(f"score_{filename}") or "").strip()
            user_score_100 = _parse_user_score_100(user_score_raw)

            if not any([user_pen, user_pos, user_decision, user_reason, user_score_raw]):
                continue

            row = {
                "timestamp": ts,
                "scene_id": scene_id,
                "filename": filename,
                "timestamp_sec": item.get("timestamp_sec"),
                "rule_pack": {
                    "id": rule_pack_id or None,
                    "applied": bool(rule_pack_applied) if rule_pack_applied is not None else None,
                    "mode": rule_pack_mode or None,
                },
                "model": {
                    "type": item.get("model_type"),
                    "position_label": item.get("model_position"),
                    "position_confidence": item.get("model_position_conf"),
                    "penetration_visible": item.get("model_pen_visible"),
                    "penetration_confidence": item.get("model_pen_conf"),
                    "score_100": _normalize_score_for_ui(item.get("score")),
                },
                "operator": {
                    "penetration_visible": user_pen if user_pen in {"yes", "no"} else None,
                    "position_label": user_pos or None,
                    "decision": user_decision if user_decision in {"keep", "reject", "maybe"} else None,
                    "score_input": user_score_raw or None,
                    "score_100": user_score_100,
                    "reason": user_reason or None,
                    "title_override": title_override or None,
                    "title_tone": title_tone or None,
                    "notes": notes or None,
                },
            }
            f.write(json.dumps(row) + "\n")
            rows_written += 1

        # Optional feedback row for soft thumbnail auto-pick.
        soft_decision = (form.get("soft_thumb_decision") or "").strip().lower()
        soft_score_raw = (form.get("soft_thumb_score") or "").strip()
        soft_reason = (form.get("soft_thumb_reason") or "").strip()
        soft_score_100 = _parse_user_score_100(soft_score_raw)
        if soft_decision in {"keep", "reject"} or soft_score_raw or soft_reason:
            soft_row = {
                "timestamp": ts,
                "scene_id": scene_id,
                "filename": (soft_thumbnail or {}).get("path").name if (soft_thumbnail or {}).get("path") else "00_soft_thumbnail.jpg",
                "timestamp_sec": (soft_thumbnail or {}).get("timestamp_sec"),
                "rule_pack": {
                    "id": rule_pack_id or None,
                    "applied": bool(rule_pack_applied) if rule_pack_applied is not None else None,
                    "mode": rule_pack_mode or None,
                },
                "model": {
                    "type": "SOFT_THUMBNAIL",
                    "position_label": None,
                    "position_confidence": None,
                    "penetration_visible": False,
                    "penetration_confidence": None,
                    "score_100": _normalize_score_for_ui((soft_thumbnail or {}).get("score")),
                },
                "operator": {
                    "penetration_visible": None,
                    "position_label": None,
                    "decision": soft_decision if soft_decision in {"keep", "reject"} else None,
                    "score_input": soft_score_raw or None,
                    "score_100": soft_score_100,
                    "reason": soft_reason or None,
                    "title_override": title_override or None,
                    "title_tone": title_tone or None,
                    "notes": notes or None,
                    "soft_thumb_decision": soft_decision if soft_decision in {"keep", "reject"} else None,
                    "soft_thumb_score_100": soft_score_100,
                },
            }
            f.write(json.dumps(soft_row) + "\n")
            rows_written += 1
    return rows_written


# ---------- job execution + live tracking ----------

def _start_dispatcher_if_needed() -> None:
    global _dispatcher_thread
    with _jobs_lock:
        if _dispatcher_thread and _dispatcher_thread.is_alive():
            return
        _dispatcher_thread = threading.Thread(target=_dispatcher_loop, daemon=True)
        _dispatcher_thread.start()


def _get_backend_instance():
    """Return a process-wide backend instance.

    Runpod warm-pod reuse depends on backend state surviving across queued
    jobs. Constructing a fresh backend per job defeats that optimization.
    """
    global _backend_instance
    with _backend_lock:
        if _backend_instance is None:
            _backend_instance = get_backend()
        return _backend_instance


def _dispatcher_loop() -> None:
    """
    FIFO dispatcher: run one queued job at a time in submission order.
    """
    global _dispatcher_thread
    while True:
        next_job_id = None
        with _jobs_lock:
            # Prune stale ids from fifo.
            _job_fifo[:] = [jid for jid in _job_fifo if jid in _jobs]
            running_exists = any(j.get("status") == "running" for j in _jobs.values())
            if not running_exists:
                for jid in _job_fifo:
                    j = _jobs.get(jid)
                    if j and j.get("status") == "queued":
                        next_job_id = jid
                        break
            pending_exists = any(j.get("status") in {"queued", "running"} for j in _jobs.values())

        if next_job_id:
            _run_job(next_job_id)
            continue
        if not pending_exists:
            break
        time.sleep(0.35)

    with _jobs_lock:
        _dispatcher_thread = None


def _cloud_browse_error(request, remote: str, path: str, message: str):
    """Render the cloud-browser partial in error mode (rclone failure,
    unknown remote, etc.). Surfacing the rclone error verbatim is fine —
    the operator is the only one who sees this and the message guides
    debugging (e.g. "expired token, re-run rclone authorize")."""
    return templates.TemplateResponse(
        request=request,
        name="_cloud_browse.html",
        context={
            "request": request,
            "remote": remote,
            "path": path,
            "parent_path": "",
            "entries": [],
            "error_msg": message,
        },
    )


def _is_video_cloud_path(path: str) -> bool:
    return Path(path).suffix.lower() in {ext.lower() for ext in VIDEO_EXTENSIONS}


def _join_cloud_path(base: str, rel: str) -> str:
    base_clean = base.strip().strip("/")
    rel_clean = rel.strip().strip("/")
    if not base_clean:
        return rel_clean
    if not rel_clean:
        return base_clean
    if rel_clean.startswith(base_clean + "/") or rel_clean == base_clean:
        return rel_clean
    return f"{base_clean}/{rel_clean}"


def _relative_cloud_path(full_path: str, root_path: str) -> str:
    full_clean = full_path.strip().strip("/")
    root_clean = root_path.strip().strip("/")
    if not root_clean:
        return full_clean
    prefix = root_clean + "/"
    if full_clean.startswith(prefix):
        return full_clean[len(prefix):]
    return Path(full_clean).name


def _expand_cloud_selection_to_videos(
    remote: str,
    selected_items: List[dict],
) -> List[dict]:
    """
    Expand multi-select cloud items into concrete video file paths.

    Supports direct file picks plus "folder picks" (recursive listing via
    rclone lsjson). Returns rows shaped like:
      {
        "path": "folder/video.mp4",
        "download_root": "folder" | None,
        "relative_path": "sub/video.mp4" | None
      }
    """
    from amg.cloud.credentials import CredentialStore
    from amg.cloud.rclone import Rclone, RcloneError, RcloneNotFoundError

    expanded: List[dict] = []
    seen: set[str] = set()

    store = CredentialStore()
    with store.materialize_config(names=[remote]) as cfg_path:
        rc = Rclone(config_path=cfg_path)
        for item in selected_items:
            p = str(item.get("path") or "").strip().strip("/")
            if not p:
                continue
            is_dir = bool(item.get("is_dir"))
            if not is_dir:
                if _is_video_cloud_path(p) and p not in seen:
                    expanded.append({"path": p, "download_root": None, "relative_path": None})
                    seen.add(p)
                continue
            try:
                folder_entries = rc.lsjson(
                    remote=remote,
                    path=p,
                    max_depth=32,
                    videos_only=True,
                    timeout=180.0,
                )
            except RcloneNotFoundError as exc:
                raise RuntimeError(f"rclone is not installed on controller: {exc}") from exc
            except RcloneError as exc:
                raise RuntimeError(f"failed to list folder {remote}:{p}: {exc}") from exc
            for entry in folder_entries:
                if entry.get("IsDir"):
                    continue
                rel = str(entry.get("Path") or entry.get("Name") or "").strip()
                if not rel:
                    continue
                full_path = _join_cloud_path(p, rel)
                if not _is_video_cloud_path(full_path):
                    continue
                if full_path in seen:
                    continue
                expanded.append(
                    {
                        "path": full_path,
                        "download_root": p,
                        "relative_path": _relative_cloud_path(full_path, p),
                    }
                )
                seen.add(full_path)
    return expanded


def _run_job(job_id: str) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["started_at_ts"] = time.time()
        job["started_at"] = datetime.now().isoformat()
        job["message"] = f"Running · priority #{job.get('queue_seq')}"
        job["current_phase"] = "ingest"
        video_path = Path(job["video_path"])
        cloud_source = job.get("cloud_source")

    try:
        backend = _get_backend_instance()
        # Only the local backend writes a run-log file on this host. The
        # remote backend already streams pod-side log lines through
        # on_log, and starting the disk-tail watcher would just clobber
        # those entries with an empty list when no local log exists.
        # Also, cloud-source jobs don't have a local log file even on the
        # LocalBackend (the rclone-then-pipeline path writes to the same
        # logger but not to the per-job tail file the watcher expects).
        if backend.name == "local" and cloud_source is None:
            _start_live_log_tail(job_id)

        def _on_log(line: str) -> None:
            with _jobs_lock:
                j = _jobs.get(job_id)
                if not j:
                    return
                tail = list(j.get("log_tail") or [])
                tail.append(line)
                if len(tail) > 50:
                    tail = tail[-50:]
                j["log_tail"] = tail
                j["message"] = line

        def _on_progress(pct: int) -> None:
            with _jobs_lock:
                j = _jobs.get(job_id)
                if not j:
                    return
                j["progress_pct"] = max(0, min(100, int(pct)))

        if cloud_source is not None:
            from amg.cloud.job_backend import CloudSource
            result = backend.run_cloud_job(
                CloudSource(
                    remote=cloud_source["remote"],
                    path=cloud_source["path"],
                    scene_id=cloud_source.get("scene_id"),
                    download_root=cloud_source.get("download_root"),
                    relative_path=cloud_source.get("relative_path"),
                ),
                on_log=_on_log,
                on_progress=_on_progress,
            )
        else:
            result = backend.run_job(video_path, on_log=_on_log, on_progress=_on_progress)
        with _jobs_lock:
            job = _jobs[job_id]
            job["result"] = result
            job["scene_id"] = result.get("scene_id", job.get("scene_id"))
            job["status"] = "done" if result.get("success") else "error"
            job["finished_at"] = datetime.now().isoformat()
            job["finished_at_ts"] = time.time()
            job["progress_pct"] = 100
            for ph in PHASES:
                pass  # phases finalized in _decorate_job
            job["message"] = (
                f"Done · {result.get('covers_saved', 0)} covers"
                if result.get("success")
                else f"Failed · {','.join(result.get('error_codes', [])) or 'unknown'}"
            )
        _record_run_timing(job, result)
        if cloud_source is not None:
            resolved_scene = str((result or {}).get("scene_id") or job.get("scene_id") or "").strip()
            if resolved_scene:
                _persist_rerun_cloud_source(resolved_scene, cloud_source)
        else:
            resolved_scene = str((result or {}).get("scene_id") or job.get("scene_id") or "").strip()
            if resolved_scene:
                _persist_rerun_video_path(resolved_scene, str(job.get("video_path") or ""))
    except Exception as e:
        with _jobs_lock:
            job = _jobs[job_id]
            job["status"] = "error"
            job["finished_at"] = datetime.now().isoformat()
            job["finished_at_ts"] = time.time()
            job["message"] = str(e)
        _record_run_timing(job, {"success": False, "error_codes": [str(e)]})


def _latest_cloud_source_for_scene(scene_id: str) -> Optional[dict]:
    target = _safe_scene_id((scene_id or "").strip())
    if not target:
        return None
    with _jobs_lock:
        candidates = list(_jobs.values())
    for job in reversed(candidates):
        raw_scene = str(job.get("scene_id") or (job.get("result") or {}).get("scene_id") or "").strip()
        if _safe_scene_id(raw_scene) != target:
            continue
        cloud_source = job.get("cloud_source")
        if isinstance(cloud_source, dict) and cloud_source.get("remote") and cloud_source.get("path"):
            return cloud_source
    persisted = _load_persisted_rerun_cloud_source(target)
    if persisted:
        # #region agent log
        _debug_log_dbg_mode(
            "post-fix-rerun",
            "H8",
            "amg/ui/app.py:_latest_cloud_source_for_scene:persisted_hit",
            "using persisted cloud source",
            {"scene_id": target, "remote": persisted.get("remote"), "path": persisted.get("path")},
        )
        # #endregion
        return persisted
    return None


def _latest_video_path_for_scene(scene_id: str) -> Optional[str]:
    target = _safe_scene_id((scene_id or "").strip())
    if not target:
        return None
    with _jobs_lock:
        candidates = list(_jobs.values())
    for job in reversed(candidates):
        raw_scene = str(job.get("scene_id") or (job.get("result") or {}).get("scene_id") or "").strip()
        if _safe_scene_id(raw_scene) != target:
            continue
        video_path = str(job.get("video_path") or "").strip()
        if video_path:
            return video_path
    persisted = _load_persisted_rerun_video_path(target)
    if persisted:
        # #region agent log
        _debug_log_dbg_mode(
            "post-fix-rerun",
            "H9",
            "amg/ui/app.py:_latest_video_path_for_scene:persisted_hit",
            "using persisted local rerun path",
            {"scene_id": target, "video_path": persisted},
        )
        # #endregion
        return persisted
    return None


def _load_persisted_rerun_cloud_source(scene_id: str) -> Optional[dict]:
    try:
        if not RERUN_SOURCES_PATH.exists():
            return None
        with open(RERUN_SOURCES_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
        row = (payload or {}).get(_safe_scene_id(scene_id))
        if isinstance(row, dict) and row.get("remote") and row.get("path"):
            return row
    except Exception:
        return None
    return None


def _load_persisted_rerun_video_path(scene_id: str) -> Optional[str]:
    try:
        if not RERUN_SOURCES_PATH.exists():
            return None
        with open(RERUN_SOURCES_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
        row = (payload or {}).get(_safe_scene_id(scene_id))
        video_path = str((row or {}).get("video_path") or "").strip()
        if video_path:
            return video_path
    except Exception:
        return None
    return None


def _persist_rerun_cloud_source(scene_id: str, cloud_source: dict) -> None:
    sid = _safe_scene_id(scene_id)
    if not sid:
        return
    row = {
        "remote": str(cloud_source.get("remote") or "").strip(),
        "path": str(cloud_source.get("path") or "").strip(),
        "download_root": str(cloud_source.get("download_root") or "").strip() or None,
        "relative_path": str(cloud_source.get("relative_path") or "").strip() or None,
    }
    if not row["remote"] or not row["path"]:
        return
    try:
        data = {}
        if RERUN_SOURCES_PATH.exists():
            with open(RERUN_SOURCES_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        data[sid] = row
        RERUN_SOURCES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RERUN_SOURCES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        # #region agent log
        _debug_log_dbg_mode(
            "post-fix-rerun",
            "H8",
            "amg/ui/app.py:_persist_rerun_cloud_source:write",
            "persisted cloud rerun source",
            {"scene_id": sid, "remote": row["remote"], "path": row["path"]},
        )
        # #endregion
    except Exception:
        return


def _persist_rerun_video_path(scene_id: str, video_path: str) -> None:
    sid = _safe_scene_id(scene_id)
    vp = str(video_path or "").strip()
    if not sid or not vp:
        return
    try:
        data = {}
        if RERUN_SOURCES_PATH.exists():
            with open(RERUN_SOURCES_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        row = data.get(sid)
        if not isinstance(row, dict):
            row = {}
        row["video_path"] = vp
        data[sid] = row
        RERUN_SOURCES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RERUN_SOURCES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        # #region agent log
        _debug_log_dbg_mode(
            "post-fix-rerun",
            "H9",
            "amg/ui/app.py:_persist_rerun_video_path:write",
            "persisted local rerun path",
            {"scene_id": sid, "video_path": vp},
        )
        # #endregion
    except Exception:
        return


def _record_run_timing(job: dict, result: Optional[dict]) -> None:
    """
    Append one durable run timing row (JSONL) for UI analytics.
    """
    try:
        RUN_TIMINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        started = job.get("started_at_ts")
        finished = job.get("finished_at_ts")
        elapsed = None
        if started and finished:
            elapsed = round(max(0.0, float(finished) - float(started)), 2)

        decision_log_path = None
        if isinstance(result, dict):
            p = result.get("decision_log_path")
            if p:
                decision_log_path = str(p)

        execution = {}
        if decision_log_path:
            try:
                with open(decision_log_path, "r") as f:
                    dlog = json.load(f)
                execution = (dlog.get("execution") or {}) if isinstance(dlog, dict) else {}
            except Exception as e:
                log.warn("Failed to load decision log for run timing", path=str(decision_log_path), error=str(e))
                execution = {}

        phase_durations = {}
        for name, phase in (execution.get("phases") or {}).items():
            if isinstance(phase, dict):
                try:
                    phase_durations[name] = round(float(phase.get("duration_sec", 0) or 0), 2)
                except (TypeError, ValueError):
                    phase_durations[name] = 0.0

        row = {
            "timestamp": _utc_now_isoz(),
            "job_id": job.get("job_id"),
            "scene_id": job.get("scene_id"),
            "status": job.get("status"),
            "source_mode": job.get("source_mode"),
            "video_path": job.get("video_path"),
            "elapsed_sec": elapsed,
            "pipeline_total_sec": execution.get("total_duration_sec")
            if execution
            else (result or {}).get("total_duration_sec"),
            "phase_durations_sec": phase_durations,
            "covers_saved": (result or {}).get("covers_saved"),
            "error_codes": (result or {}).get("error_codes", []),
            "decision_log_path": decision_log_path,
        }
        with open(RUN_TIMINGS_PATH, "a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        return


def _readiness_snapshot(scene_id: str) -> Optional[dict]:
    """Compute distribution readiness snapshot for scene detail UI."""
    try:
        from amg.review.distribution_gate import check_distribution_ready
        return check_distribution_ready(scene_id, verbose=False)
    except Exception as e:
        log.warn("Readiness snapshot failed", scene_id=scene_id, error=str(e))
        return None


def _parse_csv_tokens(raw: str, *, title_case: bool = False, lowercase: bool = False) -> List[str]:
    out: List[str] = []
    seen = set()
    for token in re.split(r"[,;\n|]", str(raw or "")):
        t = " ".join(token.strip().split())
        if not t:
            continue
        if lowercase:
            t = t.lower()
        if title_case:
            t = " ".join(part.capitalize() for part in t.split())
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def _normalized_edit_distance(lhs: Optional[str], rhs: Optional[str]) -> float:
    a = " ".join(str(lhs or "").strip().lower().split())
    b = " ".join(str(rhs or "").strip().lower().split())
    if not a and not b:
        return 0.0
    ratio = SequenceMatcher(a=a, b=b).ratio()
    return round(max(0.0, min(1.0, 1.0 - float(ratio))), 4)


def _sequence_edit_distance(lhs: List[str], rhs: List[str]) -> float:
    a = [str(x).strip().lower() for x in (lhs or []) if str(x).strip()]
    b = [str(x).strip().lower() for x in (rhs or []) if str(x).strip()]
    if not a and not b:
        return 0.0
    ratio = SequenceMatcher(a="\n".join(a), b="\n".join(b)).ratio()
    return round(max(0.0, min(1.0, 1.0 - float(ratio))), 4)


def _parse_reason_codes(raw_codes: Optional[str], multi_values: Optional[List[str]] = None) -> List[str]:
    values: List[str] = []
    if raw_codes:
        values.extend(re.split(r"[,;\n|]", str(raw_codes)))
    for val in (multi_values or []):
        values.extend(re.split(r"[,;\n|]", str(val)))
    out: List[str] = []
    seen = set()
    for token in values:
        t = re.sub(r"[^a-zA-Z0-9_ -]", "", str(token).strip().lower()).replace(" ", "_")
        t = re.sub(r"_+", "_", t).strip("_")
        if not t:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out[:12]


def _build_metadata_learning_signals(
    *,
    insight: Optional[dict],
    title_text: str,
    long_description: str,
    tags_csv: str,
    categories_csv: str,
    reason_codes: List[str],
) -> dict:
    insight = insight if isinstance(insight, dict) else {}
    ai_titles = insight.get("ai_titles") if isinstance(insight.get("ai_titles"), list) else []
    ai_primary_title = ""
    if ai_titles and isinstance(ai_titles[0], dict):
        ai_primary_title = str(ai_titles[0].get("text") or "").strip()
    ai_desc = str(insight.get("long_description") or "").strip()
    ai_tags = [str(x).strip().lower() for x in (insight.get("ai_tags") or []) if str(x).strip()]
    ai_categories = [
        " ".join(str(x).strip().split()).title()
        for x in (insight.get("ai_categories") or [])
        if str(x).strip()
    ]
    final_tags = _parse_csv_tokens(tags_csv or "", lowercase=True)
    final_categories = _parse_csv_tokens(categories_csv or "", title_case=True)
    title_dist = _normalized_edit_distance(ai_primary_title, title_text)
    desc_dist = _normalized_edit_distance(ai_desc, long_description)
    tags_dist = _sequence_edit_distance(ai_tags, final_tags)
    categories_dist = _sequence_edit_distance(ai_categories, final_categories)
    unchanged = (
        title_dist == 0.0
        and desc_dist == 0.0
        and tags_dist == 0.0
        and categories_dist == 0.0
    )
    return {
        "ai_primary_title": ai_primary_title or None,
        "ai_long_description": ai_desc or None,
        "title_edit_distance": title_dist,
        "description_edit_distance": desc_dist,
        "tags_edit_distance": tags_dist,
        "categories_edit_distance": categories_dist,
        "metadata_acceptance_label": "unchanged" if unchanged else "edited",
        "metadata_edit_reason_codes": reason_codes or [],
    }


def _platform_rules_snapshot() -> dict:
    out = {}
    for platform, req in (PLATFORM_REQUIREMENTS or {}).items():
        if not isinstance(req, dict):
            continue
        md = req.get("metadata") if isinstance(req.get("metadata"), dict) else {}
        out[platform] = {
            "title_min_chars": int(md.get("title_min_chars", 1)),
            "title_max_chars": int(req.get("title_max_chars", md.get("title_max_chars", 120))),
            "description_min_chars": int(md.get("description_min_chars", 0)),
            "description_max_chars": int(md.get("description_max_chars", 9999)),
            "min_tags": int(md.get("min_tags", 0)),
            "max_tags": int(md.get("max_tags", 9999)),
            "min_categories": int(md.get("min_categories", 0)),
            "max_categories": int(md.get("max_categories", 9999)),
        }
    return out


def _editor_suggestion_seed(decision_log: Optional[dict], insight: Optional[dict]) -> dict:
    insight = insight if isinstance(insight, dict) else {}
    inp = (decision_log or {}).get("input") if isinstance(decision_log, dict) else {}
    inp = inp if isinstance(inp, dict) else {}
    genres = [str(g).strip() for g in (inp.get("genres") or []) if str(g).strip()]
    ai_tags = [str(t).strip() for t in (insight.get("ai_tags") or []) if str(t).strip()]
    ai_categories = [str(c).strip() for c in (insight.get("ai_categories") or []) if str(c).strip()]
    tags = []
    for token in ai_tags + [g.lower().replace("_", " ") for g in genres]:
        norm = " ".join(token.split()).lower()
        if norm and norm not in tags:
            tags.append(norm)
    categories = []
    for token in ai_categories + [" ".join(g.split("_")).title() for g in genres]:
        norm = " ".join(str(token).split())
        if norm and norm.lower() not in {x.lower() for x in categories}:
            categories.append(norm)
    return {
        "tags": tags[:40],
        "categories": categories[:24],
        "description_seed": str(insight.get("long_description") or "").strip(),
    }


def _load_recent_run_timings(limit: int = 12) -> List[dict]:
    if not RUN_TIMINGS_PATH.exists():
        return []
    rows: List[dict] = []
    try:
        with open(RUN_TIMINGS_PATH, "r") as f:
            lines = f.readlines()
    except Exception:
        return []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        phase_map = row.get("phase_durations_sec") or {}
        if not isinstance(phase_map, dict):
            phase_map = {}
        row["phase_durations_sec"] = phase_map
        row["pipeline_total_sec"] = _safe_float(row.get("pipeline_total_sec"))
        row["elapsed_sec"] = _safe_float(row.get("elapsed_sec"))
        row["pipeline_total_h"] = _humanize_duration(row["pipeline_total_sec"])
        row["elapsed_h"] = _humanize_duration(row["elapsed_sec"])
        row["timestamp_ago"] = _humanize_ago(row.get("timestamp", ""))
        rows.append(row)
        if len(rows) >= max(1, int(limit)):
            break
    return rows


def _summarize_slowest_phases(runs: List[dict], top_n: int = 5) -> List[dict]:
    agg: Dict[str, dict] = {}
    for run in runs:
        phase_map = run.get("phase_durations_sec") or {}
        for phase, raw in phase_map.items():
            sec = _safe_float(raw)
            if sec is None or sec <= 0:
                continue
            cur = agg.setdefault(phase, {"phase": phase, "count": 0, "total_sec": 0.0, "max_sec": 0.0})
            cur["count"] += 1
            cur["total_sec"] += sec
            cur["max_sec"] = max(cur["max_sec"], sec)
    out = []
    for row in agg.values():
        avg = row["total_sec"] / row["count"] if row["count"] else 0.0
        out.append(
            {
                "phase": row["phase"],
                "count": row["count"],
                "avg_sec": round(avg, 2),
                "max_sec": round(row["max_sec"], 2),
                "avg_h": _humanize_duration(avg),
                "max_h": _humanize_duration(row["max_sec"]),
            }
        )
    out.sort(key=lambda x: x["avg_sec"], reverse=True)
    return out[: max(1, int(top_n))]


def _latest_run_log_for_scene(scene_id: str) -> Optional[Path]:
    if not RUN_LOGS_DIR.exists():
        return None
    pattern = f"{_safe_scene_id(scene_id)}_*.log"
    matches = sorted(RUN_LOGS_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if matches:
        return matches[0]
    matches = sorted(RUN_LOGS_DIR.glob(f"{scene_id}_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _start_live_log_tail(job_id: str) -> None:
    def _watch():
        seen_log = None
        last_size = 0
        tail = deque(maxlen=8)
        for _ in range(7200):  # ~2h ceiling
            with _jobs_lock:
                job = _jobs.get(job_id)
                if not job:
                    return
                if job.get("status") not in {"running", "queued"}:
                    return
                scene_id = job.get("scene_id", "")
            if not scene_id:
                time.sleep(1.0)
                continue

            run_log = _latest_run_log_for_scene(scene_id)
            if run_log is None:
                time.sleep(1.0)
                continue

            try:
                if seen_log != run_log:
                    seen_log = run_log
                    last_size = 0
                    tail.clear()

                size = run_log.stat().st_size
                if size > last_size:
                    with open(run_log, "r", errors="ignore") as f:
                        f.seek(last_size)
                        chunk = f.read()
                    last_size = size
                    new_phase = None
                    for line in chunk.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        tail.append(line)
                        m = _PHASE_RE.search(line)
                        if m:
                            new_phase = m.group(1)
                    with _jobs_lock:
                        job = _jobs.get(job_id)
                        if not job:
                            return
                        job["log_tail"] = list(tail)
                        if tail:
                            job["message"] = tail[-1]
                        if new_phase:
                            job["current_phase"] = new_phase
            except Exception as e:
                log.warn("Live log tail watcher error", job_id=job_id, error=str(e))

            time.sleep(1.0)

    t = threading.Thread(target=_watch, daemon=True)
    t.start()


def _decorate_job(job: dict) -> dict:
    """Add display-time metadata: phases, progress, elapsed/eta strings."""
    j = dict(job)
    status = j.get("status")
    cur = j.get("current_phase")
    cur_idx = PHASES.index(cur) if cur in PHASES else -1

    with _jobs_lock:
        active = [
            x for x in _jobs.values()
            if x.get("status") in {"queued", "running"}
        ]
    active_sorted = sorted(active, key=lambda x: x.get("queue_seq", 0))
    for idx, item in enumerate(active_sorted, start=1):
        if item.get("job_id") == j.get("job_id"):
            j["queue_pos"] = idx
            break

    phases = []
    if status == "done":
        for ph in PHASES:
            phases.append({"label": ph.replace("_", " "), "cls": "done"})
        j["progress_pct"] = 100
    elif status == "error":
        for i, ph in enumerate(PHASES):
            cls = "done" if i < cur_idx else ("active" if i == cur_idx else "")
            phases.append({"label": ph.replace("_", " "), "cls": cls})
        j["progress_pct"] = max(5, int((cur_idx + 1) / len(PHASES) * 100)) if cur_idx >= 0 else 0
    else:
        for i, ph in enumerate(PHASES):
            if i < cur_idx:
                cls = "done"
            elif i == cur_idx:
                cls = "active"
            else:
                cls = ""
            phases.append({"label": ph.replace("_", " "), "cls": cls})
        # progress: completed phases / total
        done_count = max(cur_idx, 0)
        j["progress_pct"] = int(done_count / len(PHASES) * 100) if cur_idx >= 0 else 5
    j["phases"] = phases

    # Elapsed / ETA / real-time pct.
    started = j.get("started_at_ts")
    finished = j.get("finished_at_ts")
    elapsed = None
    if started:
        elapsed = (finished or time.time()) - started
        j["elapsed_str"] = _humanize_duration(elapsed)
        if elapsed > 0 and j.get("progress_pct"):
            pct = j["progress_pct"]
            if pct > 5 and pct < 100:
                eta = elapsed * (100 - pct) / pct
                j["eta_str"] = _humanize_duration(eta)

    # If we already have a decision log, compute % of real-time.
    sid = j.get("scene_id")
    if sid and elapsed:
        d = _load_decision_log(sid)
        if d:
            video_dur = (d.get("input") or {}).get("duration_sec") or 0
            if video_dur > 0:
                j["realtime_pct"] = int(round(elapsed / video_dur * 100))
    return j


# ---------- ollama health ----------

def _ollama_ok(timeout: float = 0.4) -> bool:
    try:
        from amg.config import OLLAMA_HOST
        target = OLLAMA_HOST
        if target.startswith("https://"):
            target = target[len("https://"):]
            default_port = 443
        elif target.startswith("http://"):
            target = target[len("http://"):]
            default_port = 80
        else:
            default_port = 11434
        target = target.split("/", 1)[0]  # strip any path
        host, _, port = target.partition(":")
        s = socket.create_connection((host, int(port or default_port)), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def _disk_free_gb(p: Path) -> int:
    try:
        usage = shutil.disk_usage(str(p))
        return int(usage.free / (1024 ** 3))
    except Exception:
        return 0


def _health_snapshot() -> dict:
    """Cheap, ~5s cached."""
    global _health_cache, _health_ts
    with _health_lock:
        if _health_cache and (time.time() - _health_ts) < 5.0:
            return _health_cache
        snap = {
            "ollama_ok": _ollama_ok(),
            "model": VISION_MODEL,
            "parallel": "4",
            "kv_cache": "MLX · q8_0",
            "disk_free_gb": _disk_free_gb(Path.home()),
            "scenes_total": len(list(DECISION_LOGS_DIR.glob("*.json"))) if DECISION_LOGS_DIR.exists() else 0,
        }
        _health_cache = snap
        _health_ts = time.time()
        return snap


# ---------- feedback page ----------

def _feedback_metrics_from_rows(rows: list[dict]) -> dict:
    total = len(rows)
    if total == 0:
        return {"total_rows": 0}

    pen_labeled = 0
    pen_matches = 0
    pos_labeled = 0
    pos_matches = 0
    score_labeled = 0
    score_abs_err_sum = 0.0
    score_within_5 = 0
    by_scene: Dict[str, int] = {}

    for r in rows:
        sid = r.get("scene_id", "unknown")
        by_scene[sid] = by_scene.get(sid, 0) + 1
        model = r.get("model") or {}
        op = r.get("operator") or {}

        op_pen = op.get("penetration_visible")
        if op_pen in ("yes", "no"):
            pen_labeled += 1
            model_pen = bool(model.get("penetration_visible", False))
            if (op_pen == "yes" and model_pen) or (op_pen == "no" and not model_pen):
                pen_matches += 1

        op_pos = (op.get("position_label") or "").upper()
        if op_pos:
            pos_labeled += 1
            model_pos = (model.get("position_label") or "").upper()
            if op_pos == model_pos:
                pos_matches += 1

        op_score = op.get("score_100")
        model_score = model.get("score_100")
        if op_score is not None and model_score is not None:
            try:
                op_v = float(op_score)
                model_v = float(model_score)
            except (TypeError, ValueError):
                op_v = None
                model_v = None
            if op_v is not None and model_v is not None:
                score_labeled += 1
                err = abs(op_v - model_v)
                score_abs_err_sum += err
                if err <= 5.0:
                    score_within_5 += 1

    return {
        "total_rows": total,
        "scene_count": len(by_scene),
        "penetration_labeled_rows": pen_labeled,
        "penetration_match_rate": round((pen_matches / pen_labeled) * 100, 1) if pen_labeled else None,
        "position_labeled_rows": pos_labeled,
        "position_match_rate": round((pos_matches / pos_labeled) * 100, 1) if pos_labeled else None,
        "score_labeled_rows": score_labeled,
        "score_mae": round(score_abs_err_sum / score_labeled, 2) if score_labeled else None,
        "score_within_5_rate": round((score_within_5 / score_labeled) * 100, 1) if score_labeled else None,
        "rows_by_scene": by_scene,
    }


def _feedback_page_data(
    *,
    scene_id: str = "",
    studio: str = "",
    since_days: str = "",
    view: str = "disagreements",
) -> dict:
    since_days_int = _safe_int(since_days, 0)
    since_days_int = since_days_int if since_days_int > 0 else None
    rows = load_feedback_rows(
        scene_id=scene_id.strip() or None,
        studio=studio.strip() or None,
        since_days=since_days_int,
    )
    metrics = _feedback_metrics_from_rows(rows)
    if not scene_id and not studio and since_days_int is None:
        # Keep CLI parity when no UI filters are set.
        metrics = evaluate_feedback()

    decision_counts = {"keep": 0, "reject": 0, "maybe": 0}
    position_counts: dict[str, int] = {}
    for r in rows:
        op = (r.get("operator") or {})
        d = op.get("decision")
        if d in decision_counts:
            decision_counts[d] += 1
        pos = (op.get("position_label") or "").upper()
        if pos:
            position_counts[pos] = position_counts.get(pos, 0) + 1

    # Build recent comparisons table.
    disagreement_only = (view or "disagreements") != "all"
    recent_rows = []
    for r in reversed(rows[-120:]):
        model = r.get("model") or {}
        op = (r.get("operator") or {})
        ts = r.get("timestamp", "")[:19].replace("T", " ")
        sid = r.get("scene_id", "")
        filename = r.get("filename") or ""
        t_sec = r.get("timestamp_sec")
        t_short = ""
        if isinstance(t_sec, (float, int)):
            t_short = f"{float(t_sec):.1f}s"

        op_pen = op.get("penetration_visible")
        if op_pen in ("yes", "no"):
            model_pen = bool(model.get("penetration_visible"))
            mv = "yes" if model_pen else "no"
            match = (op_pen == mv)
            match_cls = "ok" if match else "err"
            if disagreement_only and match_cls == "ok":
                pass
            else:
                recent_rows.append(
                    {
                        "scene_id": sid,
                        "saved_at": ts,
                        "filename": filename,
                        "cover_time": t_short,
                        "field": "penetration",
                        "model_value": mv,
                        "operator_value": op_pen,
                        "match_cls": match_cls,
                    }
                )

        op_pos = (op.get("position_label") or "").upper()
        if op_pos:
            model_pos = (model.get("position_label") or "").upper() or "OTHER"
            match = (op_pos == model_pos)
            match_cls = "ok" if match else "err"
            if disagreement_only and match_cls == "ok":
                pass
            else:
                recent_rows.append(
                    {
                        "scene_id": sid,
                        "saved_at": ts,
                        "filename": filename,
                        "cover_time": t_short,
                        "field": "position",
                        "model_value": model_pos,
                        "operator_value": op_pos,
                        "match_cls": match_cls,
                    }
                )

        op_score = op.get("score_100")
        model_score = model.get("score_100")
        if op_score is not None and model_score is not None:
            try:
                op_v = float(op_score)
                model_v = float(model_score)
                err = abs(op_v - model_v)
                match_cls = "ok" if err <= 5.0 else ("warn" if err <= 10.0 else "err")
                if disagreement_only and match_cls == "ok":
                    pass
                else:
                    recent_rows.append(
                        {
                            "scene_id": sid,
                            "saved_at": ts,
                            "filename": filename,
                            "cover_time": t_short,
                            "field": "score",
                            "model_value": f"{model_v:.1f}",
                            "operator_value": f"{op_v:.1f}",
                            "match_cls": match_cls,
                        }
                    )
            except (TypeError, ValueError):
                pass
        if len(recent_rows) >= 40:
            break

    by_scene = (metrics.get("rows_by_scene") or {})
    scenes_by_count = [{"scene_id": k, "count": v} for k, v in sorted(by_scene.items(), key=lambda x: x[1], reverse=True)[:8]]

    pos_total = sum(position_counts.values()) or 1
    position_list = [
        {"label": k, "count": v, "pct": round(v / pos_total * 100, 1)}
        for k, v in sorted(position_counts.items(), key=lambda x: x[1], reverse=True)
    ]

    trend_days = {}
    for r in rows:
        stamp = str(r.get("timestamp", "") or "")
        day = stamp[:10]
        if len(day) == 10:
            trend_days[day] = trend_days.get(day, 0) + 1
    trend_rows = [{"day": k, "count": v} for k, v in sorted(trend_days.items(), reverse=True)[:7]]
    trend_rows.reverse()
    try:
        from amg.learning.rule_packs import get_active_rule_pointer
        from amg.learning.rule_promotion import list_rule_eval_runs
        rule_status = get_active_rule_pointer()
        latest_rule_eval = next(iter(list_rule_eval_runs(limit=1)), None)
    except Exception:
        rule_status = {
            "enabled": False,
            "mode": "off",
            "rule_pack_id": None,
            "canary_pct": 0.0,
            "updated_at_utc": None,
        }
        latest_rule_eval = None

    return {
        "metrics": metrics,
        "recent_rows": recent_rows,
        "scenes_by_count": scenes_by_count,
        "position_counts": position_list,
        "decision_counts": decision_counts,
        "feedback_path": str(OPERATOR_FEEDBACK_PATH),
        "feedback_filter": {
            "scene": scene_id or "",
            "studio": studio or "",
            "since_days": str(since_days or ""),
            "view": "all" if (view == "all") else "disagreements",
        },
        "trend_rows": trend_rows,
        "rule_status": rule_status,
        "latest_rule_eval": latest_rule_eval,
    }


# ---------- library page ----------

def _library_data(filt: dict) -> dict:
    logs = _all_decision_logs()
    summaries = [_scene_summary(d) for d in logs]

    studio_counts: dict[str, int] = {}
    for s in summaries:
        if s["studio"]:
            studio_counts[s["studio"]] = studio_counts.get(s["studio"], 0) + 1
    studios = [{"name": k, "count": v} for k, v in sorted(studio_counts.items(), key=lambda x: x[1], reverse=True)]

    stats = {
        "total": len(summaries),
        "ready": sum(1 for s in summaries if s["status"] == "REVIEWED"),
        "review": sum(1 for s in summaries if s["status"] == "REVIEW"),
        "draft": sum(1 for s in summaries if s["status"] == "DRAFT"),
        "failed": sum(1 for s in summaries if s["status"] == "FAILED"),
    }

    # filter
    filtered, sort = _filter_and_sort_library_summaries(summaries, filt)
    limit = max(12, min(_safe_int(filt.get("limit"), 24), 120))
    total_filtered = len(filtered)
    visible_scenes = filtered[:limit]
    has_more = total_filtered > len(visible_scenes)
    next_limit = min(limit + 24, 120)

    return {
        "scenes": visible_scenes,
        "studios": studios,
        "stats": stats,
        "filter": {**filt, "sort": sort, "limit": str(limit)},
        "total_filtered": total_filtered,
        "has_more": has_more,
        "next_limit": next_limit,
    }


def _filter_and_sort_library_summaries(summaries: List[dict], filt: dict) -> tuple[List[dict], str]:
    # filter
    q = (filt.get("q") or "").strip().lower()
    studio = (filt.get("studio") or "").strip()
    status = (filt.get("status") or "").strip().lower()
    sort = (filt.get("sort") or "newest").strip().lower()
    text_fallback = (filt.get("text_fallback") or "").strip().lower()
    only_text_fallback = text_fallback in {"1", "true", "yes", "on"}

    filtered = []
    for s in summaries:
        haystack_parts = [
            s["scene_id"] or "",
            s["studio"] or "",
            s.get("title") or "",
            " ".join(s.get("performers") or []),
            " ".join(s.get("tags") or []),
        ]
        haystack = " ".join(haystack_parts).lower()
        if q and q not in haystack:
            continue
        if studio and s["studio"] != studio:
            continue
        if status:
            wanted = {
                "ready": "REVIEWED",
                "review": "REVIEW",
                "draft": "DRAFT",
                "failed": "FAILED",
            }.get(status)
            if wanted and s["status"] != wanted:
                continue
        if only_text_fallback and not bool(s.get("text_model_fallback_used")):
            continue
        filtered.append(s)

    action_rank = {"REVIEW": 0, "FAILED": 1, "DRAFT": 2, "REVIEWED": 3}
    if sort == "newest":
        filtered.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    elif sort == "highest_score":
        filtered.sort(key=lambda x: _normalize_score_for_ui(x.get("top_score")) or 0.0, reverse=True)
    elif sort == "lowest_score":
        filtered.sort(key=lambda x: _normalize_score_for_ui(x.get("top_score")) or 0.0)
    else:
        filtered.sort(
            key=lambda x: (
                action_rank.get(x.get("status"), 99),
                -(float(_normalize_score_for_ui(x.get("top_score")) or 0.0)),
                x.get("timestamp") or "",
            ),
            reverse=False,
        )
    return filtered, sort


def _remove_feedback_rows_for_scene(scene_id: str) -> tuple[int, int]:
    """
    Remove operator-feedback rows for one scene_id from feedback jsonl.
    Returns (removed_count, remaining_count).
    """
    if not OPERATOR_FEEDBACK_PATH.exists():
        return 0, 0
    kept_lines: list[str] = []
    removed_count = 0
    for raw in OPERATOR_FEEDBACK_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            kept_lines.append(line)
            continue
        if str((row or {}).get("scene_id") or "") == scene_id:
            removed_count += 1
            continue
        kept_lines.append(line)
    tmp = OPERATOR_FEEDBACK_PATH.with_suffix(".tmp")
    tmp.write_text(("\n".join(kept_lines) + ("\n" if kept_lines else "")), encoding="utf-8")
    tmp.replace(OPERATOR_FEEDBACK_PATH)
    return removed_count, len(kept_lines)


def _delete_scene_from_library(scene_id: str, *, delete_feedback: bool) -> dict:
    """
    Delete scene artifacts surfaced in Library:
    decision log, reviewed state, extracted work_dir, and optionally feedback rows.
    """
    sid = _safe_scene_id(scene_id)
    decision_log_path = DECISION_LOGS_DIR / f"{sid}.json"
    reviewed_path = REVIEWED_DIR / f"{sid}.json"
    decision_log = _load_decision_log(scene_id)
    work_dir = _resolve_scene_work_dir(scene_id, decision_log)

    deleted = {"decision_log": False, "reviewed": False, "work_dir": False, "feedback_rows": 0}

    if decision_log_path.exists():
        decision_log_path.unlink(missing_ok=True)
        deleted["decision_log"] = True
    if reviewed_path.exists():
        reviewed_path.unlink(missing_ok=True)
        deleted["reviewed"] = True
    if work_dir and work_dir.exists():
        try:
            if _path_within_roots(work_dir.resolve(), [DATA_DIR.resolve()]):
                shutil.rmtree(work_dir, ignore_errors=True)
                deleted["work_dir"] = True
        except Exception:
            pass
    if delete_feedback:
        removed_count, _ = _remove_feedback_rows_for_scene(scene_id)
        deleted["feedback_rows"] = removed_count
    return deleted


def _process_jobs_view() -> dict:
    """
    Snapshot for Process page: FIFO queue slice, separated active vs completed.

    Larger window than legacy [-20:] so overnight batches remain visible without
    having completed rows appear to "vanish" when new uploads shift the slice.
    """
    with _jobs_lock:
        jobs_raw = sorted(list(_jobs.values()), key=lambda j: j.get("queue_seq", 0))[-120:]
    jobs = [_decorate_job(j) for j in jobs_raw]
    active_all = [j for j in jobs if j.get("status") in {"queued", "running"}]
    running = [j for j in active_all if j.get("status") == "running"]
    queued = [j for j in active_all if j.get("status") == "queued"]
    running.sort(key=lambda j: j.get("queue_seq", 0))
    queued.sort(key=lambda j: j.get("queue_seq", 0))
    active_jobs = running + queued
    completed_jobs = [j for j in reversed(jobs) if j.get("status") in {"done", "error", "stopped"}][:36]
    return {
        "active_jobs": active_jobs,
        "completed_jobs": completed_jobs,
        "running_count": len(running),
        "queued_count": len(queued),
    }


# ---------- app ----------

def create_app() -> FastAPI:
    app = FastAPI(title="AMG UI", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    # Auth: registers /login and /logout, plus the gate middleware (when
    # AMG_AUTH_DISABLED is not set). Local Mac use sets that env var via
    # cmd_ui() in cli.py, so the existing single-user workflow keeps working.
    install_auth(app)

    @app.on_event("shutdown")
    async def _shutdown_backend() -> None:
        backend = None
        with _backend_lock:
            backend = _backend_instance
        if backend and hasattr(backend, "shutdown"):
            try:
                backend.shutdown()
            except Exception as exc:  # noqa: BLE001
                log.warn("Backend shutdown failed", error=str(exc))

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        pj = _process_jobs_view()
        recent_scenes = _recent_scenes(limit=12)
        recent_runs = _load_recent_run_timings(limit=10)
        slowest_phases = _summarize_slowest_phases(recent_runs, top_n=5)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "request": request,
                **pj,
                "recent_scenes": recent_scenes,
                "recent_runs": recent_runs,
                "slowest_phases": slowest_phases,
                "active_nav": "process",
                "health": _health_snapshot(),
            },
        )

    @app.get("/partials/process-jobs", response_class=HTMLResponse)
    async def process_jobs_partial(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="_process_queues.html",
            context={"request": request, **_process_jobs_view()},
        )

    @app.post("/jobs", response_class=HTMLResponse)
    async def create_job(
        request: Request,
        path: str = Form(default=""),
        video_file: UploadFile | None = File(default=None),
        video_files: List[UploadFile] = File(default=[]),
    ):
        video_path = None
        source_mode = "path"
        upload_stats = None

        uploads: List[UploadFile] = []
        # `video_files` is the new multi-file/folder path. Keep `video_file`
        # for compatibility with older clients.
        for u in (video_files or []):
            if u and (u.filename or "").strip():
                uploads.append(u)
        if video_file and (video_file.filename or "").strip():
            uploads.append(video_file)

        if uploads:
            video_path, upload_stats = await _save_upload_batch_and_select_video(uploads)
            source_mode = "folder_upload" if len(uploads) > 1 else "upload"
        elif path.strip():
            video_path = _resolve_video_path(Path(path.strip()))
            source_mode = "path"

        if video_path is None:
            if upload_stats and upload_stats.get("videos_found", 0) == 0:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Upload contained no valid video files. "
                        "Upload a folder that includes MP4/MOV/MKV/AVI/M4V/WEBM/WMV/FLV files."
                    ),
                )
            raise HTTPException(status_code=400, detail="Provide either a valid path or a video/folder upload.")

        job_id = uuid.uuid4().hex[:10]
        scene_id = _build_scene_id_from_video(video_path)
        global _job_seq_counter
        with _jobs_lock:
            _job_seq_counter += 1
            queue_seq = _job_seq_counter
        job = {
            "job_id": job_id,
            "status": "queued",
            "scene_id": scene_id,
            "video_path": str(video_path),
            "created_at": datetime.now().isoformat(),
            "message": f"Queued · priority #{queue_seq}",
            "result": None,
            "source_mode": source_mode,
            "log_tail": [],
            "current_phase": None,
            "progress_pct": 0,
            "queue_seq": queue_seq,
        }
        if upload_stats:
            chosen_name = Path(upload_stats["chosen_video"]).name if upload_stats.get("chosen_video") else "(none)"
            job["message"] = (
                f"Queued · priority #{queue_seq} · uploaded {upload_stats['files_uploaded']} files, "
                f"found {upload_stats['videos_found']} videos, selected longest: {chosen_name}"
            )
            job["upload_stats"] = upload_stats
        with _jobs_lock:
            _jobs[job_id] = job
            _job_fifo.append(job_id)
        _start_dispatcher_if_needed()

        return templates.TemplateResponse(
            request=request,
            name="_process_queues.html",
            context={"request": request, **_process_jobs_view()},
        )

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    async def job_card(request: Request, job_id: str):
        with _jobs_lock:
            job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return templates.TemplateResponse(
            request=request,
            name="_job_card.html",
            context={"request": request, "job": _decorate_job(job), "health": _health_snapshot()},
        )

    # ---------- cloud-source picker (Phase 2) ----------

    @app.get("/cloud", response_class=HTMLResponse)
    async def cloud_picker(request: Request):
        from amg.cloud.credentials import (
            CredentialKeyMissingError,
            CredentialStore,
        )
        try:
            store = CredentialStore()
            remotes = store.list_remotes()
        except CredentialKeyMissingError as exc:
            remotes = []
            error_msg = str(exc)
        else:
            error_msg = None
        return templates.TemplateResponse(
            request=request,
            name="cloud_picker.html",
            context={
                "request": request,
                "active_nav": "cloud",
                "remotes": remotes,
                "error_msg": error_msg,
                "health": _health_snapshot(),
            },
        )

    @app.get("/partials/cloud-browse", response_class=HTMLResponse)
    async def cloud_browse_partial(
        request: Request,
        remote: str,
        path: str = "",
    ):
        from amg.cloud.credentials import (
            CredentialNotFoundError,
            CredentialStore,
        )
        from amg.cloud.rclone import (
            Rclone,
            RcloneError,
            RcloneNotFoundError,
        )
        store = CredentialStore()
        try:
            with store.materialize_config(names=[remote]) as cfg_path:
                try:
                    entries = Rclone(config_path=cfg_path).lsjson(
                        remote,
                        path,
                        max_depth=1,
                        videos_only=True,
                    )
                except RcloneNotFoundError as exc:
                    return _cloud_browse_error(request, remote, path, str(exc))
                except RcloneError as exc:
                    return _cloud_browse_error(request, remote, path, str(exc))
        except CredentialNotFoundError as exc:
            return _cloud_browse_error(request, remote, path, str(exc))

        # Build parent crumb so the template can render a "go up" link
        # without needing to do path arithmetic in Jinja.
        parent_path = ""
        if path:
            parent_path = "/".join(path.rstrip("/").split("/")[:-1])

        return templates.TemplateResponse(
            request=request,
            name="_cloud_browse.html",
            context={
                "request": request,
                "remote": remote,
                "path": path,
                "parent_path": parent_path,
                "entries": sorted(entries, key=lambda e: (not e.get("IsDir"), e.get("Name", ""))),
            },
        )

    @app.post("/jobs/cloud")
    async def create_cloud_job(
        request: Request,
        remote: str = Form(...),
        path: str = Form(default=""),
        paths_json: str = Form(default=""),
        scene_id: str = Form(default=""),
    ):
        if not remote.strip():
            raise HTTPException(status_code=400, detail="remote is required")
        # Verify the credential actually exists before queueing — better to
        # 400 here than queue a job that's guaranteed to fail.
        from amg.cloud.credentials import (
            CredentialKeyMissingError,
            CredentialNotFoundError,
            CredentialStore,
        )
        try:
            CredentialStore().get_remote(remote)
        except CredentialNotFoundError:
            raise HTTPException(status_code=400, detail=f"unknown remote: {remote!r}")
        except CredentialKeyMissingError as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        selected_items: List[dict] = []
        if paths_json.strip():
            try:
                raw = json.loads(paths_json)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail=f"invalid paths_json: {exc}") from exc
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict):
                        selected_items.append(
                            {
                                "path": str(item.get("path") or "").strip(),
                                "is_dir": bool(item.get("type") == "folder" or item.get("is_dir")),
                            }
                        )
                    elif isinstance(item, str):
                        selected_items.append({"path": item.strip(), "is_dir": False})
        if not selected_items and path.strip():
            selected_items.append({"path": path.strip(), "is_dir": False})
        if not selected_items:
            raise HTTPException(status_code=400, detail="select at least one cloud file/folder")

        expanded_items = _expand_cloud_selection_to_videos(remote.strip(), selected_items)
        if not expanded_items:
            raise HTTPException(
                status_code=400,
                detail="selection contains no playable videos (expected mp4/mov/mkv/avi/m4v/webm/wmv/flv)",
            )
        max_batch = 200
        if len(expanded_items) > max_batch:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"selection expands to {len(expanded_items)} videos which exceeds the max queue size "
                    f"({max_batch}). Narrow the folder scope or submit in chunks."
                ),
            )

        first_job_id = ""
        seen_scene_ids: Dict[str, int] = {}
        global _job_seq_counter
        total = len(expanded_items)
        for idx, item in enumerate(expanded_items):
            one_path = item["path"]
            download_root = item.get("download_root")
            relative_path = item.get("relative_path")
            job_id = uuid.uuid4().hex[:10]
            if not first_job_id:
                first_job_id = job_id
            base_scene_id = _build_cloud_scene_id(
                one_path=one_path,
                download_root=download_root,
                relative_path=relative_path,
                explicit_scene_id=scene_id,
            )
            seen_scene_ids[base_scene_id] = seen_scene_ids.get(base_scene_id, 0) + 1
            n = seen_scene_ids[base_scene_id]
            scene_folder = base_scene_id if n == 1 else _safe_scene_id(f"{base_scene_id}_{n}")
            with _jobs_lock:
                _job_seq_counter += 1
                queue_seq = _job_seq_counter
                _jobs[job_id] = {
                    "job_id": job_id,
                    "status": "queued",
                    "scene_id": scene_folder,
                    "video_path": "",  # populated by the backend after rclone copy
                    "cloud_source": {
                        "remote": remote,
                        "path": one_path,
                        "scene_id": scene_folder,
                        "download_root": download_root,
                        "relative_path": relative_path,
                    },
                    "created_at": datetime.now().isoformat(),
                    "message": (
                        f"Queued · cloud {idx + 1}/{total} · {remote}:{one_path}"
                        f"{' · folder context' if download_root else ''} · priority #{queue_seq}"
                        if total > 1
                        else (
                            f"Queued · cloud · {remote}:{one_path}"
                            f"{' · folder context' if download_root else ''} · priority #{queue_seq}"
                        )
                    ),
                    "result": None,
                    "source_mode": "cloud",
                    "log_tail": [],
                    "current_phase": None,
                    "progress_pct": 0,
                    "queue_seq": queue_seq,
                }
                _job_fifo.append(job_id)
        _start_dispatcher_if_needed()

        # The operator submitted from /cloud, but the queue panel + per-job
        # cards live on /. Send them home so they can watch the job they
        # just kicked off — HX-Redirect makes HTMX do a full navigation
        # rather than swapping the queue HTML into the cloud-browser slot.
        return Response(
            status_code=204,
            headers={
                "HX-Redirect": f"/?job_id={first_job_id}",
            },
        )

    @app.get("/library", response_class=HTMLResponse)
    async def library(
        request: Request,
        q: str = Query(default=""),
        studio: str = Query(default=""),
        status: str = Query(default=""),
        sort: str = Query(default="newest"),
        text_fallback: str = Query(default=""),
        limit: str = Query(default="24"),
        deleted: str = Query(default=""),
        feedback_deleted: str = Query(default=""),
        rerun_queued: str = Query(default=""),
        rerun_skipped: str = Query(default=""),
    ):
        filt = {
            "q": q,
            "studio": studio,
            "status": status,
            "sort": sort,
            "text_fallback": text_fallback,
            "limit": limit,
        }
        data = _library_data(filt)
        delete_notice = None
        if deleted.strip():
            if feedback_deleted.strip():
                delete_notice = f"Deleted scene '{deleted}' (feedback rows removed: {feedback_deleted})."
            else:
                delete_notice = f"Deleted scene '{deleted}'."
        rerun_notice = None
        if rerun_queued.strip() or rerun_skipped.strip():
            queued_n = max(0, _safe_int(rerun_queued, 0))
            skipped_n = max(0, _safe_int(rerun_skipped, 0))
            rerun_notice = f"Queued {queued_n} scene rerun job(s)."
            if skipped_n:
                rerun_notice += f" Skipped {skipped_n} scene(s) with missing/inaccessible source video path."
        return templates.TemplateResponse(
            request=request,
            name="library.html",
            context={
                "request": request,
                **data,
                "delete_notice": delete_notice,
                "rerun_notice": rerun_notice,
                "active_nav": "library",
                "health": _health_snapshot(),
            },
        )

    @app.post("/library/rerun-filtered")
    async def library_rerun_filtered(
        q: str = Form(default=""),
        studio: str = Form(default=""),
        status: str = Form(default=""),
        sort: str = Form(default="newest"),
        text_fallback: str = Form(default=""),
    ):
        filt = {
            "q": q,
            "studio": studio,
            "status": status,
            "sort": sort,
            "text_fallback": text_fallback,
            "limit": "120",
        }
        logs = _all_decision_logs()
        filtered_summaries, normalized_sort = _filter_and_sort_library_summaries(
            [_scene_summary(d) for d in logs],
            filt,
        )
        log_by_scene = {
            str((d or {}).get("scene_id") or ""): d
            for d in logs
            if str((d or {}).get("scene_id") or "")
        }

        queued_count = 0
        skipped_count = 0
        first_job_id = ""
        seen_scene_ids: set[str] = set()

        global _job_seq_counter
        for summary in filtered_summaries:
            scene_id = str(summary.get("scene_id") or "").strip()
            if not scene_id or scene_id in seen_scene_ids:
                continue
            seen_scene_ids.add(scene_id)
            decision_log = log_by_scene.get(scene_id) or {}
            scene_path = str((decision_log or {}).get("scene_path") or "").strip()
            if not scene_path:
                skipped_count += 1
                continue
            resolved_video = _resolve_video_path(Path(scene_path))
            if resolved_video is None:
                skipped_count += 1
                continue

            job_id = uuid.uuid4().hex[:10]
            if not first_job_id:
                first_job_id = job_id
            with _jobs_lock:
                _job_seq_counter += 1
                queue_seq = _job_seq_counter
                _jobs[job_id] = {
                    "job_id": job_id,
                    "status": "queued",
                    "scene_id": scene_id,
                    "video_path": str(resolved_video),
                    "created_at": datetime.now().isoformat(),
                    "message": f"Queued · bulk rerun · {scene_id} · priority #{queue_seq}",
                    "result": None,
                    "source_mode": "path",
                    "log_tail": [],
                    "current_phase": None,
                    "progress_pct": 0,
                    "queue_seq": queue_seq,
                }
                _job_fifo.append(job_id)
            queued_count += 1

        if queued_count:
            _start_dispatcher_if_needed()

        filter_query = (
            f"&q={quote_plus(q)}"
            f"&studio={quote_plus(studio)}"
            f"&status={quote_plus(status)}"
            f"&sort={quote_plus(normalized_sort)}"
            f"&text_fallback={quote_plus(text_fallback)}"
        )
        if first_job_id:
            filter_query += f"&job_id={quote_plus(first_job_id)}"
        return RedirectResponse(
            url=f"/library?rerun_queued={queued_count}&rerun_skipped={skipped_count}{filter_query}",
            status_code=303,
        )

    @app.post("/library/delete")
    async def library_delete_scene(
        scene_id: str = Form(...),
        delete_feedback: str = Form(default=""),
    ):
        target_scene = scene_id.strip()
        if not target_scene:
            raise HTTPException(status_code=400, detail="scene_id is required")
        deleted_meta = _delete_scene_from_library(
            target_scene,
            delete_feedback=(delete_feedback.strip().lower() in {"1", "true", "on", "yes"}),
        )
        feedback_q = f"&feedback_deleted={deleted_meta.get('feedback_rows', 0)}" if deleted_meta.get("feedback_rows") else ""
        target_scene_q = quote_plus(target_scene)
        return RedirectResponse(
            url=f"/library?deleted={target_scene_q}{feedback_q}",
            status_code=303,
        )

    @app.get("/feedback", response_class=HTMLResponse)
    async def feedback(
        request: Request,
        scene: str = Query(default=""),
        studio: str = Query(default=""),
        since_days: str = Query(default=""),
        view: str = Query(default="disagreements"),
        rule_notice: str = Query(default=""),
    ):
        data = _feedback_page_data(scene_id=scene, studio=studio, since_days=since_days, view=view)
        return templates.TemplateResponse(
            request=request,
            name="feedback.html",
            context={
                "request": request,
                **data,
                "rule_notice": rule_notice.strip(),
                "active_nav": "feedback",
                "health": _health_snapshot(),
            },
        )

    @app.post("/rules/activate")
    async def rules_activate(
        rule_pack_id: str = Form(...),
        mode: str = Form(default="canary"),
        canary_pct: str = Form(default="35"),
    ):
        from amg.learning.rule_packs import set_active_rule_pack

        pack_id = str(rule_pack_id or "").strip()
        if not pack_id:
            raise HTTPException(status_code=400, detail="rule_pack_id is required")
        try:
            set_active_rule_pack(
                rule_pack_id=pack_id,
                mode=str(mode or "canary").strip().lower(),
                canary_pct=float(canary_pct or "35"),
            )
            notice = f"Activated rule pack {pack_id}."
        except Exception as e:
            notice = f"Rule activate failed: {e}"
        return RedirectResponse(url=f"/feedback?rule_notice={quote_plus(notice)}", status_code=303)

    @app.post("/rules/rollback")
    async def rules_rollback():
        from amg.learning.rule_promotion import rollback_active_rule_pack

        try:
            rollback_active_rule_pack()
            notice = "Active rule pack rolled back (disabled)."
        except Exception as e:
            notice = f"Rule rollback failed: {e}"
        return RedirectResponse(url=f"/feedback?rule_notice={quote_plus(notice)}", status_code=303)

    @app.post("/rules/eval")
    async def rules_eval(
        rule_pack_id: str = Form(...),
        days: str = Form(default="30"),
    ):
        from amg.learning.rule_promotion import run_rule_eval

        pack_id = str(rule_pack_id or "").strip()
        if not pack_id:
            raise HTTPException(status_code=400, detail="rule_pack_id is required")
        try:
            result = run_rule_eval(rule_pack_id=pack_id, days_back=max(1, _safe_int(days, 30)))
            if result.gates_passed:
                notice = f"Rule eval pass for {pack_id} (run {result.run_id})."
            else:
                blocked = ", ".join(result.blocked_reasons) if result.blocked_reasons else "unknown"
                notice = f"Rule eval blocked for {pack_id} (run {result.run_id}): {blocked}"
        except Exception as e:
            notice = f"Rule eval failed: {e}"
        return RedirectResponse(url=f"/feedback?rule_notice={quote_plus(notice)}", status_code=303)

    @app.post("/rules/promote")
    async def rules_promote(
        run_id: str = Form(...),
        mode: str = Form(default="canary"),
        canary_pct: str = Form(default="35"),
    ):
        from amg.learning.rule_promotion import promote_rule_pack_from_run

        rid = str(run_id or "").strip()
        if not rid:
            raise HTTPException(status_code=400, detail="run_id is required")
        try:
            promotion = promote_rule_pack_from_run(
                run_id=rid,
                mode=str(mode or "canary").strip().lower(),
                canary_pct=float(canary_pct or "35"),
            )
            notice = (
                f"Promoted rule eval run {rid}: mode={promotion.get('mode')} "
                f"canary={promotion.get('canary_pct')}"
            )
        except Exception as e:
            notice = f"Rule promote failed: {e}"
        return RedirectResponse(url=f"/feedback?rule_notice={quote_plus(notice)}", status_code=303)

    # Backward-compatibility aliases: older UI/deploys linked to
    # /covers/review* before scene detail stabilized at /scene/{scene_id}.
    # Keep these redirects so "Review covers" never hard-404s after upgrades.
    @app.get("/covers/review")
    @app.get("/review-covers")
    @app.get("/library/review-covers")
    async def review_covers_legacy(scene_id: str = Query(default=""), job_id: str = Query(default="")):
        target_scene = _safe_scene_id((scene_id or "").strip())
        if not target_scene and job_id:
            with _jobs_lock:
                job = _jobs.get(job_id.strip()) or {}
            target_scene = _safe_scene_id(
                str(job.get("scene_id") or (job.get("result") or {}).get("scene_id") or "").strip()
            )
        if not target_scene:
            raise HTTPException(status_code=404, detail="scene_id not found for legacy review route")
        return RedirectResponse(url=f"/scene/{target_scene}", status_code=307)

    @app.get("/covers/review/{scene_id}")
    async def review_covers_legacy_path(scene_id: str):
        target_scene = _safe_scene_id((scene_id or "").strip())
        if not target_scene:
            raise HTTPException(status_code=404, detail="Invalid scene_id")
        return RedirectResponse(url=f"/scene/{target_scene}", status_code=307)

    @app.get("/scene/{scene_id}", response_class=HTMLResponse)
    async def scene_detail(
        request: Request,
        scene_id: str,
        saved: int = 0,
        rerun_error: str = "",
    ):
        decision_log = _load_decision_log(scene_id)
        reviewed = _load_reviewed(scene_id)
        dlog_work_dir = _work_dir_from_decision_log(decision_log)
        fallback_work_dir = _find_work_dir(scene_id)
        work_dir = _resolve_scene_work_dir(scene_id, decision_log)
        covers = []
        finalized_items: list[dict] = []
        contact_sheet = None
        insight = None
        provided_thumb_report = None
        soft_thumbnail = None
        kept_bundle = {"kept_count": 0, "kept_folder_path": None, "kept_zip_path": None}

        if work_dir:
            covers = _cover_items(scene_id, decision_log, work_dir)
            sheets = sorted(work_dir.glob("00_*_contact_sheet.jpg"))
            if sheets:
                contact_sheet = sheets[0]
            insight = _load_insight(work_dir)
            provided_thumb_report = _load_provided_thumb_report(work_dir)
            soft_thumbnail = _load_soft_thumbnail(work_dir)
            reviewed_kept = []
            if isinstance(reviewed, dict):
                reviewed_kept = reviewed.get("kept_covers") or reviewed.get("selected_covers") or []
            if isinstance(reviewed_kept, list):
                kept_bundle = _build_kept_covers_package(
                    scene_id=scene_id,
                    work_dir=work_dir,
                    cover_items=covers,
                    kept_filenames=[str(x) for x in reviewed_kept if isinstance(x, str)],
                )
                kept_folder_path = kept_bundle.get("kept_folder_path")
                if kept_folder_path and Path(kept_folder_path).is_dir():
                    for p in sorted(Path(kept_folder_path).glob("*.jpg")):
                        finalized_items.append(
                            {
                                "filename": p.name,
                                "path": p,
                            }
                        )
        form_state = _build_review_form_state(reviewed=reviewed, insight=insight, cover_items=covers)
        readiness = _readiness_snapshot(scene_id)
        save_validation = (reviewed or {}).get("metadata_validation") if isinstance(reviewed, dict) else None
        finalized_thumbnails = bool((reviewed or {}).get("finalized_thumbnails"))
        scene_source_name = None
        if isinstance(decision_log, dict):
            scene_path = (decision_log.get("scene_path") or "").strip()
            if scene_path:
                scene_source_name = Path(scene_path).name

        return templates.TemplateResponse(
            request=request,
            name="scene.html",
            context={
                "request": request,
                "scene_id": scene_id,
                "decision_log": decision_log,
                "work_dir": work_dir,
                "covers": covers,
                "contact_sheet": contact_sheet,
                "insight": insight,
                "provided_thumb_report": provided_thumb_report,
                "soft_thumbnail": soft_thumbnail,
                "reviewed": reviewed,
                "form_state": form_state,
                "readiness": readiness,
                "save_validation": save_validation,
                "platform_names": list(PLATFORM_REQUIREMENTS.keys()),
                "platform_rules": _platform_rules_snapshot(),
                "editor_suggestions": _editor_suggestion_seed(decision_log, insight),
                "finalized_thumbnails": finalized_thumbnails,
                "finalized_items": finalized_items,
                "scene_source_name": scene_source_name,
                "kept_count": kept_bundle.get("kept_count", 0),
                "kept_folder_path": kept_bundle.get("kept_folder_path"),
                "kept_zip_path": kept_bundle.get("kept_zip_path"),
                "saved": saved,
                "rerun_error": rerun_error.strip(),
                "active_nav": "library",
                "health": _health_snapshot(),
            },
        )

    @app.post("/scene/{scene_id}/rerun")
    async def rerun_scene(scene_id: str):
        global _job_seq_counter
        target_scene = _safe_scene_id((scene_id or "").strip())
        # #region agent log
        _debug_log_dbg_mode(
            "pre-fix-rerun",
            "H2",
            "amg/ui/app.py:rerun_scene:entry",
            "rerun route hit",
            {"scene_id_raw": scene_id, "target_scene": target_scene},
        )
        # #endregion
        if not target_scene:
            raise HTTPException(status_code=400, detail="scene_id is required")

        decision_log = _load_decision_log(target_scene)
        # #region agent log
        _debug_log_dbg_mode(
            "pre-fix-rerun",
            "H2",
            "amg/ui/app.py:rerun_scene:decision_log",
            "decision log lookup",
            {"found": bool(decision_log), "scene_id": target_scene},
        )
        # #endregion
        if not decision_log:
            return RedirectResponse(
                url=f"/scene/{target_scene}?rerun_error={quote_plus('missing decision log')}",
                status_code=303,
            )

        scene_path = str((decision_log.get("scene_path") or "")).strip()
        if not scene_path:
            return RedirectResponse(
                url=f"/scene/{target_scene}?rerun_error={quote_plus('missing source path')}",
                status_code=303,
            )

        resolved_video = _resolve_video_path(Path(scene_path))
        # #region agent log
        _debug_log_dbg_mode(
            "pre-fix-rerun",
            "H2",
            "amg/ui/app.py:rerun_scene:resolve_video",
            "resolve video path result",
            {"scene_path": scene_path, "resolved_video": str(resolved_video) if resolved_video else None},
        )
        # #endregion
        if resolved_video is None:
            cloud_source = _latest_cloud_source_for_scene(target_scene)
            # #region agent log
            _debug_log_dbg_mode(
                "post-fix-rerun",
                "H5",
                "amg/ui/app.py:rerun_scene:cloud_fallback_lookup",
                "local path missing; cloud fallback lookup",
                {
                    "scene_id": target_scene,
                    "scene_path": scene_path,
                    "cloud_source_found": bool(cloud_source),
                    "cloud_remote": (cloud_source or {}).get("remote") if isinstance(cloud_source, dict) else None,
                },
            )
            # #endregion
            if not cloud_source:
                video_path_hint = _latest_video_path_for_scene(target_scene)
                # #region agent log
                _debug_log_dbg_mode(
                    "post-fix-rerun",
                    "H9",
                    "amg/ui/app.py:rerun_scene:local_fallback_lookup",
                    "cloud fallback missing; local rerun-path lookup",
                    {"scene_id": target_scene, "video_path_hint": video_path_hint},
                )
                # #endregion
                if video_path_hint:
                    resolved_hint = _resolve_video_path(Path(video_path_hint))
                    # #region agent log
                    _debug_log_dbg_mode(
                        "post-fix-rerun",
                        "H9",
                        "amg/ui/app.py:rerun_scene:local_fallback_resolve",
                        "resolved local rerun-path hint",
                        {
                            "scene_id": target_scene,
                            "video_path_hint": video_path_hint,
                            "resolved_hint": str(resolved_hint) if resolved_hint else None,
                        },
                    )
                    # #endregion
                    if resolved_hint is not None:
                        job_id = uuid.uuid4().hex[:10]
                        with _jobs_lock:
                            _job_seq_counter += 1
                            queue_seq = _job_seq_counter
                            _jobs[job_id] = {
                                "job_id": job_id,
                                "status": "queued",
                                "scene_id": target_scene,
                                "video_path": str(resolved_hint),
                                "created_at": datetime.now().isoformat(),
                                "message": f"Queued · rerun(path-fallback) · {target_scene} · priority #{queue_seq}",
                                "result": None,
                                "source_mode": "path",
                                "log_tail": [],
                                "current_phase": None,
                                "progress_pct": 0,
                                "queue_seq": queue_seq,
                            }
                            _job_fifo.append(job_id)
                        _start_dispatcher_if_needed()
                        return RedirectResponse(url=f"/?job_id={job_id}", status_code=303)
                return RedirectResponse(
                    url=f"/scene/{target_scene}?rerun_error={quote_plus('source path is missing or inaccessible')}",
                    status_code=303,
                )

            job_id = uuid.uuid4().hex[:10]
            with _jobs_lock:
                _job_seq_counter += 1
                queue_seq = _job_seq_counter
                _jobs[job_id] = {
                    "job_id": job_id,
                    "status": "queued",
                    "scene_id": target_scene,
                    "video_path": "",
                    "cloud_source": {
                        "remote": cloud_source.get("remote"),
                        "path": cloud_source.get("path"),
                        "scene_id": target_scene,
                        "download_root": cloud_source.get("download_root"),
                        "relative_path": cloud_source.get("relative_path"),
                    },
                    "created_at": datetime.now().isoformat(),
                    "message": f"Queued · rerun(cloud) · {target_scene} · priority #{queue_seq}",
                    "result": None,
                    "source_mode": "cloud",
                    "log_tail": [],
                    "current_phase": None,
                    "progress_pct": 0,
                    "queue_seq": queue_seq,
                }
                _job_fifo.append(job_id)
            # #region agent log
            _debug_log_dbg_mode(
                "post-fix-rerun",
                "H7",
                "amg/ui/app.py:rerun_scene:queued_cloud",
                "rerun queued using cloud source fallback",
                {"job_id": job_id, "scene_id": target_scene, "queue_seq": queue_seq},
            )
            # #endregion
            _start_dispatcher_if_needed()
            return RedirectResponse(url=f"/?job_id={job_id}", status_code=303)

        job_id = uuid.uuid4().hex[:10]
        with _jobs_lock:
            _job_seq_counter += 1
            queue_seq = _job_seq_counter
            _jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "scene_id": target_scene,
                "video_path": str(resolved_video),
                "created_at": datetime.now().isoformat(),
                "message": f"Queued · rerun · {target_scene} · priority #{queue_seq}",
                "result": None,
                "source_mode": "path",
                "log_tail": [],
                "current_phase": None,
                "progress_pct": 0,
                "queue_seq": queue_seq,
            }
            _job_fifo.append(job_id)
        # #region agent log
        _debug_log_dbg_mode(
            "pre-fix-rerun",
            "H4",
            "amg/ui/app.py:rerun_scene:queued",
            "rerun job queued",
            {"job_id": job_id, "scene_id": target_scene, "queue_seq": queue_seq},
        )
        # #endregion
        _start_dispatcher_if_needed()
        return RedirectResponse(url=f"/?job_id={job_id}", status_code=303)

    @app.post("/scene/{scene_id}/insight", response_class=HTMLResponse)
    async def regenerate_insight(request: Request, scene_id: str):
        """HTMX-driven: regenerate AI insight + titles for an existing scene."""
        decision_log = _load_decision_log(scene_id)
        if not decision_log:
            raise HTTPException(404, "No decision log for that scene")
        work_dir = _resolve_scene_work_dir(scene_id, decision_log)
        if not work_dir:
            raise HTTPException(404, "Work directory missing")
        reviewed = _load_reviewed(scene_id)

        form = await request.form()
        title_tone = (form.get("title_tone") or TITLE_TONE_DEFAULT).strip().lower()
        if title_tone not in {"retail_safe", "edgy", "creative", "premium_story"}:
            title_tone = TITLE_TONE_DEFAULT

        try:
            insight = _regenerate_insight_for_scene(decision_log, work_dir, title_tone=title_tone)
        except Exception as e:
            return HTMLResponse(
                f'<div class="empty err">Insight generation failed: {e}</div>',
                status_code=500,
            )

        return templates.TemplateResponse(
            request=request,
            name="_insight_panel.html",
            context={
                "request": request,
                "insight": insight,
                "scene_id": scene_id,
                "form_state": _build_review_form_state(reviewed=reviewed, insight=insight),
            },
        )

    @app.post("/scene/{scene_id}/review")
    async def save_review(scene_id: str, request: Request):
        form = await request.form()
        reviewed_prev = _load_reviewed(scene_id) or {}
        review_action = (form.get("review_action") or "save").strip().lower()
        notes = (form.get("notes") or "").strip()
        title = (form.get("title") or "").strip()
        long_description = (form.get("long_description") or "").strip()
        tags_csv = (form.get("tags_csv") or "").strip()
        categories_csv = (form.get("categories_csv") or "").strip()
        metadata_reason_codes_raw = (form.get("metadata_reason_codes") or "").strip()
        metadata_reason_codes = _parse_reason_codes(
            metadata_reason_codes_raw,
            multi_values=[
                *[str(x) for x in form.getlist("metadata_reason_codes")],
                *[str(x) for x in form.getlist("metadata_reason_codes[]")],
            ],
        )
        target_platforms = [
            p for p in [str(x).upper().strip() for x in form.getlist("target_platforms")]
            if p in PLATFORM_REQUIREMENTS
        ]
        if not target_platforms:
            target_platforms = list(PLATFORM_REQUIREMENTS.keys())
        title_tone = (form.get("title_tone") or TITLE_TONE_DEFAULT).strip().lower()
        if title_tone not in {"retail_safe", "edgy", "creative", "premium_story"}:
            title_tone = TITLE_TONE_DEFAULT

        decision_log = _load_decision_log(scene_id)
        work_dir = _resolve_scene_work_dir(scene_id, decision_log)
        insight = _load_insight(work_dir)
        cover_items = _cover_items(scene_id, decision_log, work_dir)
        soft_thumbnail = _load_soft_thumbnail(work_dir)
        rule_pack_id = str((insight or {}).get("rule_pack_id") or "").strip() or None
        rule_pack_applied = bool((insight or {}).get("rule_pack_applied")) if isinstance(insight, dict) else None
        rule_pack_mode = str((insight or {}).get("rule_pack_mode") or "").strip() or None
        selected = []
        kept_only = []
        per_cover = {}
        for item in cover_items:
            fn = item["filename"]
            d = (form.get(f"decision_{fn}") or "").strip().lower()
            pen = (form.get(f"pen_{fn}") or "").strip().lower()
            pos = (form.get(f"pos_{fn}") or "").strip()
            reason = (form.get(f"reason_{fn}") or "").strip()
            score = (form.get(f"score_{fn}") or "").strip()
            if d in {"keep", "maybe"}:
                selected.append(fn)
            if d == "keep":
                kept_only.append(fn)
            per_cover[fn] = {
                "decision": d or "",
                "pen": pen if pen in {"yes", "no"} else "",
                "pos": pos or "",
                "reason": reason or "",
                "score": score or "",
            }
        # Fallback: recover decisions directly from posted decision_* keys
        # if cover_items mapping missed user selections.
        if not selected:
            for key, val in form.multi_items():
                if not isinstance(key, str) or not key.startswith("decision_"):
                    continue
                fn = key[len("decision_"):].strip()
                if not fn:
                    continue
                d = (val or "").strip().lower()
                row = per_cover.get(fn) or {
                    "decision": "",
                    "pen": "",
                    "pos": "",
                    "reason": "",
                    "score": "",
                }
                row["decision"] = d or ""
                per_cover[fn] = row
                if d in {"keep", "maybe"}:
                    selected.append(fn)
                if d == "keep":
                    kept_only.append(fn)
        if not selected:
            # Backward compatibility with older UI payloads.
            selected = form.getlist("cover")
        if not kept_only and selected:
            # Older payloads don't distinguish keep vs maybe.
            kept_only = list(selected)
        finalized_thumbnails = bool(reviewed_prev.get("finalized_thumbnails")) or (review_action == "finalize")
        feedback_rows = _append_feedback_rows(
            scene_id=scene_id,
            cover_items=cover_items,
            soft_thumbnail=soft_thumbnail,
            form=form,
            title_override=title,
            title_tone=title_tone,
            notes=notes,
            rule_pack_id=rule_pack_id,
            rule_pack_applied=rule_pack_applied,
            rule_pack_mode=rule_pack_mode,
        )
        soft_decision = (form.get("soft_thumb_decision") or "").strip().lower()
        soft_score_raw = (form.get("soft_thumb_score") or "").strip()
        soft_reason = (form.get("soft_thumb_reason") or "").strip()
        soft_score_100 = _parse_user_score_100(soft_score_raw)
        metadata_validation = validate_metadata_for_platforms(
            title_text=title,
            long_description=long_description,
            tags=_parse_csv_tokens(tags_csv, lowercase=True),
            categories=_parse_csv_tokens(categories_csv, title_case=True),
            target_platforms=target_platforms,
            performers=[],
            decision_log=decision_log or {},
        )
        learning_signals = _build_metadata_learning_signals(
            insight=insight,
            title_text=title,
            long_description=long_description,
            tags_csv=tags_csv,
            categories_csv=categories_csv,
            reason_codes=metadata_reason_codes,
        )

        REVIEWED_DIR.mkdir(parents=True, exist_ok=True)
        sid = _safe_scene_id(scene_id)
        out = REVIEWED_DIR / f"{sid}.json"
        payload = {
            "scene_id": scene_id,
            "timestamp": _utc_now_isoz(),
            "selected_covers": selected,
            "kept_covers": kept_only,
            "finalized_thumbnails": finalized_thumbnails,
            "per_cover": per_cover,
            "title_override": title or None,
            "title_tone": title_tone,
            "long_description": long_description or None,
            "tags_csv": tags_csv or None,
            "categories_csv": categories_csv or None,
            "target_platforms": target_platforms,
            "metadata_validation": metadata_validation,
            "notes": notes or None,
            "rule_pack_id": rule_pack_id,
            "rule_pack_applied": rule_pack_applied,
            "rule_pack_mode": rule_pack_mode,
            "soft_thumbnail_review": {
                "decision": soft_decision if soft_decision in {"keep", "reject"} else None,
                "score_100": soft_score_100,
                "reason": soft_reason or None,
            },
            "title_edit_distance": learning_signals.get("title_edit_distance"),
            "description_edit_distance": learning_signals.get("description_edit_distance"),
            "tags_edit_distance": learning_signals.get("tags_edit_distance"),
            "categories_edit_distance": learning_signals.get("categories_edit_distance"),
            "metadata_acceptance_label": learning_signals.get("metadata_acceptance_label"),
            "metadata_edit_reason_codes": learning_signals.get("metadata_edit_reason_codes"),
            "source": "amg_ui_v0",
            "feedback_rows_written": feedback_rows,
        }
        with open(out, "w") as f:
            json.dump(payload, f, indent=2)
        _persist_review_to_decision_log(
            scene_id=scene_id,
            title_tone=title_tone,
            title_override=title or None,
            long_description=long_description or None,
            selected_covers=selected,
            kept_covers=kept_only,
            finalized_thumbnails=finalized_thumbnails,
            per_cover=per_cover,
            tags_csv=tags_csv or None,
            categories_csv=categories_csv or None,
            rule_pack_id=rule_pack_id,
            rule_pack_applied=rule_pack_applied,
            rule_pack_mode=rule_pack_mode,
            soft_thumbnail_review={
                "decision": soft_decision if soft_decision in {"keep", "reject"} else None,
                "score_100": soft_score_100,
                "reason": soft_reason or None,
            },
            metadata_learning_signals=learning_signals,
        )
        _build_kept_covers_package(
            scene_id=scene_id,
            work_dir=work_dir,
            cover_items=cover_items,
            kept_filenames=kept_only,
        )
        try:
            # Keep retrieval corpus warm as fresh operator-approved rows land.
            export_approved_example_bank(days_back=365)
        except Exception as e:
            log.warn("Example-bank refresh failed after review save", scene_id=scene_id, error=str(e))

        return RedirectResponse(url=f"/scene/{scene_id}?saved=1", status_code=303)

    @app.get("/artifact")
    async def artifact(path: str = Query(..., description="Absolute path to local artifact")):
        p = Path(path).expanduser().resolve()
        if not p.exists() or not p.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        if not _path_within_roots(p, _artifact_allowed_roots()):
            raise HTTPException(status_code=403, detail="Path outside allowed artifact roots")
        return FileResponse(p)

    @app.get("/artifact-zip")
    async def artifact_zip(path: str = Query(..., description="Absolute path to local folder artifact")):
        p = Path(path).expanduser().resolve()
        if not p.exists() or not p.is_dir():
            raise HTTPException(status_code=404, detail="Folder not found")
        if not _path_within_roots(p, _artifact_allowed_roots()):
            raise HTTPException(status_code=403, detail="Path outside allowed artifact roots")

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp_path = Path(tmp.name)
        tmp.close()
        try:
            with zipfile.ZipFile(tmp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(p):
                    root_path = Path(root)
                    for name in files:
                        src = root_path / name
                        rel = src.relative_to(p)
                        zf.write(src, arcname=str(rel))
        except Exception as e:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
            raise HTTPException(status_code=500, detail=f"Failed to build zip: {e}")

        return FileResponse(
            tmp_path,
            filename=f"{p.name}.zip",
            media_type="application/zip",
            background=BackgroundTask(lambda: tmp_path.unlink(missing_ok=True)),
        )

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "ts": time.time(), "snapshot": _health_snapshot()}

    return app
