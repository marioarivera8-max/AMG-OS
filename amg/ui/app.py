from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from amg.config import DECISION_LOGS_DIR, REVIEWED_DIR
from amg.ingest.inventory import discover_scenes
from amg.pipeline import process_scene

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_jobs_lock = threading.Lock()
_jobs: Dict[str, dict] = {}


def _safe_scene_id(scene_id: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in scene_id)[:120]


def _resolve_video_path(user_path: Path) -> Optional[Path]:
    path = user_path.expanduser().resolve()
    if not path.exists():
        return None
    if path.is_file():
        return path
    videos = discover_scenes(path, recursive=False)
    if not videos:
        return None
    return videos[0]


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


def _find_work_dir(scene_id: str) -> Optional[Path]:
    home = Path.home()
    roots = [home / "AMG_Processing", home / "AMG_OS" / "incoming"]
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


def _run_job(job_id: str) -> None:
    with _jobs_lock:
        job = _jobs[job_id]
        job["status"] = "running"
        job["started_at"] = datetime.now().isoformat()
        video_path = Path(job["video_path"])

    try:
        result = process_scene(video_path)
        with _jobs_lock:
            job = _jobs[job_id]
            job["result"] = result
            job["scene_id"] = result.get("scene_id")
            job["status"] = "done" if result.get("success") else "error"
            job["finished_at"] = datetime.now().isoformat()
            job["message"] = (
                f"Done: {result.get('covers_saved', 0)} covers"
                if result.get("success")
                else f"Failed: {','.join(result.get('error_codes', [])) or 'unknown error'}"
            )
    except Exception as e:
        with _jobs_lock:
            job = _jobs[job_id]
            job["status"] = "error"
            job["finished_at"] = datetime.now().isoformat()
            job["message"] = str(e)


def create_app() -> FastAPI:
    app = FastAPI(title="AMG UI", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        with _jobs_lock:
            jobs = list(_jobs.values())[-20:]
        jobs = list(reversed(jobs))
        return templates.TemplateResponse("index.html", {"request": request, "jobs": jobs})

    @app.post("/jobs", response_class=HTMLResponse)
    async def create_job(request: Request, path: str = Form(...)):
        video_path = _resolve_video_path(Path(path))
        if video_path is None:
            raise HTTPException(status_code=400, detail=f"No valid video found at: {path}")

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
        }
        with _jobs_lock:
            _jobs[job_id] = job

        t = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
        t.start()

        return templates.TemplateResponse("_job_card.html", {"request": request, "job": job})

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    async def job_card(request: Request, job_id: str):
        with _jobs_lock:
            job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return templates.TemplateResponse("_job_card.html", {"request": request, "job": job})

    @app.get("/scene/{scene_id}", response_class=HTMLResponse)
    async def scene_detail(request: Request, scene_id: str, saved: int = 0):
        decision_log = _load_decision_log(scene_id)
        work_dir = _find_work_dir(scene_id)
        covers = []
        contact_sheet = None

        if work_dir:
            covers_dir = work_dir / "covers"
            if covers_dir.exists():
                covers = sorted(covers_dir.glob("*.jpg"))
            sheets = sorted(work_dir.glob("00_*_contact_sheet.jpg"))
            if sheets:
                contact_sheet = sheets[0]

        return templates.TemplateResponse(
            "scene.html",
            {
                "request": request,
                "scene_id": scene_id,
                "decision_log": decision_log,
                "work_dir": work_dir,
                "covers": covers,
                "contact_sheet": contact_sheet,
                "saved": saved,
            },
        )

    @app.post("/scene/{scene_id}/review")
    async def save_review(scene_id: str, request: Request):
        form = await request.form()
        selected = form.getlist("cover")
        notes = (form.get("notes") or "").strip()
        title = (form.get("title") or "").strip()

        REVIEWED_DIR.mkdir(parents=True, exist_ok=True)
        sid = _safe_scene_id(scene_id)
        out = REVIEWED_DIR / f"{sid}.json"
        payload = {
            "scene_id": scene_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "selected_covers": selected,
            "title_override": title or None,
            "notes": notes or None,
            "source": "amg_ui_v0",
        }
        with open(out, "w") as f:
            json.dump(payload, f, indent=2)

        return RedirectResponse(url=f"/scene/{scene_id}?saved=1", status_code=303)

    @app.get("/artifact")
    async def artifact(path: str = Query(..., description="Absolute path to local artifact")):
        p = Path(path).expanduser().resolve()
        if not p.exists() or not p.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        # Localhost-only app; keep a conservative path boundary.
        if Path.home().resolve() not in p.parents and p != Path.home().resolve():
            raise HTTPException(status_code=403, detail="Path outside home directory")
        return FileResponse(p)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "ts": time.time()}

    return app

