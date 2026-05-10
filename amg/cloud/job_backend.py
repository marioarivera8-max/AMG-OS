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
  keeps working unchanged. Also implements ``run_cloud_job`` so the
  end-to-end cloud-source path is testable on the Mac (operator just needs
  rclone installed locally + a populated credential store).
* ``RunpodBackend`` — provisions a Runpod GPU pod (via ``RunpodClient``),
  uploads the video (or hands the pod an rclone reference), polls until
  done, downloads the resulting zip, extracts it locally, and tears the
  pod down.

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
import tempfile
import threading
import time
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
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


@dataclass(frozen=True)
class CloudSource:
    """Reference to a video sitting in cloud storage.

    ``remote`` matches the rclone remote name (the ``[gdrive_amy]`` section
    header in the stored config), ``path`` is the path within that remote
    (e.g. ``incoming/scene4.mp4``)."""

    remote: str
    path: str
    scene_id: Optional[str] = None
    # Optional folder context for cloud folder-select flows:
    # - download_root: copy this remote folder instead of just `path`
    # - relative_path: locate the intended video inside the copied folder
    download_root: Optional[str] = None
    relative_path: Optional[str] = None


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

    def run_cloud_job(
        self,
        cloud_source: CloudSource,
        *,
        on_log: LogHook = _noop_log,
        on_progress: ProgressHook = _noop_progress,
    ) -> Dict[str, Any]:
        """Execute the pipeline against a cloud-storage reference.

        The backend looks up the rclone credential for ``cloud_source.remote``
        in the local CredentialStore (controller-side), then either copies
        the file locally (LocalBackend) or hands the rclone reference to
        the pod and lets it download directly (RunpodBackend).

        Default is to refuse — backends that haven't opted in raise
        ``NotImplementedError`` instead of silently doing the wrong thing
        (e.g. uploading the entire 4 GB scene through the controller again,
        defeating the whole point of the cloud-source path)."""
        raise NotImplementedError(
            f"backend {self.name!r} does not support cloud-source jobs"
        )

    def shutdown(self) -> None:
        """Optional cleanup hook (close http sessions, terminate warm pods, …).
        Default is a no-op; override in subclasses that need it."""


# ---------- local ----------


class LocalBackend(JobBackend):
    """Run the pipeline in-process. Same behavior as v11.x.

    The cloud-source path uses the rclone CLI on this host (operator's
    Mac) to pull the file into a local download dir, then runs the
    pipeline against it. Useful for end-to-end testing of the
    cloud-source flow without spinning up a Runpod pod."""

    name = "local"

    @staticmethod
    def _locate_downloaded_video(
        download_dir: Path,
        *,
        expected_name: str,
        relative_path: Optional[str] = None,
    ) -> Optional[Path]:
        if relative_path:
            rel = Path(relative_path.strip().strip("/"))
            candidate = download_dir / rel
            if candidate.is_file():
                return candidate
        expected = download_dir / expected_name
        if expected.is_file():
            return expected
        return next(
            (p for p in download_dir.rglob("*") if p.is_file() and not p.name.startswith(".")),
            None,
        )

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

    def run_cloud_job(
        self,
        cloud_source: CloudSource,
        *,
        on_log: LogHook = _noop_log,
        on_progress: ProgressHook = _noop_progress,
    ) -> Dict[str, Any]:
        # Imports deferred so AMG_JOB_BACKEND=local without rclone installed
        # doesn't break import of this module.
        from amg.cloud.credentials import CredentialStore
        from amg.cloud.rclone import Rclone, RcloneError, RcloneNotFoundError
        from amg.pipeline import process_scene

        scene_folder = (cloud_source.scene_id or Path(cloud_source.path).stem).strip()
        if not scene_folder:
            scene_folder = "cloud_job"
        download_dir = DATA_DIR / "cloud_downloads" / scene_folder
        download_dir.mkdir(parents=True, exist_ok=True)

        copy_source = cloud_source.download_root or cloud_source.path
        store = CredentialStore()
        on_log(
            f"[local-cloud] rclone copy {cloud_source.remote}:{copy_source} "
            f"-> {download_dir}"
        )
        with store.materialize_config(names=[cloud_source.remote]) as cfg_path:
            try:
                Rclone(config_path=cfg_path).copy(
                    f"{cloud_source.remote}:{copy_source.lstrip('/')}",
                    download_dir,
                    on_progress=lambda stats: on_progress(int(0.5 * stats.get("pct", 0))),
                    on_log=lambda line: on_log(f"[rclone] {line}"),
                )
            except RcloneNotFoundError as exc:
                raise RuntimeError(
                    f"local cloud-source jobs need rclone installed: {exc}"
                ) from exc
            except RcloneError as exc:
                raise RuntimeError(f"rclone copy failed: {exc}") from exc

        video_path = self._locate_downloaded_video(
            download_dir,
            expected_name=Path(cloud_source.path).name,
            relative_path=cloud_source.relative_path,
        )
        if video_path is None:
            raise RuntimeError(
                f"rclone copy reported success but no file landed in {download_dir}"
            )

        on_log(f"[local-cloud] download complete -> {video_path}")
        on_progress(50)
        result = process_scene(video_path)
        on_progress(100)
        on_log(
            f"[local-cloud] finished: success={result.get('success')} "
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

    Warm-pod reuse is optional and controlled by ``AMG_RUNPOD_IDLE_TERMINATE_SEC``.
    With a positive value, the backend keeps one pod warm between queued jobs
    and only terminates after idle timeout. With 0, behavior stays strict
    per-job provision/terminate.

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
    * ``AMG_RUNPOD_IDLE_TERMINATE_SEC`` — keep a warm pod alive for this
      many idle seconds between jobs so queued work doesn't pay cold-boot
      repeatedly (default 0 = terminate immediately after each job).
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
        idle_terminate_sec: Optional[float] = None,
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
        # Time budget for the pod's FastAPI worker to come up AND for the
        # vision model to be loaded into Ollama. Default 900s (15 min) is
        # sized for cold pulls on H100 SXM, which has no network volume in
        # EU-RO-1 and pays the full ~5 GB qwen2.5vl:7b download every cold
        # boot. Warm pods (network volume attached) typically reach ready
        # in <60s and never come close to this ceiling.
        self._pod_ready_timeout_sec = float(
            os.environ.get("AMG_JOB_POD_READY_TIMEOUT_SEC", "900")
        )
        self._work_dirs_root = Path(work_dirs_root) if work_dirs_root else DATA_DIR / "work_dirs"
        self._session = http_session or requests.Session()
        self._auth_token = (auth_token or os.environ.get("AMG_POD_AUTH_TOKEN", "")).strip()
        self._idle_terminate_sec = float(
            idle_terminate_sec if idle_terminate_sec is not None
            else os.environ.get("AMG_RUNPOD_IDLE_TERMINATE_SEC", "900")
        )
        # Controller-side rclone fallback is a foot-gun on small VMs (4 GB
        # Hetzner CX21 OOM-kills on multi-GB downloads). Disabled by default;
        # operator must explicitly opt in.
        self._allow_controller_rclone_fallback = (
            os.environ.get("AMG_RUNPOD_ALLOW_CONTROLLER_RCLONE_FALLBACK", "0").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        self._lifecycle_lock = threading.RLock()
        self._provision_lock = threading.Lock()
        self._active_jobs = 0
        self._warm_pod_id: Optional[str] = None
        self._idle_timer: Optional[threading.Timer] = None
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
        return self._run_with_lifecycle(
            on_log=on_log,
            on_progress=on_progress,
            submit=lambda pod_id: self._submit_job(pod_id, video_path),
        )

    def run_cloud_job(
        self,
        cloud_source: CloudSource,
        *,
        on_log: LogHook = _noop_log,
        on_progress: ProgressHook = _noop_progress,
    ) -> Dict[str, Any]:
        from amg.cloud.credentials import CredentialStore

        store = CredentialStore()
        on_log(
            f"[runpod] cloud-source job: {cloud_source.remote}:{cloud_source.path}"
        )

        # Decrypt the credential up front (before paying for a pod). If the
        # credential store is misconfigured, fail FAST with no GPU charges.
        rclone_config = store.get_remote(cloud_source.remote)
        upload_fallback_video: Optional[Path] = None

        def _submit_with_fallback(pod_id: str) -> str:
            nonlocal upload_fallback_video
            try:
                return self._submit_cloud_job(
                    pod_id,
                    remote=cloud_source.remote,
                    path=cloud_source.path,
                    rclone_config=rclone_config,
                    scene_id=cloud_source.scene_id,
                    download_root=cloud_source.download_root,
                    relative_path=cloud_source.relative_path,
                )
            except RuntimeError as exc:
                msg = str(exc)
                if "no cloud submit route" not in msg:
                    raise
                if not self._allow_controller_rclone_fallback:
                    raise RuntimeError(
                        f"{msg} | controller-side rclone fallback is disabled "
                        "(set AMG_RUNPOD_ALLOW_CONTROLLER_RCLONE_FALLBACK=1 to "
                        "enable it; only safe on controllers with enough RAM "
                        "and disk to stage the source video locally)."
                    ) from exc
                on_log(
                    "[runpod] pod lacks cloud-submit routes; "
                    "falling back to controller-side rclone download + /jobs upload"
                )
                upload_fallback_video = self._download_cloud_source_for_upload(
                    cloud_source=cloud_source,
                    rclone_config=rclone_config,
                    on_log=on_log,
                    on_progress=on_progress,
                )
                return self._submit_job(pod_id, upload_fallback_video)

        try:
            return self._run_with_lifecycle(
                on_log=on_log,
                on_progress=on_progress,
                submit=_submit_with_fallback,
            )
        finally:
            if upload_fallback_video is not None:
                try:
                    shutil.rmtree(upload_fallback_video.parent.parent, ignore_errors=True)
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass

    def _run_with_lifecycle(
        self,
        *,
        on_log: LogHook,
        on_progress: ProgressHook,
        submit: Callable[[str], str],
    ) -> Dict[str, Any]:
        """Pod lifecycle template shared by both upload and cloud-source jobs.

        ``submit`` is called once the pod is RUNNING and must return the
        pod-side job_id. Splitting this out avoids duplicating the
        provision/wait/download/teardown sequence between the two paths."""
        pod_id = self._acquire_pod(on_log=on_log)
        success = False
        try:
            on_log(f"[runpod] pod {pod_id} RUNNING; waiting for pod-worker /healthz")
            on_progress(12)
            self._wait_for_pod_worker_ready(
                pod_id,
                on_log=on_log,
                timeout_sec=self._pod_ready_timeout_sec,
            )
            on_log(f"[runpod] pod-worker ready; submitting job")
            on_progress(15)
            job_id = submit(pod_id)
            on_log(f"[runpod] pod accepted job {job_id}; waiting for pipeline")
            result = self._wait_for_job(pod_id, job_id, on_log, on_progress)
            on_log(f"[runpod] pipeline done; pulling artifacts")
            on_progress(90)
            controller_paths = self._download_and_extract(
                pod_id, job_id, result.get("scene_id")
            )
            # Rewrite pod-side paths in the result to their controller-side
            # equivalents so the UI (which reads from the local filesystem)
            # finds covers, decision log, work_dir at the right place.
            if controller_paths.get("work_dir"):
                result["work_dir"] = str(controller_paths["work_dir"])
            if controller_paths.get("decision_log_path"):
                result["decision_log_path"] = str(controller_paths["decision_log_path"])
            on_progress(100)
            success = True
            return result
        except Exception:
            # With opt-in controller concurrency, several jobs may share one
            # warm pod. Do not tear the pod out from under sibling jobs; the
            # last failing job will still terminate it when no other active
            # work remains.
            with self._lifecycle_lock:
                other_active = self._active_jobs > 1
            if other_active:
                on_log("[runpod] job failed while sibling jobs are active; deferring pod teardown")
            else:
                self._terminate_pod_now(pod_id, on_log=on_log)
            raise
        finally:
            self._release_pod(pod_id, success=success, on_log=on_log)

    def shutdown(self) -> None:
        self._cancel_idle_timer()
        with self._lifecycle_lock:
            pod_id = self._warm_pod_id
            self._warm_pod_id = None
        if pod_id:
            try:
                self._client.terminate_pod(pod_id)
            except Exception as exc:  # noqa: BLE001 - best-effort
                log.error(f"terminate_pod({pod_id}) during shutdown failed: {exc}")
        try:
            self._session.close()
        except Exception:  # noqa: BLE001 - best-effort
            pass

    def status_snapshot(self, *, probe_worker: bool = False, timeout_sec: float = 1.0) -> Dict[str, Any]:
        """Return a cheap controller-side view of warm-pod state.

        When ``probe_worker`` is true and a warm pod exists, this also asks the
        pod worker for its AMG-native capacity and recent latency snapshots.
        """
        with self._lifecycle_lock:
            pod_id = self._warm_pod_id
            active_jobs = self._active_jobs
            idle_timer_active = self._idle_timer is not None
        try:
            max_active_jobs = int(
                os.environ.get(
                    "AMG_RUNPOD_MAX_ACTIVE_PIPELINES",
                    os.environ.get("AMG_POD_MAX_ACTIVE_PIPELINES", "1"),
                )
            )
        except (TypeError, ValueError):
            max_active_jobs = 1
        snap: Dict[str, Any] = {
            "backend": self.name,
            "warm_pod_id": pod_id,
            "warm_pod_status": "warm" if pod_id else "none",
            "active_jobs": active_jobs,
            "idle_timer_active": idle_timer_active,
            "idle_terminate_sec": self._idle_terminate_sec,
            "max_active_jobs": max(1, max_active_jobs),
        }
        if not probe_worker or not pod_id:
            return snap
        base = self._pod_base_url(pod_id)
        try:
            r = self._session.get(
                f"{base}/used-system-capacity",
                headers=self._headers(),
                timeout=timeout_sec,
            )
            if r.status_code == 200:
                snap["pod_capacity"] = r.json()
        except Exception as exc:  # noqa: BLE001 - UI-only snapshot
            snap["pod_capacity_error"] = str(exc)[:160]
        try:
            r = self._session.get(
                f"{base}/latency",
                params={"media_type": "video"},
                headers=self._headers(),
                timeout=timeout_sec,
            )
            if r.status_code == 200:
                snap["pod_latency"] = r.json()
        except Exception as exc:  # noqa: BLE001 - UI-only snapshot
            snap["pod_latency_error"] = str(exc)[:160]
        return snap

    # ----- helpers -----

    def _pod_base_url(self, pod_id: str) -> str:
        from amg.cloud.runpod import RunpodClient
        return RunpodClient.proxy_url(pod_id, self._pod_port)

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._auth_token}"}

    def _readiness_headers(self, *, authenticated: bool) -> Dict[str, str]:
        headers = {
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        }
        if authenticated:
            headers.update(self._headers())
        return headers

    def _acquire_pod(self, *, on_log: LogHook) -> str:
        with self._lifecycle_lock:
            self._cancel_idle_timer()
            pod_id = self._warm_pod_id
        if pod_id:
            with self._lifecycle_lock:
                self._active_jobs += 1
            on_log(
                "[runpod] reusing warm pod; env/image changes require pod recycle "
                "before they take effect"
            )
            return pod_id

        with self._provision_lock:
            with self._lifecycle_lock:
                pod_id = self._warm_pod_id
                if pod_id:
                    self._active_jobs += 1
                    on_log(
                        "[runpod] reusing warm pod; env/image changes require pod recycle "
                        "before they take effect"
                    )
                    return pod_id

            on_log(f"[runpod] provisioning GPU pod (gpu={self._spec.gpu_type})")
            spec = self._spec
            ollama_parallel = str(os.environ.get("AMG_RUNPOD_OLLAMA_NUM_PARALLEL", "6"))
            worker_parallel = str(os.environ.get("AMG_RUNPOD_AI_PARALLEL_WORKERS", ollama_parallel))
            # Hard-lock decode path to ffmpeg_cuda by default for cloud jobs.
            # This prevents silent fallback to pyav/opencv when controller env
            # omits AMG_RUNPOD_VIDEO_BACKEND. Operators can still override.
            video_backend = str(os.environ.get("AMG_RUNPOD_VIDEO_BACKEND", "ffmpeg_cuda"))
            # Cloud should default to balanced if controller env omitted the
            # profile; local CLI keeps its own quality default in config.py.
            processing_profile = str(os.environ.get("AMG_PROCESSING_PROFILE", "balanced"))
            # Pass the shared secret into pod env so worker accepts controller requests.
            spec_env = dict(spec.env or {})
            spec_env["OLLAMA_NUM_PARALLEL"] = ollama_parallel
            spec_env["AMG_AI_PARALLEL_WORKERS"] = worker_parallel
            spec_env["AMG_VIDEO_BACKEND"] = video_backend
            spec_env["AMG_VIDEO_HWACCEL"] = str(os.environ.get("AMG_VIDEO_HWACCEL", "cuda"))
            spec_env["AMG_GPU_CV_ENABLED"] = str(os.environ.get("AMG_GPU_CV_ENABLED", "1"))
            spec_env["AMG_GPU_CV_BACKEND"] = str(os.environ.get("AMG_GPU_CV_BACKEND", "opencv_cuda"))
            spec_env["AMG_GPU_DEDUP_ENABLED"] = str(
                os.environ.get("AMG_GPU_DEDUP_ENABLED", spec_env["AMG_GPU_CV_ENABLED"])
            )
            spec_env["AMG_PROCESSING_PROFILE"] = processing_profile
            spec_env["AMG_POD_MAX_ACTIVE_PIPELINES"] = str(
                os.environ.get(
                    "AMG_RUNPOD_MAX_ACTIVE_PIPELINES",
                    os.environ.get("AMG_POD_MAX_ACTIVE_PIPELINES", "1"),
                )
            )
            # Streaming scan is the cloud happy path; force-on unless the
            # operator explicitly overrides it in controller env.
            spec_env["AMG_STREAMING_SCAN"] = str(os.environ.get("AMG_STREAMING_SCAN", "1"))
            forwarded_env = [
                "AMG_CALIBRATION_SAMPLE_COUNT",
                "AMG_CALIBRATION_MAX_DURATION_SEC",
                "AMG_TIER_SCAN_MODE",
                "AMG_TIER_SCAN_MAX_EXTRACTED_FRAMES_PER_TIER",
                "AMG_TIER_SCAN_MAX_AI_FRAMES_PER_TIER",
                "AMG_TIER_SCAN_MAX_WALL_SEC_PER_TIER",
                "AMG_SINGLE_PASS_SCAN_INTERVAL_SEC",
                "AMG_SINGLE_PASS_MAX_AI_FRAMES",
                "AMG_SINGLE_PASS_MIN_GAP_SEC",
                "AMG_CLUSTER_HUNTER_TOP_N",
                "AMG_FINISH_HUNTER_TOP_N",
                "AMG_BUILDUP_HUNTER_TOP_N",
                "AMG_POSITION_CLASSIFIER_MAX_CANDIDATES",
                "AMG_ENABLE_FINISH_HUNTER",
                "AMG_ENABLE_BUILDUP_HUNTER",
                "AMG_ENABLE_CLUSTER_EXPANSION",
                "AMG_ENABLE_POSITION_CLASSIFIER",
                "AMG_ENABLE_SCENE_INSIGHT",
                "AMG_ENABLE_PROVIDED_THUMBNAIL_SCORING",
                "AMG_ENABLE_SCENE_ANALYSIS",
                "AMG_ENABLE_ANALYSIS_OCR_POLICY",
                "AMG_ANALYSIS_OCR_MAX_FRAMES",
                "AMG_ENABLE_PREVIEW_GENERATION",
                "AMG_PREVIEW_CLIP_COUNT",
                "AMG_PREVIEW_CLIP_DURATION_SEC",
                "AMG_PREVIEW_GIF_ENABLED",
                "AMG_PREVIEW_FFMPEG_BIN",
                "AMG_SOFT_THUMB_ENABLED",
                "AMG_SOFT_THUMB_SAMPLE_COUNT",
                "AMG_COVER_NEARBY_POLISH_ENABLED",
                "AMG_COVER_BLUR_RESCUE_ENABLED",
                "AMG_COVER_BLUR_RESCUE_MIN_SCORE",
                "AMG_COVER_BLUR_RESCUE_SHARPNESS_FLOOR",
                "AMG_COVER_PRESENCE_GATE_ENABLED",
                "AMG_COVER_PRESENCE_GATE_MODE",
                "AMG_COVER_SUSPICIOUS_RECHECK_ENABLED",
                "AMG_COVER_SUSPICIOUS_RECHECK_MAX",
                "AMG_COVER_MIN_PENETRATION_CONFIDENCE",
                "AMG_PROVIDED_THUMB_MAX_SCAN",
                "AMG_PROVIDED_THUMB_MAX_ACCEPT",
                "AMG_VISION_MODEL_OVERRIDE",
                "AMG_VIDEO_HWACCEL",
                "AMG_GPU_CV_ENABLED",
                "AMG_GPU_CV_BACKEND",
                "AMG_GPU_DEDUP_ENABLED",
                "AMG_STREAMING_SCAN",
                "AMG_STREAMING_SCAN_INTERVAL_SEC",
                "AMG_STREAMING_SCAN_MAX_QUEUED",
                "AMG_STREAMING_SCAN_MAX_AI_CALLS",
                "AMG_STREAMING_FRAME_CACHE_MAX_MB",
                "AMG_STREAMING_LOW_RES_ANALYSIS",
                "AMG_STREAMING_ANALYSIS_MAX_WIDTH",
                "AMG_STREAMING_ANALYSIS_MAX_HEIGHT",
                "AMG_STREAMING_SEGMENT_COUNT",
                "AMG_STREAMING_SEGMENT_MIN_DURATION_SEC",
                "AMG_STREAMING_DEFER_CALIBRATION",
                "AMG_STREAMING_EARLY_STOP_ENABLED",
                "AMG_STREAMING_FUSED_AI_WEIGHT",
                "AMG_STREAMING_FUSED_SHARP_WEIGHT",
                "AMG_STREAMING_ZONE_BONUS_FINISH",
                "AMG_STREAMING_ZONE_BONUS_BUILDUP",
                "AMG_STREAMING_POST_AI_SHARP_PERCENTILE",
                "AMG_POD_MAX_ACTIVE_PIPELINES",
            ]
            for env_name in forwarded_env:
                if env_name in os.environ:
                    spec_env[env_name] = os.environ[env_name]
            spec_env["AMG_POD_AUTH_TOKEN"] = self._auth_token
            spec.env = spec_env
            pod = self._client.provision_pod(
                spec,
                ready_timeout=self._provision_timeout_sec,
                poll_interval=self._poll_interval_sec,
            )
            new_pod_id = pod["id"]
            with self._lifecycle_lock:
                self._warm_pod_id = new_pod_id
                self._active_jobs += 1
            return new_pod_id

    def _release_pod(self, pod_id: str, *, success: bool, on_log: LogHook) -> None:
        with self._lifecycle_lock:
            self._active_jobs = max(0, self._active_jobs - 1)
            has_active = self._active_jobs > 0
        if has_active:
            return
        if not success:
            return
        if self._idle_terminate_sec <= 0:
            self._terminate_pod_now(pod_id, on_log=on_log)
            return
        self._schedule_idle_termination(pod_id, on_log=on_log)

    def _schedule_idle_termination(self, pod_id: str, *, on_log: LogHook) -> None:
        self._cancel_idle_timer()
        on_log(
            f"[runpod] keeping pod warm for queued jobs "
            f"(idle timeout {int(self._idle_terminate_sec)}s)"
        )

        def _on_idle() -> None:
            with self._lifecycle_lock:
                if self._active_jobs > 0:
                    return
                if self._warm_pod_id != pod_id:
                    return
            self._terminate_pod_now(pod_id, on_log=on_log)

        timer = threading.Timer(self._idle_terminate_sec, _on_idle)
        timer.daemon = True
        with self._lifecycle_lock:
            self._idle_timer = timer
        timer.start()

    def _cancel_idle_timer(self) -> None:
        timer: Optional[threading.Timer] = None
        with self._lifecycle_lock:
            timer = self._idle_timer
            self._idle_timer = None
        if timer:
            try:
                timer.cancel()
            except Exception:
                pass

    def _terminate_pod_now(self, pod_id: str, *, on_log: LogHook) -> None:
        self._cancel_idle_timer()
        with self._lifecycle_lock:
            if self._warm_pod_id == pod_id:
                self._warm_pod_id = None
        on_log(f"[runpod] terminating pod {pod_id}")
        try:
            self._client.terminate_pod(pod_id)
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask real result
            on_log(f"[runpod] terminate failed (pod may keep billing): {exc}")
            log.error(f"terminate_pod({pod_id}) failed: {exc}")

    def _wait_for_pod_worker_ready(
        self,
        pod_id: str,
        *,
        on_log: LogHook,
        timeout_sec: float = 900.0,
        poll_interval_sec: float = 5.0,
    ) -> None:
        """Poll until the pod-worker FastAPI app is actually taking requests.

        Runpod marks a pod RUNNING the moment its container starts. The pod
        entrypoint then (a) starts ollama, (b) waits for it to respond, and
        (c) execs ``amg pod-worker`` so uvicorn binds :8000. The vision-
        model pull (``ollama pull qwen2.5vl:7b``, ~5 GB) runs in the
        BACKGROUND so the worker can serve ``/healthz`` immediately and
        the controller doesn't time out waiting for the pull. ``/readyz``
        is the gate that ensures the pull has finished before we submit
        work.

        ``/healthz`` alone is insufficient — some proxies can satisfy probes
        before uvicorn mounts our routes (brief window where POST
        ``/jobs/cloud`` returns 404 empty-body while GET ``/healthz`` looks
        fine). We therefore require all of:

          * GET ``/healthz`` → 200 (unauthenticated — proves TCP + routing),
          * GET ``/jobs`` with our bearer token → 200 (proves pod-worker app +
            auth middleware agree with the controller's secret),
          * GET ``/readyz`` with our bearer token → 200 (proves Ollama and the
            configured vision model are available).

        The default timeout is 15 min so cold H100 pods (no network volume
        in EU-RO-1, fresh model pull on every cold-start) can finish their
        ~5 GB ``ollama pull`` before the controller gives up. Override via
        ``AMG_JOB_POD_READY_TIMEOUT_SEC`` if needed.
        """
        base = self._pod_base_url(pod_id)
        health_url = f"{base}/healthz"
        jobs_ping_url = f"{base}/jobs"
        ready_url = f"{base}/readyz"
        deadline = time.monotonic() + float(timeout_sec)
        last_log_at = 0.0
        attempts = 0
        last_phase = "starting"
        last_readyz_payload: Dict[str, Any] = {}
        while time.monotonic() < deadline:
            attempts += 1
            cache_buster = f"?attempt={attempts}&t={int(time.time() * 1000)}"
            current_phase = "starting"
            try:
                h = self._session.get(
                    f"{health_url}{cache_buster}",
                    headers=self._readiness_headers(authenticated=False),
                    timeout=10.0,
                )
            except requests.RequestException:
                pass
            else:
                if h.status_code != 200:
                    pass
                else:
                    current_phase = "fastapi-up"
                    try:
                        ping = self._session.get(
                            f"{jobs_ping_url}{cache_buster}",
                            headers=self._readiness_headers(authenticated=True),
                            timeout=10.0,
                        )
                    except requests.RequestException:
                        ping = None
                    else:
                        if ping.status_code == 401:
                            raise RuntimeError(
                                f"pod {pod_id} rejected bearer token on GET /jobs (HTTP 401). "
                                "AMG_POD_AUTH_TOKEN on the controller must match the value "
                                "injected into the pod env."
                            )
                        if ping.status_code == 200:
                            current_phase = "auth-ok"
                            try:
                                ready = self._session.get(
                                    f"{ready_url}{cache_buster}",
                                    headers=self._readiness_headers(authenticated=True),
                                    timeout=10.0,
                                )
                            except requests.RequestException:
                                ready = None
                            if ready is None:
                                pass
                            elif ready.status_code == 401:
                                raise RuntimeError(
                                    f"pod {pod_id} rejected bearer token on GET /readyz (HTTP 401). "
                                    "AMG_POD_AUTH_TOKEN on the controller must match the value "
                                    "injected into the pod env."
                                )
                            else:
                                # /readyz body is JSON in both 200 and 503 cases —
                                # 503 carries `{ok, ollama_ok, model_ok, ...}` as
                                # the HTTPException detail, which is exactly what
                                # we want to surface to the operator.
                                try:
                                    payload = ready.json() or {}
                                except Exception:  # noqa: BLE001 - readiness body is diagnostic only
                                    payload = {}
                                if isinstance(payload, dict) and "detail" in payload and isinstance(payload["detail"], dict):
                                    payload = payload["detail"]
                                last_readyz_payload = payload if isinstance(payload, dict) else {}
                                if ready.status_code == 200:
                                    on_log(
                                        f"[runpod] pod-worker ready (/healthz + /jobs + /readyz OK "
                                        f"after {attempts} attempt(s); model={last_readyz_payload.get('vision_model', '?')} "
                                        f"workers={last_readyz_payload.get('ai_parallel_workers', '?')})"
                                    )
                                    return
                                # 503 means worker is up but Ollama / model is not
                                # yet ready (background pull still running).
                                if last_readyz_payload.get("ollama_ok") and not last_readyz_payload.get("model_ok"):
                                    current_phase = "model-pull"
                                elif not last_readyz_payload.get("ollama_ok"):
                                    current_phase = "waiting-on-ollama"
            now = time.monotonic()
            # Throttle progress logs so a multi-minute model pull doesn't spam,
            # and surface phase transitions (starting → fastapi-up → model-pull
            # → ready) so the operator sees movement instead of a stuck "not
            # ready yet" loop.
            if current_phase != last_phase or now - last_log_at >= 30.0:
                if current_phase == "model-pull":
                    on_log(
                        f"[runpod] pod-worker FastAPI is up, Ollama is pulling "
                        f"{last_readyz_payload.get('vision_model', 'qwen2.5vl:7b')} (attempt {attempts}); "
                        "this is normal on the first H100 cold-start"
                    )
                elif current_phase == "fastapi-up":
                    on_log(f"[runpod] pod-worker /healthz OK (attempt {attempts}); auth check pending")
                elif current_phase == "auth-ok":
                    on_log(f"[runpod] pod-worker auth OK (attempt {attempts}); waiting on /readyz")
                elif current_phase == "waiting-on-ollama":
                    on_log(f"[runpod] pod-worker FastAPI is up but Ollama is not yet reachable (attempt {attempts})")
                else:
                    on_log(f"[runpod] pod-worker not ready yet (attempt {attempts}); still waiting...")
                last_log_at = now
                last_phase = current_phase
            time.sleep(poll_interval_sec)
        # Surface the last /readyz payload in the timeout error so the operator
        # knows whether it timed out on FastAPI startup, Ollama startup, or the
        # model pull — three very different failure modes.
        diag = ""
        if last_readyz_payload:
            diag = (
                f" Last /readyz: ollama_ok={last_readyz_payload.get('ollama_ok')} "
                f"model_ok={last_readyz_payload.get('model_ok')} "
                f"model={last_readyz_payload.get('vision_model')}"
            )
        raise RuntimeError(
            f"pod {pod_id} did not become ready within {timeout_sec:.0f}s "
            f"(after {attempts} readiness checks; final phase={last_phase}).{diag} "
            "Check the pod's container logs in the Runpod console."
        )

    def _submit_job(self, pod_id: str, video_path: Path) -> str:
        url = f"{self._pod_base_url(pod_id)}/jobs"
        parent = (video_path.parent.name or "").strip()
        stem = (video_path.stem or "").strip()
        if parent and stem and parent.lower() != stem.lower():
            scene_id = f"{parent}_{stem}"
        else:
            scene_id = stem or parent or video_path.name
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

    def _submit_cloud_job(
        self,
        pod_id: str,
        *,
        remote: str,
        path: str,
        rclone_config: str,
        scene_id: Optional[str],
        download_root: Optional[str] = None,
        relative_path: Optional[str] = None,
    ) -> str:
        base = self._pod_base_url(pod_id)
        body = {
            "remote": remote,
            "path": path,
            "rclone_config": rclone_config,
        }
        if scene_id:
            body["scene_id"] = scene_id
        if download_root:
            body["download_root"] = download_root
        if relative_path:
            body["relative_path"] = relative_path
        hdrs = {
            **self._headers(),
            "Content-Type": "application/json",
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        }
        # Each cycle tries /jobs/cloud first, then /jobs-cloud (the alias for
        # proxies that mishandle nested /jobs/...). If both return 404 — which
        # happens when Runpod's proxy is serving stale cached 404s from when
        # the pod-worker was still booting — we sleep and retry. Cache-busters
        # force the proxy to revalidate against origin.
        last_txt = ""
        last_status = 0
        for cycle in range(1, 5):
            for path_suffix in ("/jobs/cloud", "/jobs-cloud"):
                cache_buster = f"?cycle={cycle}&t={int(time.time() * 1000)}"
                url = f"{base}{path_suffix}{cache_buster}"
                resp = self._session.post(url, headers=hdrs, json=body, timeout=60.0)
                last_txt = resp.text[:300]
                last_status = resp.status_code
                if resp.status_code == 404:
                    continue  # try the other route
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"pod {pod_id} rejected cloud submit ({path_suffix}): "
                        f"HTTP {resp.status_code} {last_txt}"
                    )
                return resp.json()["job_id"]
            # Both routes returned 404. Sleep before retrying both again.
            time.sleep(min(2.0 * cycle, 6.0))
        raise RuntimeError(
            f"pod {pod_id} has no cloud submit route (HTTP {last_status} on "
            f"/jobs/cloud and /jobs-cloud after retries). Body: {last_txt}"
        )

    def _download_cloud_source_for_upload(
        self,
        *,
        cloud_source: CloudSource,
        rclone_config: str,
        on_log: LogHook,
        on_progress: ProgressHook,
    ) -> Path:
        """Fallback path when pod cloud-submit routes are unavailable.

        Download source on the controller via rclone, then upload with
        multipart ``POST /jobs`` (which has proven more stable through
        the Runpod proxy than ``POST /jobs/cloud`` on some pods).
        """
        from amg.cloud.rclone import Rclone, RcloneError

        scene_folder = (cloud_source.scene_id or Path(cloud_source.path).stem).strip() or "cloud_job"
        root = DATA_DIR / "cloud_submit_fallback" / f"{int(time.time())}_{scene_folder[:80]}"
        download_dir = root / scene_folder
        download_dir.mkdir(parents=True, exist_ok=True)

        copy_source = cloud_source.download_root or cloud_source.path
        cfg_fd, cfg_path = tempfile.mkstemp(prefix="amg-rclone-", suffix=".conf")
        try:
            with os.fdopen(cfg_fd, "w") as fh:
                fh.write(rclone_config)
            try:
                os.chmod(cfg_path, 0o600)
            except OSError:
                pass
            on_log(
                f"[runpod] fallback download {cloud_source.remote}:{copy_source} "
                f"-> {download_dir}"
            )
            try:
                Rclone(config_path=Path(cfg_path)).copy(
                    f"{cloud_source.remote}:{copy_source.lstrip('/')}",
                    download_dir,
                    on_progress=lambda stats: on_progress(min(25, int(0.25 * stats.get("pct", 0)))),
                    on_log=lambda line: on_log(f"[rclone-fallback] {line}"),
                )
            except RcloneError as exc:
                raise RuntimeError(f"fallback rclone copy failed: {exc}") from exc
        finally:
            try:
                os.unlink(cfg_path)
            except OSError:
                pass

        video_path = LocalBackend._locate_downloaded_video(
            download_dir,
            expected_name=Path(cloud_source.path).name,
            relative_path=cloud_source.relative_path,
        )
        if video_path is None:
            raise RuntimeError(
                f"fallback rclone copy reported success but no file landed in {download_dir}"
            )
        on_log(f"[runpod] fallback download complete -> {video_path}")
        return video_path

    def _wait_for_job(
        self,
        pod_id: str,
        job_id: str,
        on_log: LogHook,
        on_progress: ProgressHook,
    ) -> Dict[str, Any]:
        # Cache-bust every poll. The Runpod proxy (Cloudflare in front of
        # the pod) will otherwise cache the very first response (often
        # status=running or status=downloading taken seconds after submit)
        # and serve it for the rest of the job's lifetime, so the
        # controller never sees status=done. ``cf-cache-status: HIT, age:
        # NNNN`` is the smoking-gun symptom on the wire. The same pattern
        # is applied to readiness probes upstream and to the zip download
        # below.
        base_url = f"{self._pod_base_url(pod_id)}/jobs/{job_id}"
        poll_headers = {
            **self._headers(),
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        }
        deadline = time.monotonic() + self._run_timeout_sec
        last_seen_log_idx = 0
        transient_404s = 0
        while time.monotonic() < deadline:
            url = f"{base_url}?t={int(time.time() * 1000)}"
            resp = self._session.get(url, headers=poll_headers, timeout=30.0)
            if resp.status_code != 200:
                # Runpod proxy can briefly return 404 for /jobs/{id} right after
                # a successful submit even though the worker accepted the job.
                # Treat short 404 bursts as transient instead of failing the run.
                if resp.status_code == 404 and transient_404s < 12:
                    transient_404s += 1
                    on_log(
                        f"[runpod] transient 404 polling job {job_id} "
                        f"(attempt {transient_404s}/12); retrying"
                    )
                    time.sleep(max(self._poll_interval_sec, 2.0))
                    continue
                raise RuntimeError(
                    f"pod {pod_id} job {job_id} poll failed: HTTP {resp.status_code}"
                )
            transient_404s = 0
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
                result = body.get("result") or {}
                if isinstance(result, dict):
                    pod_timing = {
                        key: body.get(key)
                        for key in (
                            "source_kind",
                            "created_at",
                            "started_at",
                            "finished_at",
                            "created_at_ts",
                            "started_at_ts",
                            "finished_at_ts",
                            "download_started_at_ts",
                            "download_finished_at_ts",
                            "pipeline_wait_started_at_ts",
                            "pipeline_started_at_ts",
                            "pipeline_finished_at_ts",
                            "download_pct",
                            "progress_pct",
                        )
                        if key in body
                    }
                    if any(k.endswith("_ts") or k in {"created_at", "started_at", "finished_at"} for k in pod_timing):
                        result["_pod_job_timing"] = pod_timing
                return result
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
    ) -> Dict[str, Optional[Path]]:
        """Pull the artifact bundle and place pieces at controller-canonical paths.

        Pod ships a zip with this layout:
          ``work_dir/<files...>``  — covers, contact sheet, insight.json, etc.
          ``decision_log.json``    — pod-side decision log (optional)

        We extract:
          ``work_dir/...``  -> ``DATA_DIR/work_dirs/<scene_id>/``
          decision log     -> ``DATA_DIR/decision_logs/<safe_scene_id>.json``

        Returns the controller-side paths the caller should overwrite into
        the result dict so the UI reads from the right places. Older pods
        that don't know about the new layout still work — if the zip is
        flat (no ``work_dir/`` prefix and no ``decision_log.json``) we fall
        back to extracting the whole archive into ``DATA_DIR/work_dirs/<scene>/``
        like the v0 backend did.
        """
        from amg.config import DECISION_LOGS_DIR

        if not scene_id:
            scene_id = job_id  # fallback so we still land artifacts somewhere
        # Match the safe-id rules in amg.ui.app._safe_scene_id so the UI
        # finds extracted artifacts at the same path it computes for
        # decision_logs / reviewed payloads. Previously we used the raw
        # scene_id (with spaces and other punctuation) which silently
        # broke _find_work_dir lookups for any scene whose id wasn't
        # already a clean alphanumeric string.
        safe_scene_id = "".join(
            c if (c.isalnum() or c in "_-") else "_" for c in scene_id
        )[:120] or job_id
        # Cache-bust the zip pull for the same reason described in
        # ``_wait_for_job`` above — without this, the proxy can serve a
        # stale cached payload (or worse, a cached 404 from when the file
        # didn't exist yet) for the entire run.
        url = (
            f"{self._pod_base_url(pod_id)}/jobs/{job_id}/zip"
            f"?t={int(time.time() * 1000)}"
        )
        zip_headers = {
            **self._headers(),
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        }
        resp = self._session.get(url, headers=zip_headers, timeout=600.0, stream=True)
        if resp.status_code != 200:
            raise RuntimeError(
                f"pod {pod_id} /jobs/{job_id}/zip returned HTTP {resp.status_code}"
            )
        # Read into memory once so zipfile can seek; for typical scene-cover
        # output this is small (covers + JSON + contact sheet, well under
        # 50 MB). If we ever need to handle bigger payloads we can spool to a
        # temp file instead.
        buf = io.BytesIO()
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                buf.write(chunk)
        buf.seek(0)
        target = self._work_dirs_root / safe_scene_id
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".incoming")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)

        decision_log_dest: Optional[Path] = None
        bundle_layout = False

        with zipfile.ZipFile(buf, "r") as zf:
            names = zf.namelist()
            bundle_layout = any(
                n == "decision_log.json" or n.startswith("work_dir/")
                for n in names
            )
            if bundle_layout:
                for member in zf.infolist():
                    name = member.filename
                    if name.startswith("work_dir/") and not name.endswith("/"):
                        rel = Path(name).relative_to("work_dir")
                        out_path = tmp / rel
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(out_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                if "decision_log.json" in names:
                    DECISION_LOGS_DIR.mkdir(parents=True, exist_ok=True)
                    decision_log_dest = DECISION_LOGS_DIR / f"{safe_scene_id}.json"
                    with zf.open("decision_log.json") as src, open(decision_log_dest, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            else:
                # v0 layout: flat work_dir contents at zip root.
                zf.extractall(tmp)

        if target.exists():
            shutil.rmtree(target)
        tmp.rename(target)
        log.info(
            f"Extracted pod result for scene {scene_id}: work_dir={target}, "
            f"decision_log={decision_log_dest}, layout={'bundle' if bundle_layout else 'v0_flat'}"
        )
        return {"work_dir": target, "decision_log_path": decision_log_dest}


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
    "CloudSource",
    "JobBackend",
    "LocalBackend",
    "LogHook",
    "ProgressHook",
    "RunpodBackend",
    "get_backend",
]
