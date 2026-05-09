"""
Pod-side worker for the AMG cloud-hosted edition.

This runs INSIDE the Runpod GPU container (built from `Dockerfile.pod`).
Its only client is the controller VM's dispatcher; every
endpoint except ``/healthz`` is gated by a per-pod bearer token that the
controller passes in via ``AMG_POD_AUTH_TOKEN`` when the pod is provisioned.

The worker accepts either an uploaded video OR a cloud-storage reference
(rclone remote + path), runs ``amg.pipeline.process_scene`` against the
result in a background thread, and lets the controller poll for status
and pull back the resulting work-dir as a zip.

Endpoints:

* ``POST /jobs`` (multipart) — start a job for an uploaded video. Returns
  ``{job_id, status: "queued"}``. The video is saved into
  ``AMG_DATA_DIR/pod_uploads/<job_id>/`` and the pipeline runs against it.
* ``POST /jobs/cloud`` (JSON) — start a job that pulls the source from a
  cloud-storage remote via rclone. Alias: ``POST /jobs-cloud`` (same body —
  some proxies mishandle nested ``/jobs/...`` paths). The controller decrypts
  the relevant credential and passes it in the request body so it lives only
  in pod RAM (and a 0600 temp file) for the lifetime of the download. Status
  flows: ``queued -> downloading -> running -> done|error``.
* ``GET /jobs`` — list recent job ids (authenticated); used by the controller
  readiness probe so we don't POST jobs until uvicorn has mounted all routes.
* ``GET /readyz`` — authenticated readiness probe. Verifies Ollama is reachable
  and the configured vision model is present before the controller submits work.
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
from pydantic import BaseModel, Field
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

_PIPELINE_SEMAPHORE_LOCK = threading.Lock()
_PIPELINE_SEMAPHORE: Optional[threading.BoundedSemaphore] = None
_PIPELINE_SEMAPHORE_LIMIT: Optional[int] = None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _epoch_sec() -> float:
    return round(time.time(), 3)


def _pod_pipeline_limit() -> int:
    raw = os.environ.get("AMG_POD_MAX_ACTIVE_PIPELINES", "1")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _get_pipeline_semaphore() -> threading.BoundedSemaphore:
    global _PIPELINE_SEMAPHORE, _PIPELINE_SEMAPHORE_LIMIT
    limit = _pod_pipeline_limit()
    with _PIPELINE_SEMAPHORE_LOCK:
        if _PIPELINE_SEMAPHORE is None or _PIPELINE_SEMAPHORE_LIMIT != limit:
            _PIPELINE_SEMAPHORE = threading.BoundedSemaphore(limit)
            _PIPELINE_SEMAPHORE_LIMIT = limit
        return _PIPELINE_SEMAPHORE


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

    def create(
        self,
        job_id: str,
        *,
        scene_id: str,
        video_path: Path,
        work_dir: Path,
        source_kind: str = "upload",
        cloud_source: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            job = {
                "job_id": job_id,
                "status": "queued",
                "source_kind": source_kind,  # "upload" | "cloud"
                "cloud_source": cloud_source,  # {"remote": ..., "path": ...} or None
                "scene_id": scene_id,
                "video_path": str(video_path),
                "work_dir": str(work_dir),
                "created_at": _utcnow_iso(),
                "created_at_ts": _epoch_sec(),
                "started_at": None,
                "started_at_ts": None,
                "download_started_at_ts": None,
                "download_finished_at_ts": None,
                "pipeline_wait_started_at_ts": None,
                "pipeline_started_at_ts": None,
                "pipeline_finished_at_ts": None,
                "finished_at": None,
                "finished_at_ts": None,
                "log_tail": deque(maxlen=LOG_TAIL_LINES),
                "result": None,
                "error": None,
                "download_pct": 0,
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
        status="waiting_pipeline",
        pipeline_wait_started_at_ts=_epoch_sec(),
    )
    tracker.append_log(
        job_id,
        f"[pod-worker] waiting for pipeline slot "
        f"(max_active={_pod_pipeline_limit()})",
    )
    sem = _get_pipeline_semaphore()
    sem.acquire()
    try:
        tracker.update(
            job_id,
            status="running",
            started_at=_utcnow_iso(),
            started_at_ts=_epoch_sec(),
            pipeline_started_at_ts=_epoch_sec(),
        )
        tracker.append_log(job_id, f"[pod-worker] starting process_scene for {video_path}")
        from amg.pipeline import process_scene  # imported here to keep startup cheap

        def _on_progress(pct: int) -> None:
            tracker.update(job_id, progress_pct=int(pct))

        result = process_scene(video_path, on_progress=_on_progress)
        # Re-point the tracker's work_dir at the pipeline's actual output
        # folder before the controller pulls /jobs/{id}/zip. Initially it
        # points at the download dir (which holds the multi-GB source
        # video), and zipping that into a BytesIO blocks the streaming
        # response by minutes — long enough to time out the controller's
        # artifact-pull. The pipeline's result["work_dir"] is the much
        # smaller out/... folder containing only the covers, contact
        # sheet, and decision log, which is exactly what the operator
        # needs back.
        update_kwargs: Dict[str, Any] = dict(
            status="done" if result.get("success") else "error",
            finished_at=_utcnow_iso(),
            finished_at_ts=_epoch_sec(),
            pipeline_finished_at_ts=_epoch_sec(),
            result=result,
            progress_pct=100,
        )
        pipeline_work_dir = result.get("work_dir")
        if pipeline_work_dir:
            wd = Path(pipeline_work_dir)
            if wd.is_dir():
                update_kwargs["work_dir"] = str(wd)
        tracker.update(job_id, **update_kwargs)
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
            finished_at_ts=_epoch_sec(),
            pipeline_finished_at_ts=_epoch_sec(),
            error=str(exc),
            progress_pct=0,
        )
        tracker.append_log(job_id, f"[pod-worker] pipeline raised: {exc}")
        log.error(f"Job {job_id} failed: {exc}")
    finally:
        try:
            sem.release()
        except ValueError:
            pass


def _write_temp_rclone_config(config_text: str, parent: Path) -> Path:
    """Write ``config_text`` to a freshly-created 0600 file under ``parent``.

    Returns the path. Caller is responsible for unlinking after the
    download completes (or in a finally block on failure)."""
    parent.mkdir(parents=True, exist_ok=True)
    cfg_path = parent / f".rclone-{uuid.uuid4().hex[:8]}.conf"
    fd = os.open(cfg_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fp:
        fp.write(config_text)
    return cfg_path


def _locate_downloaded_video(download_dir: Path, expected_basename: str) -> Optional[Path]:
    """Find the video that rclone copied into ``download_dir``.

    rclone copy preserves the source filename, so the obvious match is
    ``download_dir / expected_basename``. Fallback: pick the largest
    file in the directory, since rclone might have written a temp
    ``.partial`` file alongside on a retry. Returns ``None`` if the
    directory is empty (= rclone produced no output despite reporting
    success, which means an upstream config bug)."""
    obvious = download_dir / expected_basename
    if obvious.is_file():
        return obvious
    candidates = sorted(
        (p for p in download_dir.iterdir() if p.is_file() and not p.name.startswith(".")),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _locate_downloaded_video_with_relative(
    download_dir: Path,
    *,
    expected_basename: str,
    relative_path: Optional[str] = None,
) -> Optional[Path]:
    if relative_path:
        rel = Path(relative_path.strip().strip("/"))
        candidate = download_dir / rel
        if candidate.is_file():
            return candidate
    return _locate_downloaded_video(download_dir, expected_basename)


def _run_cloud_job_in_thread(
    tracker: _JobTracker,
    job_id: str,
    *,
    remote: str,
    remote_path: str,
    rclone_config: str,
    download_dir: Path,
    download_root: Optional[str] = None,
    relative_path: Optional[str] = None,
) -> None:
    """rclone-copy the source then hand off to the pipeline runner.

    The rclone config file is written to a sibling ``_creds`` directory
    (NOT ``download_dir``, which gets zipped and shipped back to the
    operator), set 0600, and unlinked the moment the copy finishes —
    success or failure. The plaintext credential never outlives the
    download phase."""
    from amg.cloud.rclone import Rclone, RcloneError

    copy_source = (download_root or remote_path).strip()
    tracker.update(
        job_id,
        status="downloading",
        started_at=_utcnow_iso(),
        started_at_ts=_epoch_sec(),
        download_started_at_ts=_epoch_sec(),
    )
    tracker.append_log(
        job_id,
        f"[pod-worker] downloading {remote}:{copy_source} -> {download_dir}",
    )

    creds_dir = download_dir.parent / "_creds"
    cfg_path: Optional[Path] = None
    try:
        cfg_path = _write_temp_rclone_config(rclone_config, creds_dir)

        def _on_progress(stats: Dict[str, Any]) -> None:
            tracker.update(job_id, download_pct=int(stats.get("pct", 0)))

        def _on_log(line: str) -> None:
            tracker.append_log(job_id, f"[rclone] {line}")

        Rclone(config_path=cfg_path).copy(
            f"{remote}:{copy_source.lstrip('/')}",
            download_dir,
            on_progress=_on_progress,
            on_log=_on_log,
        )
    except RcloneError as exc:
        tracker.update(
            job_id,
            status="error",
            finished_at=_utcnow_iso(),
            finished_at_ts=_epoch_sec(),
            download_finished_at_ts=_epoch_sec(),
            error=f"rclone copy failed: {exc}",
        )
        tracker.append_log(job_id, f"[pod-worker] rclone copy failed: {exc}")
        log.error(f"Job {job_id} rclone copy failed: {exc}")
        return
    except Exception as exc:  # noqa: BLE001 - any download failure must surface as error
        tracker.update(
            job_id,
            status="error",
            finished_at=_utcnow_iso(),
            finished_at_ts=_epoch_sec(),
            download_finished_at_ts=_epoch_sec(),
            error=f"download setup failed: {exc}",
        )
        tracker.append_log(job_id, f"[pod-worker] download setup failed: {exc}")
        log.error(f"Job {job_id} download setup failed: {exc}")
        return
    finally:
        if cfg_path is not None:
            try:
                cfg_path.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                creds_dir.rmdir()
            except OSError:
                pass

    tracker.update(job_id, download_pct=100, download_finished_at_ts=_epoch_sec())
    expected = Path(remote_path).name
    video_path = _locate_downloaded_video_with_relative(
        download_dir,
        expected_basename=expected,
        relative_path=relative_path,
    )
    if video_path is None:
        tracker.update(
            job_id,
            status="error",
            finished_at=_utcnow_iso(),
            finished_at_ts=_epoch_sec(),
            error="rclone reported success but no file landed in the download dir",
        )
        tracker.append_log(job_id, "[pod-worker] no downloaded file found after rclone copy")
        return

    tracker.update(job_id, video_path=str(video_path))
    tracker.append_log(job_id, f"[pod-worker] download complete -> {video_path}")
    _run_pipeline_in_thread(tracker, job_id, video_path)


# ---------- zip streaming ----------


def _stream_artifact_bundle(work_dir: Path, decision_log_path: Optional[Path]):
    """Yield a zip archive containing the work_dir AND the decision log.

    Layout inside the zip:
      ``work_dir/<files...>``           — covers, contact sheet, insight.json, etc.
      ``decision_log.json``             — pod-side decision log (if it exists)

    The controller extracts ``work_dir/`` to its canonical
    ``DATA_DIR/work_dirs/<scene_id>/`` and copies ``decision_log.json``
    to ``DATA_DIR/decision_logs/<scene_id>.json``. Without the decision
    log on the controller the scene doesn't show up in the library, the
    review form silently fails to persist, and per-phase timings stay
    blank (``_record_run_timing`` reads them from the decision log).
    """
    if not work_dir.exists() or not work_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"work dir not found: {work_dir}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(work_dir.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=str(Path("work_dir") / p.relative_to(work_dir)))
        if decision_log_path is not None and decision_log_path.is_file():
            zf.write(decision_log_path, arcname="decision_log.json")
    buf.seek(0)
    yield from iter(lambda: buf.read(64 * 1024), b"")


# ---------- request models ----------


class CloudJobRequest(BaseModel):
    """JSON body for ``POST /jobs/cloud``.

    ``rclone_config`` carries an entire rclone.conf-style section (the
    ``[gdrive_amy]`` block including type and tokens). The pod writes
    it to a 0600 temp file for the duration of the rclone copy and
    deletes it the moment the copy finishes — success or failure."""

    remote: str = Field(..., min_length=1, description="rclone remote name (no trailing colon)")
    path: str = Field(..., min_length=1, description="path within the remote, e.g. 'incoming/scene4.mp4'")
    rclone_config: str = Field(..., min_length=1, description="full rclone config section text")
    scene_id: Optional[str] = Field(None, description="optional friendly id; defaults to filename stem")
    download_root: Optional[str] = Field(
        None,
        description="optional folder path to copy instead of `path` (for folder-select context)",
    )
    relative_path: Optional[str] = Field(
        None,
        description="optional relative video path within `download_root`",
    )


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

    @app.get("/readyz")
    async def readyz() -> Dict[str, Any]:
        from amg.scoring.ai_client import AIClient

        client = AIClient()
        ollama_ok = client.is_alive()
        model_ok = client.is_model_loaded() if ollama_ok else False
        payload = {
            "ok": bool(ollama_ok and model_ok),
            "ollama_ok": ollama_ok,
            "model_ok": model_ok,
            "vision_model": client.vision_model,
            "ollama_num_parallel": os.environ.get("OLLAMA_NUM_PARALLEL"),
            "ai_parallel_workers": os.environ.get("AMG_AI_PARALLEL_WORKERS"),
            "video_backend": os.environ.get("AMG_VIDEO_BACKEND"),
            "video_hwaccel": os.environ.get("AMG_VIDEO_HWACCEL"),
            "gpu_cv_enabled": os.environ.get("AMG_GPU_CV_ENABLED"),
            "gpu_cv_backend": os.environ.get("AMG_GPU_CV_BACKEND"),
            "gpu_dedup_enabled": os.environ.get("AMG_GPU_DEDUP_ENABLED"),
            "processing_profile": os.environ.get("AMG_PROCESSING_PROFILE"),
            "pod_max_active_pipelines": _pod_pipeline_limit(),
        }
        if not payload["ok"]:
            raise HTTPException(status_code=503, detail=payload)
        return payload

    @app.post("/jobs/cloud")
    @app.post("/jobs-cloud")
    async def create_cloud_job(req: CloudJobRequest) -> Dict[str, Any]:
        if ":" in req.remote:
            raise HTTPException(
                status_code=400,
                detail="'remote' must be the bare remote name (no trailing colon)",
            )
        if "[" not in req.rclone_config or "type" not in req.rclone_config:
            # Cheap structural check before we waste a thread on a doomed
            # rclone invocation. The wrapper validates more rigorously.
            raise HTTPException(
                status_code=400,
                detail="rclone_config does not look like a valid rclone section",
            )
        job_id = uuid.uuid4().hex[:12]
        scene_folder = (req.scene_id or Path(req.path).stem).strip() or job_id
        # download_dir is where rclone deposits the source video, and where
        # the pipeline writes its out/... folder. The tracker's work_dir is
        # initially set to download_dir, then narrowed in
        # _run_pipeline_in_thread to the pipeline's actual output dir
        # (result["work_dir"]) once process_scene returns. That way the
        # /jobs/{id}/zip endpoint ships back only the covers + contact
        # sheet + decision log — not the multi-GB source video, which
        # would block the streaming response while zipfile builds the
        # whole archive into BytesIO before yielding the first chunk.
        download_dir = POD_UPLOADS_DIR / job_id / scene_folder
        download_dir.mkdir(parents=True, exist_ok=True)

        track.create(
            job_id,
            scene_id=scene_folder,
            video_path=download_dir / Path(req.path).name,  # provisional; finalized after copy
            work_dir=download_dir,
            source_kind="cloud",
            cloud_source={"remote": req.remote, "path": req.path},
        )
        thread = threading.Thread(
            target=_run_cloud_job_in_thread,
            kwargs={
                "tracker": track,
                "job_id": job_id,
                "remote": req.remote,
                "remote_path": req.path,
                "rclone_config": req.rclone_config,
                "download_dir": download_dir,
                "download_root": req.download_root,
                "relative_path": req.relative_path,
            },
            daemon=True,
            name=f"pod-cloud-job-{job_id}",
        )
        thread.start()
        log.info(f"Job {job_id} cloud-source queued: {req.remote}:{req.path}")
        return {
            "job_id": job_id,
            "status": "queued",
            "source_kind": "cloud",
            "remote": req.remote,
            "path": req.path,
        }

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

    @app.get("/jobs")
    async def list_jobs() -> Dict[str, Any]:
        return {"job_ids": track.list_ids()}

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str) -> Dict[str, Any]:
        j = track.serialize(job_id)
        if j is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        return j

    @app.get("/jobs/{job_id}/zip")
    async def get_job_zip(job_id: str) -> StreamingResponse:
        j = track.serialize(job_id)
        if j is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        work_dir = Path(j["work_dir"])
        decision_log_path: Optional[Path] = None
        result = j.get("result") or {}
        dlp = result.get("decision_log_path") if isinstance(result, dict) else None
        if dlp:
            try:
                p = Path(dlp)
                if p.is_file():
                    decision_log_path = p
            except (TypeError, ValueError):
                pass
        return StreamingResponse(
            _stream_artifact_bundle(work_dir, decision_log_path),
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
