"""
Pod-side worker for the AMG cloud-hosted edition.

This runs INSIDE the Runpod GPU container (built from the repo Dockerfile,
commit 8f8cef0). Its only client is the controller VM's dispatcher; every
endpoint except ``/healthz`` is gated by a per-pod bearer token that the
controller passes in via ``AMG_POD_AUTH_TOKEN`` when the pod is provisioned.

The worker accepts a video upload, runs ``amg.pipeline.process_scene``
against it in a background thread, and lets the controller poll for status
and pull back the resulting work-dir as a zip.

Endpoints:

* ``POST /jobs`` (multipart) — start a job for an uploaded video. Returns
  ``{job_id, status: "queued"}``. The video is saved into
  ``AMG_DATA_DIR/pod_uploads/<job_id>/`` and the pipeline runs against it.
* ``GET /jobs/{job_id}`` — poll status + log tail + result summary.
* ``GET /jobs/{job_id}/zip`` — stream the entire scene work-dir (the
  ``out/...`` folder produced by the pipeline) back as a zip. The
  controller pulls this once the job is done.
* ``GET /healthz`` (unauthenticated) — readiness probe for Runpod's proxy.

Why a separate FastAPI app instead of reusing the v11.3 UI:

The UI is HTML-first (templates, navigation, login) and assumes a single
operator browsing it. The pod worker is API-only with one client (the
controller) and must run in environments without templates/static assets.
Keeping it slim also makes the pod container start faster.

Configuration (env):

* ``AMG_POD_AUTH_TOKEN`` (required at startup) — bearer token. Generate
  with ``python -c 'import secrets; print(secrets.token_urlsafe(48))'``.
  Refusing to start without it makes accidental "open to the internet"
  deployments impossible.
* ``AMG_POD_PORT`` — bind port (default 8000). Must match the
  ``ports="8000/http"`` declared in ``RunpodClient.create_pod``.
* ``AMG_POD_BIND`` — bind address (default ``0.0.0.0``).
* ``AMG_POD_MAX_JOBS_RETAINED`` — keep the latest N jobs in memory
  (default 50). Older job state is evicted, but their on-disk artifacts
  remain until the pod is destroyed.
"""
from __future__ import annotations

import io
import os
import secrets as _secrets
import threading
import time
import uuid
import zipfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware

from amg.config import DATA_DIR
from amg.utils.logging import get_logger

log = get_logger("amg.cloud.pod_worker")

# ---------- module state ----------

POD_UPLOADS_DIR = DATA_DIR / "pod_uploads"

_AUTH_EXEMPT_PATHS = {"/healthz"}

# Per-job log-tail buffer size. Long enough to be useful for debugging,
# small enough that 50 jobs in memory ~= 50 * 200 lines * ~200 bytes = 2 MB.
LOG_TAIL_LINES = 200


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- auth ----------


def _required_token() -> str:
    raw = os.environ.get("AMG_POD_AUTH_TOKEN", "").strip()
    if not raw:
        raise RuntimeError(
            "AMG_POD_AUTH_TOKEN is not set. The pod worker refuses to start "
            "without a bearer token. Generate one and pass it via env when "
            "provisioning the pod: "
            "python -c 'import secrets; print(secrets.token_urlsafe(48))'"
        )
    if len(raw) < 32:
        raise RuntimeError(
            "AMG_POD_AUTH_TOKEN is too short (need >=32 chars). The pod is "
            "reachable from the public internet via the Runpod proxy URL."
        )
    return raw


class _BearerTokenMiddleware(BaseHTTPMiddleware):
    """Require ``Authorization: Bearer <token>`` on every non-exempt route.

    Constant-time comparison guards against timing oracles. ``/healthz`` is
    exempt so Runpod's own proxy can probe pod liveness without holding a
    token (and nothing it returns is useful to an attacker)."""

    def __init__(self, app, expected_token: str) -> None:
        super().__init__(app)
        self._expected = expected_token

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return JSONResponse({"error": "missing bearer token"}, status_code=401)
        presented = header[len("Bearer "):].strip()
        if not _secrets.compare_digest(presented, self._expected):
            return JSONResponse({"error": "invalid token"}, status_code=401)
        return await call_next(request)


# ---------- job tracker ----------


class _JobTracker:
    """In-memory job registry. State dies with the pod; that's by design —
    the controller is the source of truth for cross-pod job history."""

    def __init__(self, max_retained: int = 50) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._order: Deque[str] = deque()
        self._lock = threading.Lock()
        self._max_retained = max_retained

    def create(self, job_id: str, *, scene_id: str, video_path: Path, work_dir: Path) -> Dict[str, Any]:
        with self._lock:
            job = {
                "job_id": job_id,
                "status": "queued",
                "scene_id": scene_id,
                "video_path": str(video_path),
                "work_dir": str(work_dir),
                "created_at": _utcnow_iso(),
                "started_at": None,
                "finished_at": None,
                "log_tail": deque(maxlen=LOG_TAIL_LINES),
                "result": None,
                "error": None,
                "progress_pct": 0,
            }
            self._jobs[job_id] = job
            self._order.append(job_id)
            while len(self._order) > self._max_retained:
                evict = self._order.popleft()
                self._jobs.pop(evict, None)
            return job

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            j = self._jobs.get(job_id)
            if j is None:
                return
            j.update(fields)

    def append_log(self, job_id: str, line: str) -> None:
        with self._lock:
            j = self._jobs.get(job_id)
            if j is None:
                return
            j["log_tail"].append(line)

    def serialize(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            j = self._jobs.get(job_id)
            if j is None:
                return None
            out = dict(j)
            out["log_tail"] = list(j["log_tail"])
            return out

    def list_ids(self) -> List[str]:
        with self._lock:
            return list(self._order)


# ---------- runner ----------


def _run_pipeline_in_thread(tracker: _JobTracker, job_id: str, video_path: Path) -> None:
    """Run amg.pipeline.process_scene in a background thread. Writes status
    transitions and the final result back into the tracker."""
    tracker.update(
        job_id,
        status="running",
        started_at=_utcnow_iso(),
    )
    tracker.append_log(job_id, f"[pod-worker] starting process_scene for {video_path}")
    try:
        from amg.pipeline import process_scene  # imported here to keep startup cheap

        result = process_scene(video_path)
        tracker.update(
            job_id,
            status="done" if result.get("success") else "error",
            finished_at=_utcnow_iso(),
            result=result,
            progress_pct=100,
        )
        tracker.append_log(
            job_id,
            f"[pod-worker] process_scene finished: success={result.get('success')} "
            f"covers_saved={result.get('covers_saved')}",
        )
    except Exception as exc:  # noqa: BLE001 - any pipeline failure must surface as error status
        tracker.update(
            job_id,
            status="error",
            finished_at=_utcnow_iso(),
            error=str(exc),
            progress_pct=0,
        )
        tracker.append_log(job_id, f"[pod-worker] pipeline raised: {exc}")
        log.error(f"Job {job_id} failed: {exc}")


# ---------- zip streaming ----------


def _stream_dir_as_zip(directory: Path):
    """Yield a zip archive of ``directory`` (recursive). The zip is built
    in a single pass into a BytesIO so the controller can stream-download
    it without the worker holding the whole archive in memory longer than
    the duration of the response."""
    if not directory.exists() or not directory.is_dir():
        raise HTTPException(status_code=404, detail=f"work dir not found: {directory}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(directory.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=p.relative_to(directory))
    buf.seek(0)
    yield from iter(lambda: buf.read(64 * 1024), b"")


# ---------- app factory ----------


def create_app(*, auth_token: Optional[str] = None, tracker: Optional[_JobTracker] = None) -> FastAPI:
    """Build the pod-worker FastAPI app.

    ``auth_token`` defaults to the value of AMG_POD_AUTH_TOKEN; passing it
    explicitly is mainly for tests. ``tracker`` lets tests inject their own
    registry; production uses a fresh one per process."""
    token = (auth_token or _required_token())
    track = tracker if tracker is not None else _JobTracker(
        max_retained=int(os.environ.get("AMG_POD_MAX_JOBS_RETAINED", "50")),
    )

    app = FastAPI(title="AMG pod worker", docs_url=None, redoc_url=None)
    app.add_middleware(_BearerTokenMiddleware, expected_token=token)

    @app.get("/healthz")
    async def healthz() -> Dict[str, Any]:
        # Intentionally minimal — this endpoint is unauthenticated so Runpod's
        # proxy can probe liveness. Don't leak version numbers, env, etc.
        return {"ok": True}

    @app.post("/jobs")
    async def create_job(
        video: UploadFile = File(...),
        scene_id: Optional[str] = Form(None),
    ) -> Dict[str, Any]:
        if not video.filename:
            raise HTTPException(status_code=400, detail="missing video filename")
        job_id = uuid.uuid4().hex[:12]
        # Each job gets its own folder so multiple jobs on the same warm pod
        # don't clobber each other's uploads. The folder doubles as the work
        # dir parent so process_scene's outputs land alongside the source.
        scene_folder = (scene_id or Path(video.filename).stem).strip() or job_id
        job_dir = POD_UPLOADS_DIR / job_id / scene_folder
        job_dir.mkdir(parents=True, exist_ok=True)
        video_path = job_dir / video.filename

        # Stream the upload to disk in chunks so a 4GB scene doesn't blow up
        # the pod's RAM.
        bytes_written = 0
        with video_path.open("wb") as f:
            while True:
                chunk = await video.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                bytes_written += len(chunk)
        log.info(f"Job {job_id} uploaded {bytes_written} bytes -> {video_path}")

        track.create(
            job_id,
            scene_id=scene_folder,
            video_path=video_path,
            work_dir=job_dir,
        )
        thread = threading.Thread(
            target=_run_pipeline_in_thread,
            args=(track, job_id, video_path),
            daemon=True,
            name=f"pod-job-{job_id}",
        )
        thread.start()
        return {
            "job_id": job_id,
            "status": "queued",
            "bytes_received": bytes_written,
            "video_path": str(video_path),
        }

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str) -> Dict[str, Any]:
        j = track.serialize(job_id)
        if j is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        return j

    @app.get("/jobs")
    async def list_jobs() -> Dict[str, Any]:
        return {"job_ids": track.list_ids()}

    @app.get("/jobs/{job_id}/zip")
    async def get_job_zip(job_id: str) -> StreamingResponse:
        j = track.serialize(job_id)
        if j is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        work_dir = Path(j["work_dir"])
        return StreamingResponse(
            _stream_dir_as_zip(work_dir),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{job_id}.zip"'},
        )

    return app


# ---------- CLI entry point ----------


def cli_main(host: str = "0.0.0.0", port: int = 8000) -> int:
    """Run the pod worker via uvicorn. Called by ``amg pod-worker``."""
    try:
        import uvicorn
    except ImportError:
        print("Missing dependency: uvicorn. Install with: pip install uvicorn")
        return 1
    try:
        app = create_app()
    except RuntimeError as exc:
        print(f"Pod worker refuses to start: {exc}")
        return 1
    POD_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"AMG pod-worker listening on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


__all__ = [
    "LOG_TAIL_LINES",
    "POD_UPLOADS_DIR",
    "cli_main",
    "create_app",
]
