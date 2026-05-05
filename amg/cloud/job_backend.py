"""
Pluggable job-runner backend for the AMG dispatcher.

The v11.x UI runs ``amg.pipeline.process_scene`` directly in a worker thread
of the FastAPI process. That's right for Mario's local Mac workflow (one
operator, one machine), but it's the wrong shape for the cloud-hosted
edition where the controller VM (Hetzner) doesn't have a GPU and just
needs to push the work onto a Runpod pod that does.

This module abstracts "run a scene through the pipeline" behind a
``JobBackend`` interface and provides two implementations:

* ``LocalBackend`` — calls ``process_scene`` in-process. Identical to the
  v11.x behavior; this is the default so the existing single-Mac workflow
  keeps working unchanged.
* ``RunpodBackend`` — provisions a Runpod GPU pod (via ``RunpodClient``),
  uploads the video to the pod-worker, polls until done, downloads the
  resulting zip, extracts it locally, and tears the pod down.

Selection happens via ``AMG_JOB_BACKEND`` (``local`` | ``runpod``). The
backend factory (``get_backend``) is the only thing the dispatcher needs
to know about; it returns the right instance and handles env validation.

Per-job hooks let backends stream log lines and progress updates back to
the UI (``on_log``, ``on_progress``). The hooks have no return value — they
exist so the backend can drive the existing in-memory job tracker without
being coupled to FastAPI / templates / etc.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import time
import zipfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import requests

from amg.config import DATA_DIR
from amg.utils.logging import get_logger

log = get_logger("amg.cloud.job_backend")


LogHook = Callable[[str], None]
ProgressHook = Callable[[int], None]  # 0..100


def _noop_log(_line: str) -> None:
    return None


def _noop_progress(_pct: int) -> None:
    return None


# ---------- interface ----------


class JobBackend(ABC):
    """Run one scene through the AMG pipeline. Backend may be local or remote."""

    name: str = "abstract"

    @abstractmethod
    def run_job(
        self,
        video_path: Path,
        *,
        on_log: LogHook = _noop_log,
        on_progress: ProgressHook = _noop_progress,
    ) -> Dict[str, Any]:
        """Execute the pipeline against ``video_path`` and return the same
        ``process_scene``-shaped result dict the v11.x UI already consumes
        (``success``, ``scene_id``, ``covers_saved``, ``decision_log_path``,
        ``error_codes``, ...).

        Hooks are best-effort: implementations that can't surface live logs
        / progress (e.g. LocalBackend, where the pipeline writes through the
        existing logger) may simply not call them — callers must not assume
        either is invoked."""
        raise NotImplementedError

    def shutdown(self) -> None:
        """Optional cleanup hook (close http sessions, terminate warm pods, …).
        Default is a no-op; override in subclasses that need it."""


# ---------- local ----------


class LocalBackend(JobBackend):
    """Run the pipeline in-process. Same behavior as v11.x."""

    name = "local"

    def run_job(
        self,
        video_path: Path,
        *,
        on_log: LogHook = _noop_log,
        on_progress: ProgressHook = _noop_progress,
    ) -> Dict[str, Any]:
        from amg.pipeline import process_scene  # deferred import keeps tests cheap

        on_log(f"[local] starting process_scene for {video_path}")
        result = process_scene(video_path)
        on_progress(100)
        on_log(
            f"[local] finished: success={result.get('success')} "
            f"covers_saved={result.get('covers_saved')}"
        )
        return result


# ---------- runpod ----------


class RunpodBackend(JobBackend):
    """Run the pipeline on an on-demand Runpod GPU pod and pull the result back.

    Lifecycle per job:
      1. Provision a fresh pod (RunpodClient.provision_pod).
      2. Stream the video to the pod-worker via multipart POST /jobs.
      3. Poll /jobs/{id} every ``poll_interval_sec`` until status is done/error.
      4. Pull /jobs/{id}/zip and extract it under
         ``AMG_DATA_DIR/work_dirs/<scene_id>/`` so the UI can render covers
         + decision log from the local filesystem unchanged.
      5. Terminate the pod (always — even on failure).

    A single warm-pod-reuse layer is intentionally NOT in this class. That's
    a Phase 4 cost optimization and would couple this class to a longer-
    running orchestrator. Keep this simple and correct first.

    Configuration (env, in addition to AMG_RUNPOD_*):

    * ``AMG_JOB_POD_PORT`` — the http-typed port the pod-worker listens on
      (default 8000; must match what RunpodClient.create_pod exposes).
    * ``AMG_JOB_PROVISION_TIMEOUT_SEC`` — how long to wait for a fresh pod
      to reach RUNNING (default 600s; first boot pulls the image and the
      Ollama model).
    * ``AMG_JOB_POLL_INTERVAL_SEC`` — how often to poll /jobs/{id}
      (default 5).
    * ``AMG_JOB_RUN_TIMEOUT_SEC`` — hard ceiling for one job (default
      4 hours). Past this we abandon the job and tear down the pod.
    """

    name = "runpod"

    def __init__(
        self,
        *,
        client: Any = None,
        spec: Any = None,
        pod_port: Optional[int] = None,
        provision_timeout_sec: Optional[float] = None,
        poll_interval_sec: Optional[float] = None,
        run_timeout_sec: Optional[float] = None,
        work_dirs_root: Optional[Path] = None,
        http_session: Optional[requests.Session] = None,
        auth_token: Optional[str] = None,
    ) -> None:
        # Lazy imports keep amg.cloud.runpod from being required when
        # AMG_JOB_BACKEND=local (the common case).
        from amg.cloud.runpod import PodSpec, RunpodClient

        self._client = client or RunpodClient()
        self._spec = spec or PodSpec.from_env()
        self._pod_port = int(pod_port if pod_port is not None
                             else os.environ.get("AMG_JOB_POD_PORT", "8000"))
        self._provision_timeout_sec = float(
            provision_timeout_sec if provision_timeout_sec is not None
            else os.environ.get("AMG_JOB_PROVISION_TIMEOUT_SEC", "600")
        )
        self._poll_interval_sec = float(
            poll_interval_sec if poll_interval_sec is not None
            else os.environ.get("AMG_JOB_POLL_INTERVAL_SEC", "5")
        )
        self._run_timeout_sec = float(
            run_timeout_sec if run_timeout_sec is not None
            else os.environ.get("AMG_JOB_RUN_TIMEOUT_SEC", str(4 * 60 * 60))
        )
        self._work_dirs_root = Path(work_dirs_root) if work_dirs_root else DATA_DIR / "work_dirs"
        self._session = http_session or requests.Session()
        self._auth_token = (auth_token or os.environ.get("AMG_POD_AUTH_TOKEN", "")).strip()
        if not self._auth_token:
            raise RuntimeError(
                "RunpodBackend requires AMG_POD_AUTH_TOKEN (the bearer token the "
                "pod-worker validates). The same value gets passed into the pod "
                "via env when the pod is provisioned, so both sides agree on it."
            )

    # ----- public api -----

    def run_job(
        self,
        video_path: Path,
        *,
        on_log: LogHook = _noop_log,
        on_progress: ProgressHook = _noop_progress,
    ) -> Dict[str, Any]:
        on_log(f"[runpod] provisioning GPU pod (gpu={self._spec.gpu_type})")
        spec = self._spec
        # Pass the same shared secret into the pod's env so the worker on the
        # other end accepts our requests. Without this the pod would refuse
        # to start (commit 18eb475).
        spec.env = {**spec.env, "AMG_POD_AUTH_TOKEN": self._auth_token}
        pod = self._client.provision_pod(
            spec,
            ready_timeout=self._provision_timeout_sec,
            poll_interval=self._poll_interval_sec,
        )
        pod_id = pod["id"]
        try:
            on_log(f"[runpod] pod {pod_id} RUNNING; uploading video")
            on_progress(15)
            job_id = self._submit_job(pod_id, video_path)
            on_log(f"[runpod] pod accepted job {job_id}; waiting for pipeline")
            result = self._wait_for_job(pod_id, job_id, on_log, on_progress)
            on_log(f"[runpod] pipeline done; pulling artifacts")
            on_progress(90)
            self._download_and_extract(pod_id, job_id, result.get("scene_id"))
            on_progress(100)
            return result
        finally:
            on_log(f"[runpod] terminating pod {pod_id}")
            try:
                self._client.terminate_pod(pod_id)
            except Exception as exc:  # noqa: BLE001 - cleanup must not mask the real result
                on_log(f"[runpod] terminate failed (pod will idle until Runpod auto-stop): {exc}")
                log.error(f"terminate_pod({pod_id}) failed: {exc}")

    def shutdown(self) -> None:
        try:
            self._session.close()
        except Exception:  # noqa: BLE001 - best-effort
            pass

    # ----- helpers -----

    def _pod_base_url(self, pod_id: str) -> str:
        from amg.cloud.runpod import RunpodClient
        return RunpodClient.proxy_url(pod_id, self._pod_port)

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._auth_token}"}

    def _submit_job(self, pod_id: str, video_path: Path) -> str:
        url = f"{self._pod_base_url(pod_id)}/jobs"
        scene_id = video_path.parent.name
        with video_path.open("rb") as fh:
            files = {"video": (video_path.name, fh, "application/octet-stream")}
            data = {"scene_id": scene_id}
            resp = self._session.post(
                url,
                headers=self._headers(),
                files=files,
                data=data,
                timeout=self._run_timeout_sec,  # upload of a 4GB scene can take a while
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"pod {pod_id} rejected /jobs upload: "
                f"HTTP {resp.status_code} {resp.text[:300]}"
            )
        body = resp.json()
        return body["job_id"]

    def _wait_for_job(
        self,
        pod_id: str,
        job_id: str,
        on_log: LogHook,
        on_progress: ProgressHook,
    ) -> Dict[str, Any]:
        url = f"{self._pod_base_url(pod_id)}/jobs/{job_id}"
        deadline = time.monotonic() + self._run_timeout_sec
        last_seen_log_idx = 0
        while time.monotonic() < deadline:
            resp = self._session.get(url, headers=self._headers(), timeout=30.0)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"pod {pod_id} job {job_id} poll failed: HTTP {resp.status_code}"
                )
            body = resp.json()
            # Stream new log lines through to the UI's per-job tail.
            tail = body.get("log_tail") or []
            for line in tail[last_seen_log_idx:]:
                on_log(f"[pod] {line}")
            last_seen_log_idx = len(tail)
            pct = body.get("progress_pct")
            if isinstance(pct, (int, float)):
                # Compress 0..100 from the pod into 30..85 of the controller's
                # progress so provisioning + download have headroom.
                on_progress(30 + int(0.55 * float(pct)))
            status = body.get("status")
            if status == "done":
                return body.get("result") or {}
            if status == "error":
                raise RuntimeError(
                    f"pod-side pipeline failed: {body.get('error') or 'unknown error'}"
                )
            time.sleep(self._poll_interval_sec)
        raise RuntimeError(
            f"pod {pod_id} job {job_id} did not finish within "
            f"{self._run_timeout_sec:.0f}s; abandoning"
        )

    def _download_and_extract(
        self,
        pod_id: str,
        job_id: str,
        scene_id: Optional[str],
    ) -> Path:
        if not scene_id:
            scene_id = job_id  # fallback so we still land artifacts somewhere
        url = f"{self._pod_base_url(pod_id)}/jobs/{job_id}/zip"
        resp = self._session.get(url, headers=self._headers(), timeout=600.0, stream=True)
        if resp.status_code != 200:
            raise RuntimeError(
                f"pod {pod_id} /jobs/{job_id}/zip returned HTTP {resp.status_code}"
            )
        target = self._work_dirs_root / scene_id
        target.parent.mkdir(parents=True, exist_ok=True)
        # Read into memory once so zipfile can seek; for typical scene-cover
        # output this is small (covers + JSON + contact sheet, well under
        # 50 MB). If we ever need to handle bigger payloads we can spool to a
        # temp file instead.
        buf = io.BytesIO()
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                buf.write(chunk)
        buf.seek(0)
        # Atomic-ish replace: extract into a sibling tmp dir, then swap.
        tmp = target.with_suffix(".incoming")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        with zipfile.ZipFile(buf, "r") as zf:
            zf.extractall(tmp)
        if target.exists():
            shutil.rmtree(target)
        tmp.rename(target)
        log.info(f"Extracted pod result for scene {scene_id} -> {target}")
        return target


# ---------- factory ----------


def get_backend() -> JobBackend:
    """Return the configured backend instance.

    Reads ``AMG_JOB_BACKEND``:
      * ``local`` (default): in-process pipeline.
      * ``runpod``: Runpod-managed GPU pod.

    Misconfiguration (unknown backend, missing required env) is surfaced as
    a clear ``RuntimeError`` so the dispatcher can refuse to start jobs
    instead of silently falling back to the wrong backend."""
    name = os.environ.get("AMG_JOB_BACKEND", "local").strip().lower()
    if name == "local":
        return LocalBackend()
    if name == "runpod":
        return RunpodBackend()
    raise RuntimeError(
        f"AMG_JOB_BACKEND={name!r} is not a known backend. "
        f"Supported: local, runpod."
    )


__all__ = [
    "JobBackend",
    "LocalBackend",
    "LogHook",
    "ProgressHook",
    "RunpodBackend",
    "get_backend",
]
