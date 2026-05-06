"""Tests for amg.cloud.runpod — Runpod REST client."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
import requests


# --- mock requests session --------------------------------------------------


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        json_body: Optional[Dict[str, Any]] = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json = json_body
        self.text = text

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("not json")
        return self._json


class _FakeSession:
    """In-memory fake of requests.Session.

    Stores every request made so tests can assert URL/method/body, and
    returns a queued sequence of canned responses (or a fallback). If the
    test ever requests more responses than queued, returns a 500.
    """

    def __init__(self, responses: List[_FakeResponse]) -> None:
        self.queued = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        json: Any = None,
        timeout: float = 0.0,
    ) -> _FakeResponse:
        self.calls.append({
            "method": method,
            "url": url,
            "headers": dict(headers or {}),
            "json": json,
            "timeout": timeout,
        })
        if self.queued:
            return self.queued.pop(0)
        return _FakeResponse(500, {"error": "queue exhausted"})


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Make wait_until_running's sleep instantaneous."""
    import amg.cloud.runpod as runpod_mod

    monkeypatch.setattr(runpod_mod.time, "sleep", lambda _s: None)


@pytest.fixture
def runpod_env(monkeypatch):
    monkeypatch.setenv("AMG_RUNPOD_API_KEY", "rp_test_key")
    monkeypatch.setenv("AMG_RUNPOD_API_BASE", "https://example.invalid/v1")
    monkeypatch.setenv("AMG_RUNPOD_IMAGE", "ghcr.io/test/amg-os:latest")
    monkeypatch.delenv("AMG_RUNPOD_NETWORK_VOLUME_ID", raising=False)


@pytest.fixture
def client_factory(runpod_env):
    """Returns a function that builds a RunpodClient with a fake session."""
    from amg.cloud.runpod import RunpodClient

    def _make(responses: List[_FakeResponse]) -> tuple[RunpodClient, _FakeSession]:
        session = _FakeSession(responses)
        return RunpodClient(session=session), session

    return _make


# --- construction / config --------------------------------------------------


class TestConstruction:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("AMG_RUNPOD_API_KEY", raising=False)
        from amg.cloud.runpod import RunpodAuthError, RunpodClient

        with pytest.raises(RunpodAuthError, match="AMG_RUNPOD_API_KEY"):
            RunpodClient()

    def test_explicit_api_key_overrides_env(self, monkeypatch):
        monkeypatch.delenv("AMG_RUNPOD_API_KEY", raising=False)
        from amg.cloud.runpod import RunpodClient

        c = RunpodClient(api_key="explicit")
        # We can't read it directly without poking at private state; smoke
        # check via _headers().
        assert c._headers()["Authorization"] == "Bearer explicit"

    def test_base_url_from_env(self, runpod_env):
        from amg.cloud.runpod import RunpodClient

        c = RunpodClient()
        assert c._base_url == "https://example.invalid/v1"

    def test_base_url_trailing_slash_stripped(self, monkeypatch):
        monkeypatch.setenv("AMG_RUNPOD_API_KEY", "k")
        monkeypatch.setenv("AMG_RUNPOD_API_BASE", "https://example.invalid/v1/")
        from amg.cloud.runpod import RunpodClient

        assert RunpodClient()._base_url == "https://example.invalid/v1"

    def test_api_key_not_logged(self, runpod_env, caplog):
        """The API key must never appear in logs (including DEBUG)."""
        import logging

        from amg.cloud.runpod import RunpodClient

        caplog.set_level(logging.DEBUG)
        c = RunpodClient()
        # Trigger a request that will fail (no fake session) to exercise log paths.
        try:
            c.get_pod("nope")
        except Exception:
            pass
        for rec in caplog.records:
            assert "rp_test_key" not in rec.getMessage()


# --- PodSpec ----------------------------------------------------------------


class TestPodSpec:
    def test_from_env_uses_image(self, runpod_env):
        from amg.cloud.runpod import PodSpec

        spec = PodSpec.from_env()
        assert spec.image == "ghcr.io/test/amg-os:latest"
        assert spec.gpu_type == "NVIDIA GeForce RTX 4090"
        assert spec.cloud_type == "SECURE"

    def test_from_env_missing_image_raises(self, monkeypatch):
        monkeypatch.delenv("AMG_RUNPOD_IMAGE", raising=False)
        from amg.cloud.runpod import PodSpec, RunpodError

        with pytest.raises(RunpodError, match="AMG_RUNPOD_IMAGE"):
            PodSpec.from_env()

    def test_from_env_overrides_apply(self, runpod_env):
        from amg.cloud.runpod import PodSpec

        spec = PodSpec.from_env(gpu_type="NVIDIA H100 PCIe", gpu_count=2)
        assert spec.gpu_type == "NVIDIA H100 PCIe"
        assert spec.gpu_count == 2

    def test_gpu_type_env_override(self, runpod_env, monkeypatch):
        monkeypatch.setenv("AMG_RUNPOD_GPU_TYPE", "NVIDIA RTX A6000")
        from amg.cloud.runpod import PodSpec

        assert PodSpec.from_env().gpu_type == "NVIDIA RTX A6000"

    def test_network_volume_env_picked_up(self, runpod_env, monkeypatch):
        monkeypatch.setenv("AMG_RUNPOD_NETWORK_VOLUME_ID", "vol_abc")
        from amg.cloud.runpod import PodSpec

        assert PodSpec.from_env().network_volume_id == "vol_abc"


# --- create_pod / get_pod / terminate ---------------------------------------


class TestPodLifecycle:
    def test_create_pod_payload(self, client_factory):
        client, session = client_factory([
            _FakeResponse(201, {"id": "pod_123", "desiredStatus": "PROVISIONING"}),
        ])
        from amg.cloud.runpod import PodSpec

        spec = PodSpec.from_env(name="amg-job-1", env={"FOO": "bar"})
        out = client.create_pod(spec)
        assert out["id"] == "pod_123"
        assert len(session.calls) == 1
        call = session.calls[0]
        assert call["method"] == "POST"
        assert call["url"].endswith("/pods")
        body = call["json"]
        assert body["name"] == "amg-job-1"
        assert body["imageName"] == "ghcr.io/test/amg-os:latest"
        assert body["gpuTypeIds"] == ["NVIDIA GeForce RTX 4090"]
        assert body["env"] == {"FOO": "bar"}
        assert "8000/http" in body["ports"]
        assert body["cloudType"] == "SECURE"

    def test_create_pod_with_network_volume(self, client_factory):
        client, session = client_factory([
            _FakeResponse(201, {"id": "pod_x"}),
        ])
        from amg.cloud.runpod import PodSpec

        spec = PodSpec.from_env(network_volume_id="vol_abc")
        client.create_pod(spec)
        assert session.calls[0]["json"]["networkVolumeId"] == "vol_abc"

    def test_create_pod_omits_network_volume_when_none(self, client_factory):
        client, session = client_factory([
            _FakeResponse(201, {"id": "pod_x"}),
        ])
        from amg.cloud.runpod import PodSpec

        spec = PodSpec.from_env()
        client.create_pod(spec)
        assert "networkVolumeId" not in session.calls[0]["json"]

    def test_get_pod(self, client_factory):
        client, session = client_factory([
            _FakeResponse(200, {"id": "pod_1", "desiredStatus": "RUNNING"}),
        ])
        out = client.get_pod("pod_1")
        assert out["desiredStatus"] == "RUNNING"
        assert session.calls[0]["method"] == "GET"
        assert session.calls[0]["url"].endswith("/pods/pod_1")

    def test_terminate_pod(self, client_factory):
        client, session = client_factory([_FakeResponse(204)])
        assert client.terminate_pod("pod_1") is True
        assert session.calls[0]["method"] == "DELETE"

    def test_terminate_already_gone_is_idempotent(self, client_factory):
        client, _ = client_factory([
            _FakeResponse(404, {"error": "not found"}),
        ])
        assert client.terminate_pod("ghost") is True

    def test_terminate_real_failure_propagates(self, client_factory):
        from amg.cloud.runpod import RunpodAPIError

        client, _ = client_factory([
            _FakeResponse(500, {"error": "server boom"}),
        ])
        with pytest.raises(RunpodAPIError, match="HTTP 500"):
            client.terminate_pod("pod_1")

    def test_auth_failure_raises_clear_error(self, client_factory):
        from amg.cloud.runpod import RunpodAuthError

        client, _ = client_factory([
            _FakeResponse(401, {"error": "invalid api key"}),
        ])
        with pytest.raises(RunpodAuthError, match="rejected the API key"):
            client.get_pod("pod_1")


# --- wait_until_running -----------------------------------------------------


class TestWaitUntilRunning:
    def test_returns_when_running_with_port_mappings(self, client_factory):
        client, _ = client_factory([
            _FakeResponse(200, {"desiredStatus": "PROVISIONING"}),
            _FakeResponse(200, {"desiredStatus": "RUNNING", "portMappings": None}),
            _FakeResponse(200, {
                "desiredStatus": "RUNNING",
                "portMappings": {"8000": 12345},
                "publicIp": "1.2.3.4",
            }),
        ])
        pod = client.wait_until_running("pod_1", timeout=60.0, poll_interval=0.0)
        assert pod["desiredStatus"] == "RUNNING"
        assert pod["portMappings"]["8000"] == 12345

    def test_returns_running_without_mappings_when_not_required(self, client_factory):
        client, _ = client_factory([
            _FakeResponse(200, {"desiredStatus": "RUNNING"}),
        ])
        pod = client.wait_until_running(
            "pod_1",
            timeout=60.0,
            poll_interval=0.0,
            require_port_mappings=False,
        )
        assert pod["desiredStatus"] == "RUNNING"

    def test_terminal_failure_raises(self, client_factory):
        from amg.cloud.runpod import RunpodError

        client, _ = client_factory([
            _FakeResponse(200, {
                "desiredStatus": "EXITED",
                "lastStatusChange": "image pull failed",
            }),
        ])
        with pytest.raises(RunpodError, match="image pull failed"):
            client.wait_until_running("pod_1", timeout=60.0, poll_interval=0.0)

    def test_timeout_raises(self, client_factory, monkeypatch):
        from amg.cloud.runpod import RunpodTimeoutError

        # All polls return PROVISIONING, but we patch monotonic so the
        # deadline expires after the second call.
        client, _ = client_factory([
            _FakeResponse(200, {"desiredStatus": "PROVISIONING"}),
            _FakeResponse(200, {"desiredStatus": "PROVISIONING"}),
            _FakeResponse(200, {"desiredStatus": "PROVISIONING"}),
        ])
        import amg.cloud.runpod as runpod_mod

        ticks = iter([0.0, 1.0, 2.0, 999.0, 1000.0, 1001.0])
        monkeypatch.setattr(runpod_mod.time, "monotonic", lambda: next(ticks))
        with pytest.raises(RunpodTimeoutError):
            client.wait_until_running("pod_1", timeout=10.0, poll_interval=0.0)


# --- proxy_url --------------------------------------------------------------


class TestProxyUrl:
    def test_proxy_url_format(self):
        from amg.cloud.runpod import RunpodClient

        url = RunpodClient.proxy_url("xedezhzb9la3ye", 8000)
        assert url == "https://xedezhzb9la3ye-8000.proxy.runpod.net"


# --- provision_pod (high-level) ---------------------------------------------


class TestProvisionPod:
    def test_provision_creates_then_waits(self, client_factory):
        client, session = client_factory([
            _FakeResponse(201, {"id": "pod_xyz", "desiredStatus": "PROVISIONING"}),
            _FakeResponse(200, {
                "id": "pod_xyz",
                "desiredStatus": "RUNNING",
                "portMappings": {"8000": 21000},
            }),
        ])
        from amg.cloud.runpod import PodSpec

        pod = client.provision_pod(
            PodSpec.from_env(),
            ready_timeout=60.0,
            poll_interval=0.0,
        )
        assert pod["id"] == "pod_xyz"
        assert pod["desiredStatus"] == "RUNNING"
        # First call: POST /pods. Second+: GET /pods/pod_xyz.
        assert session.calls[0]["method"] == "POST"
        assert session.calls[0]["url"].endswith("/pods")
        assert session.calls[1]["method"] == "GET"
        assert session.calls[1]["url"].endswith("/pods/pod_xyz")

    def test_provision_returns_when_running_even_without_port_mappings(
        self, client_factory
    ):
        """Regression test for the v1/pods REST API shape.

        The new REST endpoint NEVER populates ``portMappings`` — that was a
        GraphQL-API-era field. provision_pod must accept a bare RUNNING
        status and return; the actual readiness signal is the
        ``/healthz`` poll downstream in RunpodBackend, not metadata."""
        client, _ = client_factory([
            _FakeResponse(201, {"id": "pod_rest", "desiredStatus": "PROVISIONING"}),
            _FakeResponse(200, {
                "id": "pod_rest",
                "desiredStatus": "RUNNING",
                # No portMappings key at all — matches live REST response.
                "ports": ["8000/http"],
                "publicIp": "",
                "machine": {},
            }),
        ])
        from amg.cloud.runpod import PodSpec

        pod = client.provision_pod(
            PodSpec.from_env(),
            ready_timeout=10.0,
            poll_interval=0.0,
        )
        assert pod["desiredStatus"] == "RUNNING"
        assert "portMappings" not in pod  # confirms we don't depend on it

    def test_provision_failure_terminates_pod_for_cleanup(self, client_factory):
        from amg.cloud.runpod import RunpodError

        client, session = client_factory([
            _FakeResponse(201, {"id": "pod_doomed"}),
            _FakeResponse(200, {"desiredStatus": "EXITED", "lastStatusChange": "boom"}),
            _FakeResponse(204),  # terminate cleanup
        ])
        from amg.cloud.runpod import PodSpec

        with pytest.raises(RunpodError):
            client.provision_pod(
                PodSpec.from_env(),
                ready_timeout=60.0,
                poll_interval=0.0,
            )
        # Cleanup DELETE should have fired.
        methods = [c["method"] for c in session.calls]
        assert "DELETE" in methods

    def test_provision_failure_cleanup_swallows_secondary_error(self, client_factory):
        """If terminate_pod also fails, the original RunpodError still wins."""
        from amg.cloud.runpod import RunpodError

        client, _ = client_factory([
            _FakeResponse(201, {"id": "pod_doomed"}),
            _FakeResponse(200, {"desiredStatus": "EXITED", "lastStatusChange": "boom"}),
            _FakeResponse(500, {"error": "cleanup failed too"}),
        ])
        from amg.cloud.runpod import PodSpec

        with pytest.raises(RunpodError, match="boom"):
            client.provision_pod(
                PodSpec.from_env(),
                ready_timeout=60.0,
                poll_interval=0.0,
            )


# --- transport-level errors -------------------------------------------------


class TestTransportErrors:
    def test_network_error_wrapped(self, monkeypatch, runpod_env):
        from amg.cloud.runpod import RunpodAPIError, RunpodClient

        class _BoomSession:
            def request(self, *_a, **_k):
                raise requests.ConnectionError("dns failure")

        client = RunpodClient(session=_BoomSession())
        with pytest.raises(RunpodAPIError, match="network error"):
            client.get_pod("pod_1")
