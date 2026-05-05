"""Tests for amg.cloud.job_backend — pluggable job runner."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import pytest


# --- factory ----------------------------------------------------------------


class TestFactory:
    def test_default_is_local(self, monkeypatch):
        monkeypatch.delenv("AMG_JOB_BACKEND", raising=False)
        from amg.cloud.job_backend import LocalBackend, get_backend

        assert isinstance(get_backend(), LocalBackend)

    def test_explicit_local(self, monkeypatch):
        monkeypatch.setenv("AMG_JOB_BACKEND", "local")
        from amg.cloud.job_backend import LocalBackend, get_backend

        assert isinstance(get_backend(), LocalBackend)

    def test_unknown_backend_raises(self, monkeypatch):
        monkeypatch.setenv("AMG_JOB_BACKEND", "wat")
        from amg.cloud.job_backend import get_backend

        with pytest.raises(RuntimeError, match="not a known backend"):
            get_backend()

    def test_runpod_requires_pod_token(self, monkeypatch):
        monkeypatch.setenv("AMG_JOB_BACKEND", "runpod")
        monkeypatch.setenv("AMG_RUNPOD_API_KEY", "k")
        monkeypatch.setenv("AMG_RUNPOD_IMAGE", "img")
        monkeypatch.delenv("AMG_POD_AUTH_TOKEN", raising=False)
        from amg.cloud.job_backend import get_backend

        with pytest.raises(RuntimeError, match="AMG_POD_AUTH_TOKEN"):
            get_backend()


# --- LocalBackend -----------------------------------------------------------


class TestLocalBackend:
    def test_runs_process_scene_and_returns_result(self, monkeypatch, tmp_path):
        called: Dict[str, Any] = {}

        def _fake(video_path):
            called["video_path"] = video_path
            return {"success": True, "scene_id": "scene-1", "covers_saved": 7}

        import amg.pipeline as pipeline
        monkeypatch.setattr(pipeline, "process_scene", _fake)

        from amg.cloud.job_backend import LocalBackend

        video = tmp_path / "scene.mp4"
        video.write_bytes(b"x")
        backend = LocalBackend()
        logs: List[str] = []
        progresses: List[int] = []
        result = backend.run_job(
            video,
            on_log=logs.append,
            on_progress=progresses.append,
        )
        assert result["success"] is True
        assert result["scene_id"] == "scene-1"
        assert called["video_path"] == video
        assert any("starting process_scene" in line for line in logs)
        assert progresses[-1] == 100


# --- RunpodBackend (mocked) -------------------------------------------------


class _FakePodResponse:
    def __init__(self, status: int = 200, json_body: Any = None, content: bytes = b""):
        self.status_code = status
        self._json = json_body
        self._content = content
        self.text = "" if json_body is None else str(json_body)

    def json(self):
        return self._json

    @property
    def content(self):
        return self._content

    def iter_content(self, chunk_size=1):
        yield self._content


class _FakeHttpSession:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.responses: List[_FakePodResponse] = []

    def queue(self, *responses: _FakePodResponse) -> None:
        self.responses.extend(responses)

    def _next(self) -> _FakePodResponse:
        if not self.responses:
            raise RuntimeError("fake http session has no more queued responses")
        return self.responses.pop(0)

    def post(self, url, *, headers=None, files=None, data=None, timeout=None):
        self.calls.append({"method": "POST", "url": url, "data": data,
                           "files": list(files.keys()) if files else None})
        return self._next()

    def get(self, url, *, headers=None, timeout=None, stream=False):
        self.calls.append({"method": "GET", "url": url, "stream": stream})
        return self._next()

    def close(self):
        pass


class _FakeRunpodClient:
    """Stand-in for amg.cloud.runpod.RunpodClient."""

    def __init__(self) -> None:
        self.provisioned: List[Any] = []
        self.terminated: List[str] = []
        self.next_pod_id = "pod_test"
        self.terminate_should_fail = False

    def provision_pod(self, spec, *, ready_timeout=600.0, poll_interval=5.0):
        self.provisioned.append(spec)
        return {"id": self.next_pod_id, "desiredStatus": "RUNNING"}

    def terminate_pod(self, pod_id):
        self.terminated.append(pod_id)
        if self.terminate_should_fail:
            raise RuntimeError("simulated terminate failure")
        return True


def _make_zip_bytes(files: Dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


@pytest.fixture
def runpod_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("AMG_RUNPOD_API_KEY", "rk_test")
    monkeypatch.setenv("AMG_RUNPOD_IMAGE", "ghcr.io/test/amg:latest")
    monkeypatch.setenv("AMG_POD_AUTH_TOKEN", "x" * 48)
    # Avoid real waits in tests.
    monkeypatch.setenv("AMG_JOB_POLL_INTERVAL_SEC", "0")

    import importlib

    import amg.cloud.runpod as runpod_mod
    importlib.reload(runpod_mod)
    import amg.cloud.job_backend as jb_mod
    importlib.reload(jb_mod)

    from amg.cloud.job_backend import RunpodBackend

    fake_client = _FakeRunpodClient()
    fake_session = _FakeHttpSession()
    backend = RunpodBackend(
        client=fake_client,
        spec=runpod_mod.PodSpec.from_env(),
        http_session=fake_session,
        work_dirs_root=tmp_path / "work_dirs",
        run_timeout_sec=60.0,
    )
    return backend, fake_client, fake_session, tmp_path


def test_runpod_backend_full_happy_path(runpod_backend, tmp_path, monkeypatch):
    """Provision -> upload -> poll -> download zip -> extract -> terminate."""
    backend, client, session, work_root = runpod_backend
    # Make sleep immediate.
    import amg.cloud.job_backend as jb
    monkeypatch.setattr(jb.time, "sleep", lambda _s: None)

    final_result = {
        "success": True,
        "scene_id": "scene-42",
        "covers_saved": 12,
    }
    zip_bytes = _make_zip_bytes({
        "out/cover_001.jpg": b"jpg",
        "out/decision_log.json": b'{"k":"v"}',
    })

    session.queue(
        _FakePodResponse(200, {"job_id": "j1", "status": "queued"}),       # POST /jobs
        _FakePodResponse(200, {                                            # GET /jobs/j1 (running)
            "status": "running",
            "log_tail": ["one", "two"],
            "progress_pct": 50,
        }),
        _FakePodResponse(200, {                                            # GET /jobs/j1 (done)
            "status": "done",
            "log_tail": ["one", "two", "three"],
            "progress_pct": 100,
            "result": final_result,
        }),
        _FakePodResponse(200, content=zip_bytes),                          # GET /jobs/j1/zip
    )

    video_dir = tmp_path / "scene-42"
    video_dir.mkdir()
    video = video_dir / "scene.mp4"
    video.write_bytes(b"video")

    logs: List[str] = []
    progresses: List[int] = []
    result = backend.run_job(video, on_log=logs.append, on_progress=progresses.append)

    assert result == final_result
    assert client.provisioned, "should have provisioned a pod"
    assert client.terminated == ["pod_test"], "should have terminated the pod"
    # Pod env should include the auth token from RunpodBackend so the worker
    # accepts the controller's bearer requests.
    assert client.provisioned[0].env.get("AMG_POD_AUTH_TOKEN") == "x" * 48
    # Artifacts extracted to work_dirs/<scene_id>/
    extracted = work_root / "work_dirs" / "scene-42"
    assert (extracted / "out" / "cover_001.jpg").read_bytes() == b"jpg"
    # Each pod log line surfaces through on_log.
    assert any("[pod] one" in line for line in logs)
    assert any("[pod] three" in line for line in logs)
    # Progress should have advanced at least once and ended at 100.
    assert progresses[-1] == 100


def test_runpod_backend_pipeline_error_propagates_and_terminates(runpod_backend, tmp_path, monkeypatch):
    backend, client, session, _work_root = runpod_backend
    import amg.cloud.job_backend as jb
    monkeypatch.setattr(jb.time, "sleep", lambda _s: None)

    session.queue(
        _FakePodResponse(200, {"job_id": "j1", "status": "queued"}),
        _FakePodResponse(200, {
            "status": "error",
            "log_tail": ["boom"],
            "error": "ollama unreachable",
        }),
    )
    video_dir = tmp_path / "scene-x"
    video_dir.mkdir()
    video = video_dir / "v.mp4"
    video.write_bytes(b"x")

    with pytest.raises(RuntimeError, match="ollama unreachable"):
        backend.run_job(video)
    assert client.terminated == ["pod_test"], "pod should be terminated on pipeline error"


def test_runpod_backend_terminate_failure_does_not_mask_result(runpod_backend, tmp_path, monkeypatch):
    backend, client, session, work_root = runpod_backend
    import amg.cloud.job_backend as jb
    monkeypatch.setattr(jb.time, "sleep", lambda _s: None)
    client.terminate_should_fail = True

    final_result = {"success": True, "scene_id": "scene-z", "covers_saved": 3}
    session.queue(
        _FakePodResponse(200, {"job_id": "j1", "status": "queued"}),
        _FakePodResponse(200, {
            "status": "done",
            "log_tail": [],
            "progress_pct": 100,
            "result": final_result,
        }),
        _FakePodResponse(200, content=_make_zip_bytes({"out/x.jpg": b"x"})),
    )
    video_dir = tmp_path / "scene-z"
    video_dir.mkdir()
    video = video_dir / "v.mp4"
    video.write_bytes(b"x")

    # Even though terminate raises, the result should still be returned.
    result = backend.run_job(video)
    assert result == final_result
    assert client.terminated == ["pod_test"]


def test_runpod_backend_upload_failure(runpod_backend, tmp_path, monkeypatch):
    backend, client, session, _ = runpod_backend
    import amg.cloud.job_backend as jb
    monkeypatch.setattr(jb.time, "sleep", lambda _s: None)
    session.queue(_FakePodResponse(401, {"error": "invalid token"}))

    video_dir = tmp_path / "scene-q"
    video_dir.mkdir()
    video = video_dir / "v.mp4"
    video.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="rejected /jobs upload"):
        backend.run_job(video)
    # Pod still provisioned + terminated (cleanup runs).
    assert client.terminated == ["pod_test"]


def test_runpod_backend_run_timeout(runpod_backend, tmp_path, monkeypatch):
    backend, _client, session, _ = runpod_backend
    backend._run_timeout_sec = 0.0  # immediate timeout

    session.queue(_FakePodResponse(200, {"job_id": "j1", "status": "queued"}))
    # No more responses queued — the wait loop should bail before polling.
    video_dir = tmp_path / "scene-t"
    video_dir.mkdir()
    video = video_dir / "v.mp4"
    video.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="did not finish"):
        backend.run_job(video)
