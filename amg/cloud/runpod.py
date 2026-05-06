"""
Runpod REST API client for the AMG cloud-hosted edition.

Wraps the Runpod v1 REST API (https://rest.runpod.io/v1) for the operations
the controller VM needs to spin up an on-demand GPU pod, push a job to it,
and tear it down — without holding a long-running expensive GPU when no
work is queued.

Why a custom client instead of runpod-python:

The official runpod-python SDK is primarily geared toward Serverless
endpoints and the legacy GraphQL API. The REST API at /v1 is the supported
path forward for Pod management as of 2025+, and the REST surface we need
is small enough that a focused wrapper is easier to reason about (and test)
than depending on a multi-purpose SDK.

Configuration (env vars):

* ``AMG_RUNPOD_API_KEY`` — required at use time. Generated from the Runpod
  dashboard. The client never logs or prints this value.
* ``AMG_RUNPOD_API_BASE`` — override base URL (default
  ``https://rest.runpod.io/v1``). Useful for testing against a fake server.
* ``AMG_RUNPOD_GPU_TYPE`` — default GPU type id. Default is
  ``NVIDIA GeForce RTX 4090``, which is the best price/perf for qwen2.5vl:7b
  inference and is widely available across Runpod data centers.
* ``AMG_RUNPOD_IMAGE`` — default Docker image for the pod. Set this to the
  image you publish from this repo's Dockerfile (e.g.
  ``ghcr.io/<owner>/amg-os:latest``).
* ``AMG_RUNPOD_NETWORK_VOLUME_ID`` — optional persistent network volume for
  caching the Ollama model weights + AMG data dir. With this attached, pod
  startups skip a 5GB+ model pull each time.
* ``AMG_RUNPOD_CLOUD_TYPE`` — ``SECURE`` (default) or ``COMMUNITY``. SECURE
  is more reliable; COMMUNITY is cheaper but spot-style.

Public proxy URL convention:

For HTTP-typed ports, Runpod exposes a stable HTTPS proxy at
``https://<pod-id>-<port>.proxy.runpod.net``. The controller reaches the
pod-side worker through that URL — no need to manage IPs/firewalls.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger("amg.cloud.runpod")


DEFAULT_API_BASE = "https://rest.runpod.io/v1"
DEFAULT_GPU_TYPE = "NVIDIA GeForce RTX 4090"
DEFAULT_CLOUD_TYPE = "SECURE"
DEFAULT_PORTS = ["8000/http"]
DEFAULT_CONTAINER_DISK_GB = 50
DEFAULT_VOLUME_GB = 20
DEFAULT_TIMEOUT_SEC = 30.0


# ---------- exceptions ----------


class RunpodError(Exception):
    """Base class for Runpod client failures."""


class RunpodAuthError(RunpodError):
    """API key missing or rejected by Runpod."""


class RunpodAPIError(RunpodError):
    """Runpod returned a non-2xx HTTP response."""

    def __init__(self, status_code: int, message: str, body: Any = None) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.body = body


class RunpodTimeoutError(RunpodError):
    """Pod did not reach the desired state in time."""


# ---------- value objects ----------


@dataclass
class PodSpec:
    """High-level description of a pod to provision.

    Field defaults come from the AMG_RUNPOD_* env vars resolved at construction
    time. Attributes can still be overridden per call. The dataclass exists so
    the dispatcher can build one of these once at startup and reuse it across
    jobs without re-reading env on every provision.
    """

    image: str
    gpu_type: str = DEFAULT_GPU_TYPE
    gpu_count: int = 1
    name: str = "amg-pod"
    container_disk_gb: int = DEFAULT_CONTAINER_DISK_GB
    volume_gb: int = DEFAULT_VOLUME_GB
    ports: List[str] = field(default_factory=lambda: list(DEFAULT_PORTS))
    env: Dict[str, str] = field(default_factory=dict)
    network_volume_id: Optional[str] = None
    cloud_type: str = DEFAULT_CLOUD_TYPE
    interruptible: bool = False

    @classmethod
    def from_env(cls, **overrides: Any) -> "PodSpec":
        defaults: Dict[str, Any] = {
            "image": os.environ.get("AMG_RUNPOD_IMAGE", "").strip(),
            "gpu_type": os.environ.get("AMG_RUNPOD_GPU_TYPE", DEFAULT_GPU_TYPE),
            "cloud_type": os.environ.get("AMG_RUNPOD_CLOUD_TYPE", DEFAULT_CLOUD_TYPE),
            "network_volume_id": os.environ.get("AMG_RUNPOD_NETWORK_VOLUME_ID") or None,
        }
        defaults.update(overrides)
        if not defaults.get("image"):
            raise RunpodError(
                "PodSpec requires an image. Set AMG_RUNPOD_IMAGE or pass image=... "
                "(this is the Docker image built from the repo Dockerfile and "
                "pushed to a registry Runpod can pull from)."
            )
        return cls(**defaults)


# ---------- client ----------


class RunpodClient:
    """Thin REST client over the Runpod v1 API for pod lifecycle ops."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        session: Optional[requests.Session] = None,
    ) -> None:
        key = (api_key or os.environ.get("AMG_RUNPOD_API_KEY", "")).strip()
        if not key:
            raise RunpodAuthError(
                "AMG_RUNPOD_API_KEY is not set. Generate one at "
                "https://www.runpod.io/console/user/settings and export it."
            )
        self._api_key = key
        self._base_url = (base_url or os.environ.get("AMG_RUNPOD_API_BASE", DEFAULT_API_BASE)).rstrip("/")
        self._timeout = float(timeout)
        self._session = session or requests.Session()

    # ----- low-level HTTP -----

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, *, json: Any = None) -> Any:
        url = f"{self._base_url}{path}"
        try:
            resp = self._session.request(
                method,
                url,
                headers=self._headers(),
                json=json,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise RunpodAPIError(0, f"network error: {exc}") from exc

        if resp.status_code == 401 or resp.status_code == 403:
            raise RunpodAuthError(
                f"Runpod rejected the API key (HTTP {resp.status_code}). "
                "Verify AMG_RUNPOD_API_KEY is current and not revoked."
            )
        if resp.status_code == 204:
            return None
        body: Any = None
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        if resp.status_code >= 400:
            msg = (body or {}).get("error") if isinstance(body, dict) else str(body)
            raise RunpodAPIError(resp.status_code, str(msg or "request failed"), body=body)
        return body

    # ----- pod lifecycle -----

    def create_pod(self, spec: PodSpec) -> Dict[str, Any]:
        """Create a pod from a PodSpec. Returns the pod object (id, status, ...).

        Note: this returns immediately after the pod row is created. The pod is
        usually still booting; use ``wait_until_running`` to block until it's
        reachable.
        """
        payload: Dict[str, Any] = {
            "name": spec.name,
            "imageName": spec.image,
            "gpuTypeIds": [spec.gpu_type],
            "gpuCount": int(spec.gpu_count),
            "containerDiskInGb": int(spec.container_disk_gb),
            "volumeInGb": int(spec.volume_gb),
            "ports": list(spec.ports),
            "env": dict(spec.env),
            "cloudType": spec.cloud_type,
            "interruptible": bool(spec.interruptible),
        }
        if spec.network_volume_id:
            payload["networkVolumeId"] = spec.network_volume_id
        log.info(
            "Creating Runpod pod (image=%s gpu=%s gpus=%d cloud=%s)",
            spec.image, spec.gpu_type, spec.gpu_count, spec.cloud_type,
        )
        return self._request("POST", "/pods", json=payload)

    def get_pod(self, pod_id: str) -> Dict[str, Any]:
        """Read current pod state (returns the same Pod schema as create_pod)."""
        return self._request("GET", f"/pods/{pod_id}")

    def terminate_pod(self, pod_id: str) -> bool:
        """Permanently delete a pod and stop billing. Returns True on success."""
        log.info("Terminating Runpod pod %s", pod_id)
        try:
            self._request("DELETE", f"/pods/{pod_id}")
            return True
        except RunpodAPIError as exc:
            if exc.status_code == 404:
                # Pod already gone — treat as success so retries are idempotent.
                return True
            raise

    def stop_pod(self, pod_id: str) -> Dict[str, Any]:
        """Stop a pod without deleting it. Container disk is wiped, but a
        network volume (if attached) survives. Resuming is faster than a
        fresh provision."""
        log.info("Stopping Runpod pod %s", pod_id)
        return self._request("POST", f"/pods/{pod_id}/stop")

    # ----- waiting / readiness -----

    def wait_until_running(
        self,
        pod_id: str,
        *,
        timeout: float = 600.0,
        poll_interval: float = 5.0,
        require_port_mappings: bool = True,
    ) -> Dict[str, Any]:
        """Poll until the pod's desired status is RUNNING and (optionally) its
        port mappings are populated. Returns the final pod object.

        Raises RunpodTimeoutError if the deadline is hit before the pod is
        RUNNING with port mappings populated, or RunpodError if the pod ends
        up in a terminal failed state along the way."""
        deadline = time.monotonic() + float(timeout)
        last_status: Optional[str] = None
        while time.monotonic() < deadline:
            pod = self.get_pod(pod_id)
            status = pod.get("desiredStatus")
            if status != last_status:
                log.info("Pod %s status: %s", pod_id, status)
                last_status = status
            if status in {"EXITED", "TERMINATED"}:
                raise RunpodError(
                    f"Pod {pod_id} ended up in status {status} before becoming RUNNING. "
                    f"Last lifecycle event: {pod.get('lastStatusChange')!r}"
                )
            if status == "RUNNING":
                if not require_port_mappings:
                    return pod
                mappings = pod.get("portMappings") or {}
                if mappings:
                    return pod
            time.sleep(poll_interval)
        raise RunpodTimeoutError(
            f"Pod {pod_id} did not reach RUNNING with port mappings within "
            f"{timeout:.0f}s (last status={last_status!r})."
        )

    # ----- url helpers -----

    @staticmethod
    def proxy_url(pod_id: str, port: int) -> str:
        """Return the stable HTTPS URL Runpod exposes for an HTTP-typed port.

        This URL is reachable from anywhere on the public internet (no auth
        beyond Runpod's proxy). The pod-side worker is responsible for its
        own auth (we'll pass a per-pod shared secret via env when we wire up
        the dispatcher in a later commit)."""
        return f"https://{pod_id}-{int(port)}.proxy.runpod.net"

    # ----- high-level convenience -----

    def provision_pod(
        self,
        spec: PodSpec,
        *,
        ready_timeout: float = 600.0,
        poll_interval: float = 5.0,
    ) -> Dict[str, Any]:
        """Create a pod and block until Runpod reports desiredStatus=RUNNING.

        We deliberately do NOT wait for ``portMappings`` to populate. The
        old GraphQL API returned that field eagerly, but the new REST
        ``v1/pods`` response shape (observed live 2026-05-06) doesn't
        include it at all — the field is null/absent for the entire pod
        lifetime, so blocking on it just hangs until the readiness timeout.

        We don't need it anyway. Runpod auto-routes
        ``https://<pod-id>-<port>.proxy.runpod.net`` as soon as the
        container starts listening, and the *real* readiness check
        downstream is ``RunpodBackend._wait_for_pod_worker_ready()``,
        which polls ``/healthz`` over that proxy URL.

        Returns the final pod object. Caller is responsible for
        terminating the pod when work is done (or for catching/wrapping
        this in a try/finally with terminate_pod)."""
        pod = self.create_pod(spec)
        pod_id = pod.get("id")
        if not pod_id:
            raise RunpodError(f"create_pod response missing 'id': {pod!r}")
        try:
            return self.wait_until_running(
                pod_id,
                timeout=ready_timeout,
                poll_interval=poll_interval,
                require_port_mappings=False,
            )
        except (RunpodError, RunpodTimeoutError):
            # Best-effort cleanup on a failed provision so we don't leak a pod
            # that's still going to bill us.
            log.warning("Provision failed; attempting to terminate pod %s", pod_id)
            try:
                self.terminate_pod(pod_id)
            except Exception:  # noqa: BLE001 - cleanup must not mask the original error
                log.exception("Cleanup terminate_pod(%s) also failed", pod_id)
            raise


__all__ = [
    "DEFAULT_API_BASE",
    "DEFAULT_CLOUD_TYPE",
    "DEFAULT_CONTAINER_DISK_GB",
    "DEFAULT_GPU_TYPE",
    "DEFAULT_PORTS",
    "DEFAULT_VOLUME_GB",
    "PodSpec",
    "RunpodAPIError",
    "RunpodAuthError",
    "RunpodClient",
    "RunpodError",
    "RunpodTimeoutError",
]
