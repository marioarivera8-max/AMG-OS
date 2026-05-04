from __future__ import annotations

import json
import re
import shutil
import socket
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from amg.config import (
    DATA_DIR,
    DECISION_LOGS_DIR,
    REVIEWED_DIR,
    OPERATOR_FEEDBACK_DIR,
    OPERATOR_FEEDBACK_PATH,
    VISION_MODEL,
)
from amg.ingest.inventory import VIDEO_EXTENSIONS, discover_scenes
from amg.learning.feedback_eval import evaluate_feedback
from amg.pipeline import process_scene
from amg.video.metadata import get_metadata

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
UPLOADS_DIR = DATA_DIR / "ui_uploads"
RUN_LOGS_DIR = DATA_DIR / "logs" / "runs"

# Canonical pipeline phases (used to render phase pills + progress %).
PHASES: List[str] = [
    "ingest",
    "calibration",
    "tier_scan",
    "finish_hunter",
    "buildup_hunter",
    "cluster",
    "position_classifier",
    "quota_fill",
    "output",
]
_PHASE_RE = re.compile(r"\[(" + "|".join(PHASES) + r")\]")

_jobs_lock = threading.Lock()
_jobs: Dict[str, dict] = {}

# Cached health snapshot (refreshed lazily; cheap probes).
_health_lock = threading.Lock()
_health_cache: dict = {}
_health_ts: float = 0.0


# ---------- helpers ----------

def _safe_scene_id(scene_id: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in scene_id)[:120]


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
    except Exception:
        return None


def _all_decision_logs() -> List[dict]:
    if not DECISION_LOGS_DIR.exists():
        return []
    out = []
    for p in sorted(DECISION_LOGS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            with open(p) as f:
                out.append(json.load(f))
        except Exception:
            continue
    return out


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
    preview_path = saved_covers[0]["path"] if saved_covers else None
    sid = d.get("scene_id") or ""
    status, status_cls = _scene_status(d)
    duration = (d.get("input") or {}).get("duration_sec") or 0
    return {
        "scene_id": sid,
        "studio": (d.get("input") or {}).get("studio") or "",
        "covers": out.get("covers_delivered", 0),
        "top_score": out.get("top_pick_score", 0),
        "duration_sec": duration,
        "duration_str": _humanize_duration(duration),
        "tags": (d.get("input") or {}).get("genres") or [],
        "timestamp": d.get("timestamp_processed") or "",
        "processed_ago": _humanize_ago(d.get("timestamp_processed") or ""),
        "preview_path": preview_path,
        "grad_idx": _grad_idx(sid),
        "status": status,
        "status_cls": status_cls,
    }


def _recent_scenes(limit: int = 20) -> list[dict]:
    return [_scene_summary(d) for d in _all_decision_logs()[:limit]]


def _find_work_dir(scene_id: str) -> Optional[Path]:
    home = Path.home()
    roots = [home / "AMG_Processing", home / "AMG_OS" / "incoming", UPLOADS_DIR]
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


def _regenerate_insight_for_scene(decision_log: dict, work_dir: Path) -> dict:
    """Run scene insight + AI titles for an already-processed scene and write
    the result back to ``insight.json``. Returns the merged dict."""
    from amg.ingest.folder_context import resolve_folder_context
    from amg.ingest.title_parser import parse_title_with_context, derive_primary_scene_type
    from amg.ingest.performer_code import (
        parse_performer_code_with_context,
        detect_scene_type_from_code,
    )
    from amg.ingest.studio_profiles import detect_studio
    from amg.scoring.scene_describer import (
        describe_scene_from_covers,
        generate_titles_with_insight,
        summarize_positions,
    )

    scene_path = decision_log.get("scene_path")
    if not scene_path:
        raise RuntimeError("decision_log missing scene_path")
    video_path = Path(scene_path)

    folder_ctx = resolve_folder_context(video_path)
    studio = folder_ctx.studio or detect_studio(video_path)
    code_info = parse_performer_code_with_context(video_path, folder_ctx)
    title_info = parse_title_with_context(video_path, folder_ctx)
    primary = derive_primary_scene_type(
        title_info.get("detected_genres", []),
        code_info.get("total") if code_info else None,
    )
    if code_info:
        from_code = detect_scene_type_from_code(code_info)
        if from_code != "STANDARD":
            primary = from_code

    saved_covers = (decision_log.get("outcomes") or {}).get("saved_covers", []) or []
    contact_sheets = sorted(work_dir.glob("00_*_contact_sheet.jpg"))
    contact_sheet = contact_sheets[0] if contact_sheets else None

    insight_obj = describe_scene_from_covers(
        contact_sheet_path=contact_sheet,
        cover_paths=[Path(c["path"]) for c in saved_covers if c.get("path")],
    )
    pos_summary = summarize_positions(saved_covers)
    payload = generate_titles_with_insight(
        studio=studio,
        performers=folder_ctx.performers,
        scene_type=primary,
        genres=title_info.get("detected_genres", []),
        description=title_info.get("description") or folder_ctx.title or "",
        insight=insight_obj,
        position_summary=pos_summary,
    )

    out = {
        "studio": studio,
        "performers": folder_ctx.performers,
        "scene_type": primary,
        "genres": title_info.get("detected_genres", []),
        "operator_description": title_info.get("description") or "",
        "folder_context": {
            "is_generic_filename": folder_ctx.is_generic_filename,
            "source_folder": str(folder_ctx.source_folder) if folder_ctx.source_folder else None,
            "metadata_documents_found": len(folder_ctx.metadata_documents),
            "ancestor_names": folder_ctx.ancestor_names,
        },
        "insight": insight_obj.to_dict() if insight_obj else None,
        "position_summary": pos_summary,
        "ai_titles": payload["titles"],
        "long_description": payload.get("long_description", ""),
        "ai_used": payload.get("ai_used", False),
    }
    work_dir.mkdir(parents=True, exist_ok=True)
    with open(work_dir / "insight.json", "w") as f:
        json.dump(out, f, indent=2)
    return out


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
        out.append(
            {
                "filename": p.name,
                "path": p,
                "model_type": meta.get("type", "UNKNOWN"),
                "model_position": meta.get("position_label", "OTHER"),
                "model_position_conf": meta.get("position_label_confidence", 0.0),
                "model_pen_visible": meta.get("penetration_visible", False),
                "model_pen_conf": meta.get("penetration_confidence", 0.0),
                "score": meta.get("score", None),
                "timestamp_sec": meta.get("timestamp_sec", 0) or 0,
            }
        )
    return out


def _append_feedback_rows(
    *,
    scene_id: str,
    cover_items: list[dict],
    form,
    title_override: Optional[str],
    notes: Optional[str],
) -> int:
    OPERATOR_FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    rows_written = 0
    item_by_name = {i["filename"]: i for i in cover_items}
    ts = datetime.utcnow().isoformat() + "Z"

    with open(OPERATOR_FEEDBACK_PATH, "a") as f:
        for filename, item in item_by_name.items():
            user_pen = (form.get(f"pen_{filename}") or "").strip().lower()
            user_pos = (form.get(f"pos_{filename}") or "").strip().upper()
            user_decision = (form.get(f"decision_{filename}") or "").strip().lower()
            user_reason = (form.get(f"reason_{filename}") or "").strip()

            if not any([user_pen, user_pos, user_decision, user_reason]):
                continue

            row = {
                "timestamp": ts,
                "scene_id": scene_id,
                "filename": filename,
                "timestamp_sec": item.get("timestamp_sec"),
                "model": {
                    "type": item.get("model_type"),
                    "position_label": item.get("model_position"),
                    "position_confidence": item.get("model_position_conf"),
                    "penetration_visible": item.get("model_pen_visible"),
                    "penetration_confidence": item.get("model_pen_conf"),
                },
                "operator": {
                    "penetration_visible": user_pen if user_pen in {"yes", "no"} else None,
                    "position_label": user_pos or None,
                    "decision": user_decision if user_decision in {"keep", "reject", "maybe"} else None,
                    "reason": user_reason or None,
                    "title_override": title_override or None,
                    "notes": notes or None,
                },
            }
            f.write(json.dumps(row) + "\n")
            rows_written += 1
    return rows_written


# ---------- job execution + live tracking ----------

def _run_job(job_id: str) -> None:
    with _jobs_lock:
        job = _jobs[job_id]
        job["status"] = "running"
        job["started_at_ts"] = time.time()
        job["started_at"] = datetime.now().isoformat()
        video_path = Path(job["video_path"])

    try:
        _start_live_log_tail(job_id)
        result = process_scene(video_path)
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
    except Exception as e:
        with _jobs_lock:
            job = _jobs[job_id]
            job["status"] = "error"
            job["finished_at"] = datetime.now().isoformat()
            job["finished_at_ts"] = time.time()
            job["message"] = str(e)


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
            except Exception:
                pass

            time.sleep(1.0)

    t = threading.Thread(target=_watch, daemon=True)
    t.start()


def _decorate_job(job: dict) -> dict:
    """Add display-time metadata: phases, progress, elapsed/eta strings."""
    j = dict(job)
    status = j.get("status")
    cur = j.get("current_phase")
    cur_idx = PHASES.index(cur) if cur in PHASES else -1

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
        host, _, port = "127.0.0.1:11434".partition(":")
        s = socket.create_connection((host, int(port or "11434")), timeout=timeout)
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

def _feedback_page_data() -> dict:
    metrics = evaluate_feedback()
    rows: list[dict] = []
    decision_counts = {"keep": 0, "reject": 0, "maybe": 0}
    position_counts: dict[str, int] = {}
    if OPERATOR_FEEDBACK_PATH.exists():
        with open(OPERATOR_FEEDBACK_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    for r in rows:
        op = (r.get("operator") or {})
        d = op.get("decision")
        if d in decision_counts:
            decision_counts[d] += 1
        pos = (op.get("position_label") or "").upper()
        if pos:
            position_counts[pos] = position_counts.get(pos, 0) + 1

    # Build "disagreements" recent-rows table
    recent_rows = []
    for r in reversed(rows[-30:]):
        model = r.get("model") or {}
        op = (r.get("operator") or {})
        ts = r.get("timestamp", "")[:19].replace("T", " ")
        sid = r.get("scene_id", "")

        op_pen = op.get("penetration_visible")
        if op_pen in ("yes", "no"):
            model_pen = bool(model.get("penetration_visible"))
            mv = "yes" if model_pen else "no"
            match = (op_pen == mv)
            recent_rows.append(
                {
                    "scene_id": sid,
                    "timestamp_short": ts,
                    "field": "penetration",
                    "model_value": mv,
                    "operator_value": op_pen,
                    "match_cls": "ok" if match else "err",
                }
            )
        op_pos = (op.get("position_label") or "").upper()
        if op_pos:
            model_pos = (model.get("position_label") or "").upper() or "OTHER"
            match = (op_pos == model_pos)
            recent_rows.append(
                {
                    "scene_id": sid,
                    "timestamp_short": ts,
                    "field": "position",
                    "model_value": model_pos,
                    "operator_value": op_pos,
                    "match_cls": "ok" if match else "err",
                }
            )
        if len(recent_rows) >= 25:
            break

    by_scene = (metrics.get("rows_by_scene") or {})
    scenes_by_count = [{"scene_id": k, "count": v} for k, v in sorted(by_scene.items(), key=lambda x: x[1], reverse=True)[:8]]

    pos_total = sum(position_counts.values()) or 1
    position_list = [
        {"label": k, "count": v, "pct": round(v / pos_total * 100, 1)}
        for k, v in sorted(position_counts.items(), key=lambda x: x[1], reverse=True)
    ]

    return {
        "metrics": metrics,
        "recent_rows": recent_rows,
        "scenes_by_count": scenes_by_count,
        "position_counts": position_list,
        "decision_counts": decision_counts,
        "feedback_path": str(OPERATOR_FEEDBACK_PATH),
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
    }

    # filter
    q = (filt.get("q") or "").strip().lower()
    studio = (filt.get("studio") or "").strip()
    status = (filt.get("status") or "").strip().lower()
    min_score = filt.get("min_score")

    filtered = []
    for s in summaries:
        if q and q not in s["scene_id"].lower() and q not in (s["studio"] or "").lower():
            continue
        if studio and s["studio"] != studio:
            continue
        if status:
            wanted = {
                "ready": "REVIEWED",
                "review": "REVIEW",
                "draft": "DRAFT",
                "uploaded": "UPLOADED",
            }.get(status)
            if wanted and s["status"] != wanted:
                continue
        if min_score is not None and min_score != "":
            try:
                if float(s["top_score"] or 0) < float(min_score):
                    continue
            except Exception:
                pass
        filtered.append(s)

    return {
        "scenes": filtered,
        "studios": studios,
        "stats": stats,
        "filter": filt,
    }


# ---------- app ----------

def create_app() -> FastAPI:
    app = FastAPI(title="AMG UI", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        with _jobs_lock:
            jobs = list(_jobs.values())[-20:]
        jobs = [_decorate_job(j) for j in reversed(jobs)]
        recent_scenes = _recent_scenes(limit=12)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "request": request,
                "jobs": jobs,
                "recent_scenes": recent_scenes,
                "active_nav": "process",
                "health": _health_snapshot(),
            },
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
        scene_id = video_path.parent.name
        job = {
            "job_id": job_id,
            "status": "queued",
            "scene_id": scene_id,
            "video_path": str(video_path),
            "created_at": datetime.now().isoformat(),
            "message": "Queued",
            "result": None,
            "source_mode": source_mode,
            "log_tail": [],
            "current_phase": None,
            "progress_pct": 0,
        }
        if upload_stats:
            chosen_name = Path(upload_stats["chosen_video"]).name if upload_stats.get("chosen_video") else "(none)"
            job["message"] = (
                f"Queued · uploaded {upload_stats['files_uploaded']} files, "
                f"found {upload_stats['videos_found']} videos, selected longest: {chosen_name}"
            )
            job["upload_stats"] = upload_stats
        with _jobs_lock:
            _jobs[job_id] = job

        t = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
        t.start()

        return templates.TemplateResponse(
            request=request,
            name="_job_card.html",
            context={"request": request, "job": _decorate_job(job), "health": _health_snapshot()},
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

    @app.get("/library", response_class=HTMLResponse)
    async def library(
        request: Request,
        q: str = Query(default=""),
        studio: str = Query(default=""),
        status: str = Query(default=""),
        min_score: str = Query(default=""),
    ):
        filt = {"q": q, "studio": studio, "status": status, "min_score": min_score}
        data = _library_data(filt)
        return templates.TemplateResponse(
            request=request,
            name="library.html",
            context={
                "request": request,
                **data,
                "active_nav": "library",
                "health": _health_snapshot(),
            },
        )

    @app.get("/feedback", response_class=HTMLResponse)
    async def feedback(request: Request):
        data = _feedback_page_data()
        return templates.TemplateResponse(
            request=request,
            name="feedback.html",
            context={
                "request": request,
                **data,
                "active_nav": "feedback",
                "health": _health_snapshot(),
            },
        )

    @app.get("/scene/{scene_id}", response_class=HTMLResponse)
    async def scene_detail(request: Request, scene_id: str, saved: int = 0):
        decision_log = _load_decision_log(scene_id)
        work_dir = _work_dir_from_decision_log(decision_log) or _find_work_dir(scene_id)
        covers = []
        contact_sheet = None
        insight = None
        provided_thumb_report = None

        if work_dir:
            covers = _cover_items(scene_id, decision_log, work_dir)
            sheets = sorted(work_dir.glob("00_*_contact_sheet.jpg"))
            if sheets:
                contact_sheet = sheets[0]
            insight = _load_insight(work_dir)
            provided_thumb_report = _load_provided_thumb_report(work_dir)

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
                "saved": saved,
                "active_nav": "library",
                "health": _health_snapshot(),
            },
        )

    @app.post("/scene/{scene_id}/insight", response_class=HTMLResponse)
    async def regenerate_insight(request: Request, scene_id: str):
        """HTMX-driven: regenerate AI insight + titles for an existing scene."""
        decision_log = _load_decision_log(scene_id)
        if not decision_log:
            raise HTTPException(404, "No decision log for that scene")
        work_dir = _work_dir_from_decision_log(decision_log) or _find_work_dir(scene_id)
        if not work_dir:
            raise HTTPException(404, "Work directory missing")

        try:
            insight = _regenerate_insight_for_scene(decision_log, work_dir)
        except Exception as e:
            return HTMLResponse(
                f'<div class="empty err">Insight generation failed: {e}</div>',
                status_code=500,
            )

        return templates.TemplateResponse(
            request=request,
            name="_insight_panel.html",
            context={"request": request, "insight": insight, "scene_id": scene_id},
        )

    @app.post("/scene/{scene_id}/review")
    async def save_review(scene_id: str, request: Request):
        form = await request.form()
        selected = form.getlist("cover")
        notes = (form.get("notes") or "").strip()
        title = (form.get("title") or "").strip()
        long_description = (form.get("long_description") or "").strip()

        decision_log = _load_decision_log(scene_id)
        work_dir = _work_dir_from_decision_log(decision_log) or _find_work_dir(scene_id)
        cover_items = _cover_items(scene_id, decision_log, work_dir)
        feedback_rows = _append_feedback_rows(
            scene_id=scene_id,
            cover_items=cover_items,
            form=form,
            title_override=title,
            notes=notes,
        )

        REVIEWED_DIR.mkdir(parents=True, exist_ok=True)
        sid = _safe_scene_id(scene_id)
        out = REVIEWED_DIR / f"{sid}.json"
        payload = {
            "scene_id": scene_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "selected_covers": selected,
            "title_override": title or None,
            "long_description": long_description or None,
            "notes": notes or None,
            "source": "amg_ui_v0",
            "feedback_rows_written": feedback_rows,
        }
        with open(out, "w") as f:
            json.dump(payload, f, indent=2)

        return RedirectResponse(url=f"/scene/{scene_id}?saved=1", status_code=303)

    @app.get("/artifact")
    async def artifact(path: str = Query(..., description="Absolute path to local artifact")):
        p = Path(path).expanduser().resolve()
        if not p.exists() or not p.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        if Path.home().resolve() not in p.parents and p != Path.home().resolve():
            raise HTTPException(status_code=403, detail="Path outside home directory")
        return FileResponse(p)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "ts": time.time(), "snapshot": _health_snapshot()}

    return app
