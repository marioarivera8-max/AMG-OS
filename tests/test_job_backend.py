"""Tests for amg.cloud.job_backend — pluggable job runner."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

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
    def __init__(self, auto_healthz_ok: bool = True) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.responses: List[_FakePodResponse] = []
        self._auto_healthz_ok = auto_healthz_ok

    def queue(self, *responses: _FakePodResponse) -> None:
        self.responses.extend(responses)

    def _next(self) -> _FakePodResponse:
        if not self.responses:
            raise RuntimeError("fake http session has no more queued responses")
        return self.responses.pop(0)

    def post(self, url, *, headers=None, files=None, data=None, json=None, timeout=None):
        self.calls.append({
            "method": "POST",
            "url": url,
            "data": data,
            "files": list(files.keys()) if files else None,
            "json": json,
        })
        return self._next()

    def get(self, url, *, headers=None, timeout=None, stream=False):
        self.calls.append({"method": "GET", "url": url, "stream": stream})
        # /healthz is the TCP/proxy readiness probe. /jobs (authenticated)
        # proves our FastAPI app + bearer middleware are mounted — the
        # controller requires both before submitting work (see RunpodBackend
        # ._wait_for_pod_worker_ready). Auto-respond so queue-based tests
        # only model POST/poll/zip traffic.
        if self._auto_healthz_ok and url.endswith("/healthz"):
            return _FakePodResponse(200, {"status": "ok"})
        if self._auto_healthz_ok:
            path = urlparse(url).path.rstrip("/")
            if path == "/jobs":
                return _FakePodResponse(200, {"job_ids": []})
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

    assert result["success"] is True
    assert result["scene_id"] == "scene-42"
    assert result["covers_saved"] == 12
    assert client.provisioned, "should have provisioned a pod"
    assert client.terminated == ["pod_test"], "should have terminated the pod"
    # Pod env should include the auth token from RunpodBackend so the worker
    # accepts the controller's bearer requests.
    assert client.provisioned[0].env.get("AMG_POD_AUTH_TOKEN") == "x" * 48
    # Artifacts extracted to work_dirs/<scene_id>/ (v0 layout: flat zip)
    extracted = work_root / "work_dirs" / "scene-42"
    assert (extracted / "out" / "cover_001.jpg").read_bytes() == b"jpg"
    # Controller rewrites result["work_dir"] to its local extracted path so
    # the UI can find covers without round-tripping back to the pod.
    assert Path(result["work_dir"]) == extracted
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


def test_runpod_backend_bundle_layout_drops_decision_log_at_canonical_path(
    tmp_path, monkeypatch
):
    """When the pod ships an artifact bundle (work_dir/ + decision_log.json),
    the controller must extract work_dir to DATA_DIR/work_dirs/<scene>/ and
    copy the decision log to DATA_DIR/decision_logs/<safe>.json. Without
    the decision log on the controller, the scene won't show up in the
    library and the operator's review feedback gets dropped silently."""
    monkeypatch.setenv("AMG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AMG_RUNPOD_API_KEY", "rk_test")
    monkeypatch.setenv("AMG_RUNPOD_IMAGE", "ghcr.io/test/amg:latest")
    monkeypatch.setenv("AMG_POD_AUTH_TOKEN", "x" * 48)
    monkeypatch.setenv("AMG_JOB_POLL_INTERVAL_SEC", "0")

    import importlib
    import amg.config as cfg
    importlib.reload(cfg)
    import amg.cloud.runpod as runpod_mod
    importlib.reload(runpod_mod)
    import amg.cloud.job_backend as jb
    importlib.reload(jb)
    from amg.cloud.job_backend import RunpodBackend
    from amg.config import DECISION_LOGS_DIR

    work_root = tmp_path
    fake_client = _FakeRunpodClient()
    session = _FakeHttpSession()
    backend = RunpodBackend(
        client=fake_client,
        spec=runpod_mod.PodSpec.from_env(),
        http_session=session,
        work_dirs_root=work_root / "work_dirs",
        run_timeout_sec=60.0,
    )
    monkeypatch.setattr(jb.time, "sleep", lambda _s: None)

    final_result = {
        "success": True,
        "scene_id": "scene-bundle",
        "covers_saved": 8,
        "decision_log_path": "/data/decision_logs/scene-bundle.json",  # pod-side path
    }
    bundle_zip = _make_zip_bytes({
        "work_dir/covers/cover_001.jpg": b"cover-bytes",
        "work_dir/insight.json": b'{"i":1}',
        "decision_log.json": b'{"scene_id": "scene-bundle", "execution": {"phases": {}}}',
    })

    session.queue(
        _FakePodResponse(200, {"job_id": "j1", "status": "queued"}),
        _FakePodResponse(200, {
            "status": "done",
            "log_tail": ["done"],
            "progress_pct": 100,
            "result": final_result,
        }),
        _FakePodResponse(200, content=bundle_zip),
    )

    video_dir = tmp_path / "scene-bundle"
    video_dir.mkdir()
    video = video_dir / "v.mp4"
    video.write_bytes(b"v")
    result = backend.run_job(video)

    extracted = work_root / "work_dirs" / "scene-bundle"
    assert (extracted / "covers" / "cover_001.jpg").read_bytes() == b"cover-bytes"
    assert (extracted / "insight.json").read_bytes() == b'{"i":1}'
    # Decision log lands at DATA_DIR/decision_logs/<safe-id>.json so the
    # UI's _load_decision_log finds it.
    dlog_dest = DECISION_LOGS_DIR / "scene-bundle.json"
    assert dlog_dest.is_file(), f"decision log not landed at {dlog_dest}"
    assert "scene-bundle" in dlog_dest.read_text()
    # Result paths point to the controller's local extracted artifacts.
    assert Path(result["work_dir"]) == extracted
    assert Path(result["decision_log_path"]) == dlog_dest


def test_runpod_backend_waits_for_pod_worker_healthz(runpod_backend, tmp_path, monkeypatch):
    """The lifecycle should poll /healthz until 200 before submitting."""
    backend, _client, session, _ = runpod_backend
    import amg.cloud.job_backend as jb
    monkeypatch.setattr(jb.time, "sleep", lambda _s: None)

    # Disable the auto /healthz so we control the responses.
    session._auto_healthz_ok = False
    session.queue(
        _FakePodResponse(503, {}),                                     # /healthz - not ready
        _FakePodResponse(503, {}),                                     # /healthz - still not ready
        _FakePodResponse(200, {"status": "ok"}),                       # /healthz - ready!
        _FakePodResponse(200, {"job_ids": []}),                        # GET /jobs - app mounted
        _FakePodResponse(200, {"job_id": "j1", "status": "queued"}),   # POST /jobs
        _FakePodResponse(200, {"status": "done", "result": {"success": True, "scene_id": "s"},
                               "log_tail": [], "progress_pct": 100}),  # GET /jobs/j1
        _FakePodResponse(200, content=_make_zip_bytes({"out/x.txt": b"x"})),  # GET /jobs/j1/zip
    )

    video_dir = tmp_path / "s"
    video_dir.mkdir()
    video = video_dir / "v.mp4"
    video.write_bytes(b"v")
    backend.run_job(video)

    healthz_calls = [c for c in session.calls if c["url"].endswith("/healthz")]
    assert len(healthz_calls) == 3, f"expected 3 /healthz polls, got {len(healthz_calls)}: {healthz_calls}"


def test_runpod_backend_pod_worker_readiness_timeout(runpod_backend, tmp_path, monkeypatch):
    """If /healthz never returns 200, the lifecycle should fail with a clear message."""
    backend, client, session, _ = runpod_backend
    import amg.cloud.job_backend as jb
    monkeypatch.setattr(jb.time, "sleep", lambda _s: None)

    # 0-second timeout forces the loop to exit on the first iteration with no
    # successful /healthz seen.
    monkeypatch.setattr(
        backend,
        "_wait_for_pod_worker_ready",
        lambda pid, **kw: backend.__class__._wait_for_pod_worker_ready(
            backend, pid, on_log=kw.get("on_log", lambda _m: None), timeout_sec=0.0,
        ),
    )
    session._auto_healthz_ok = False
    # Queue a non-200 response so the (single) probe attempt sees it.
    session.queue(_FakePodResponse(503, {}))

    video_dir = tmp_path / "stuck"
    video_dir.mkdir()
    video = video_dir / "v.mp4"
    video.write_bytes(b"v")
    with pytest.raises(RuntimeError, match="did not become ready"):
        backend.run_job(video)
    # Pod must still be terminated even though we failed before submit.
    assert client.terminated == ["pod_test"], "pod should be terminated even on readiness timeout"


# --- cloud-source jobs ------------------------------------------------------


SAMPLE_RCLONE_CONFIG = (
    "[gdrive_amy]\n"
    "type = drive\n"
    "token = {\"access_token\":\"ya29.fake\"}\n"
)


@pytest.fixture
def populated_credential_store(tmp_path, monkeypatch):
    """A real CredentialStore in a per-test tmp dir, with a single
    'gdrive_amy' remote already added."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AMG_CREDENTIALS_DB", str(tmp_path / "creds.sqlite"))
    monkeypatch.setenv("AMG_CREDENTIALS_KEY", Fernet.generate_key().decode("ascii"))
    from amg.cloud.credentials import CredentialStore

    store = CredentialStore()
    store.add_remote(SAMPLE_RCLONE_CONFIG, notes="amy's drive")
    return store


class TestJobBackendInterface:
    def test_default_run_cloud_job_raises(self):
        from amg.cloud.job_backend import CloudSource, JobBackend

        class _MinimalBackend(JobBackend):
            name = "minimal"

            def run_job(self, video_path, *, on_log=None, on_progress=None):
                return {"success": True}

        with pytest.raises(NotImplementedError, match="cloud-source"):
            _MinimalBackend().run_cloud_job(
                CloudSource(remote="x", path="y")
            )


class TestLocalBackendCloud:
    def _stub_rclone(self, monkeypatch, *, drop_file_named: str = "scene4.mp4"):
        """Replace amg.cloud.rclone.Rclone with a stub that 'downloads' one
        fake file into the destination directory."""
        from amg.cloud import rclone as rclone_mod

        class _StubRclone:
            def __init__(self, *_a, **_kw):
                pass

            def copy(self, src, dst, *, on_progress=None, on_log=None, **_kw):
                Path(dst).mkdir(parents=True, exist_ok=True)
                (Path(dst) / drop_file_named).write_bytes(b"fake video bytes")
                if on_progress is not None:
                    on_progress({"done": "1 MiB", "total": "1 MiB", "pct": 100})
                if on_log is not None:
                    on_log("INFO  : copied 1 file")

        monkeypatch.setattr(rclone_mod, "Rclone", _StubRclone)

    def test_local_cloud_job_round_trip(
        self, populated_credential_store, monkeypatch, tmp_path
    ):
        # AMG_DATA_DIR controls where downloads land.
        monkeypatch.setenv("AMG_DATA_DIR", str(tmp_path / "data"))

        import importlib
        import amg.config as cfg
        importlib.reload(cfg)
        import amg.cloud.job_backend as jb_mod
        importlib.reload(jb_mod)

        self._stub_rclone(monkeypatch, drop_file_named="scene4.mp4")

        captured: Dict[str, Any] = {}

        def _fake_process_scene(video_path):
            captured["video_path"] = Path(video_path)
            return {"success": True, "scene_id": "scene4", "covers_saved": 9}

        import amg.pipeline as pipeline
        monkeypatch.setattr(pipeline, "process_scene", _fake_process_scene)

        from amg.cloud.job_backend import CloudSource, LocalBackend

        backend = LocalBackend()
        logs: List[str] = []
        progresses: List[int] = []
        result = backend.run_cloud_job(
            CloudSource(remote="gdrive_amy", path="incoming/scene4.mp4"),
            on_log=logs.append,
            on_progress=progresses.append,
        )

        assert result["success"] is True
        assert result["covers_saved"] == 9
        # process_scene saw the rclone-downloaded file, not the cloud path.
        assert captured["video_path"].name == "scene4.mp4"
        assert captured["video_path"].is_file()
        # Log surfaces both phases (rclone + pipeline).
        assert any("rclone copy" in line for line in logs)
        assert any("download complete" in line for line in logs)
        # Progress ends at 100 (rclone fills 0..50, pipeline jumps to 100).
        assert progresses[-1] == 100

    def test_local_cloud_job_rclone_not_installed(
        self, populated_credential_store, monkeypatch, tmp_path
    ):
        from amg.cloud import rclone as rclone_mod

        class _MissingRclone:
            def __init__(self, *_a, **_kw):
                pass

            def copy(self, *_a, **_kw):
                raise rclone_mod.RcloneNotFoundError("rclone not on PATH")

        monkeypatch.setattr(rclone_mod, "Rclone", _MissingRclone)
        monkeypatch.setenv("AMG_DATA_DIR", str(tmp_path / "data"))

        from amg.cloud.job_backend import CloudSource, LocalBackend

        with pytest.raises(RuntimeError, match="rclone installed"):
            LocalBackend().run_cloud_job(
                CloudSource(remote="gdrive_amy", path="scene.mp4")
            )

    def test_local_cloud_job_unknown_remote(
        self, populated_credential_store, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("AMG_DATA_DIR", str(tmp_path / "data"))

        from amg.cloud.credentials import CredentialNotFoundError
        from amg.cloud.job_backend import CloudSource, LocalBackend

        with pytest.raises(CredentialNotFoundError):
            LocalBackend().run_cloud_job(
                CloudSource(remote="never_added", path="scene.mp4")
            )


class TestRunpodBackendCloud:
    def test_runpod_cloud_job_full_happy_path(
        self, runpod_backend, populated_credential_store, monkeypatch, tmp_path
    ):
        backend, client, session, work_root = runpod_backend
        import amg.cloud.job_backend as jb
        monkeypatch.setattr(jb.time, "sleep", lambda _s: None)

        final_result = {"success": True, "scene_id": "scene-cloud", "covers_saved": 5}
        zip_bytes = _make_zip_bytes({"out/cover_001.jpg": b"jpg"})

        session.queue(
            _FakePodResponse(200, {"job_id": "j1", "status": "queued"}),  # POST /jobs/cloud
            _FakePodResponse(200, {                                       # GET /jobs/j1
                "status": "done",
                "log_tail": ["downloading...", "process_scene done"],
                "progress_pct": 100,
                "result": final_result,
            }),
            _FakePodResponse(200, content=zip_bytes),                     # GET /jobs/j1/zip
        )

        from amg.cloud.job_backend import CloudSource

        logs: List[str] = []
        result = backend.run_cloud_job(
            CloudSource(remote="gdrive_amy", path="incoming/scene4.mp4", scene_id="scene-cloud"),
            on_log=logs.append,
        )
        assert result["success"] is True
        assert result["scene_id"] == "scene-cloud"
        assert result["covers_saved"] == 5
        assert client.provisioned, "should have provisioned a pod"
        assert client.terminated == ["pod_test"], "should have terminated the pod"

        # Verify the cloud path hit POST /jobs/cloud (not /jobs).
        cloud_post = next(c for c in session.calls if c.get("method") == "POST")
        assert cloud_post["url"].endswith("/jobs/cloud")

        # Artifacts extracted under work_dirs/<scene_id>/.
        extracted = work_root / "work_dirs" / "scene-cloud"
        assert (extracted / "out" / "cover_001.jpg").read_bytes() == b"jpg"
        # Controller rewrote work_dir in the result so the UI finds covers
        # at the local extracted path.
        assert Path(result["work_dir"]) == extracted

    def test_runpod_cloud_job_falls_back_to_jobs_hyphen_on_404(
        self, runpod_backend, populated_credential_store, monkeypatch, tmp_path
    ):
        """Some Runpod proxy edge cases return 404 on POST /jobs/cloud even
        though the pod-worker is healthy; /jobs-cloud is a one-segment alias."""
        backend, client, session, work_root = runpod_backend
        import amg.cloud.job_backend as jb
        monkeypatch.setattr(jb.time, "sleep", lambda _s: None)

        final_result = {"success": True, "scene_id": "scene-hy", "covers_saved": 3}
        zip_bytes = _make_zip_bytes({"out/x.jpg": b"x"})

        session.queue(
            _FakePodResponse(404, {"detail": "not found"}),
            _FakePodResponse(200, {"job_id": "j1", "status": "queued"}),
            _FakePodResponse(200, {
                "status": "done",
                "log_tail": [],
                "progress_pct": 100,
                "result": final_result,
            }),
            _FakePodResponse(200, content=zip_bytes),
        )

        from amg.cloud.job_backend import CloudSource

        result = backend.run_cloud_job(
            CloudSource(remote="gdrive_amy", path="incoming/x.mp4", scene_id="scene-hy"),
        )
        assert result["covers_saved"] == 3
        posts = [c for c in session.calls if c.get("method") == "POST"]
        assert len(posts) == 2
        assert posts[0]["url"].endswith("/jobs/cloud")
        assert posts[1]["url"].endswith("/jobs-cloud")

        extracted = work_root / "work_dirs" / "scene-hy"
        assert (extracted / "out" / "x.jpg").read_bytes() == b"x"

    def test_runpod_cloud_job_unknown_remote_skips_provisioning(
        self, runpod_backend, populated_credential_store, monkeypatch
    ):
        """Credential lookup happens BEFORE provision_pod so a misconfig
        doesn't burn a single second of GPU time."""
        backend, client, _session, _ = runpod_backend
        import amg.cloud.job_backend as jb
        monkeypatch.setattr(jb.time, "sleep", lambda _s: None)

        from amg.cloud.credentials import CredentialNotFoundError
        from amg.cloud.job_backend import CloudSource

        with pytest.raises(CredentialNotFoundError):
            backend.run_cloud_job(
                CloudSource(remote="never_added", path="scene.mp4")
            )
        assert client.provisioned == [], \
            "controller must NOT provision a pod when the credential lookup fails"
        assert client.terminated == [], \
            "no pod was provisioned, so nothing should be terminated"

    def test_runpod_cloud_job_pod_rejects_submit(
        self, runpod_backend, populated_credential_store, monkeypatch
    ):
        backend, client, session, _ = runpod_backend
        import amg.cloud.job_backend as jb
        monkeypatch.setattr(jb.time, "sleep", lambda _s: None)

        session.queue(_FakePodResponse(400, {"detail": "bad rclone_config"}))

        from amg.cloud.job_backend import CloudSource

        with pytest.raises(RuntimeError, match="rejected cloud submit"):
            backend.run_cloud_job(
                CloudSource(remote="gdrive_amy", path="scene.mp4")
            )
        # Pod was provisioned then terminated (cleanup runs even on submit error).
        assert client.terminated == ["pod_test"]

    def test_runpod_cloud_job_decrypts_and_forwards_credential(
        self, runpod_backend, populated_credential_store, monkeypatch
    ):
        """The decrypted rclone config must hit the pod's request body —
        the pod can't run rclone without it."""
        backend, _client, session, _ = runpod_backend
        import amg.cloud.job_backend as jb
        monkeypatch.setattr(jb.time, "sleep", lambda _s: None)

        # Patch the fake session's POST to capture the JSON body, since the
        # default _FakeHttpSession.post() doesn't store kwargs beyond data/files.
        captured_body: Dict[str, Any] = {}
        original_post = session.post

        def _capturing_post(url, *, headers=None, files=None, data=None,
                            json=None, timeout=None):
            captured_body["json"] = json
            captured_body["headers"] = headers
            return original_post(url, headers=headers, files=files, data=data,
                                 timeout=timeout)

        session.post = _capturing_post

        session.queue(
            _FakePodResponse(200, {"job_id": "j1", "status": "queued"}),
            _FakePodResponse(200, {
                "status": "done",
                "log_tail": [],
                "progress_pct": 100,
                "result": {"success": True, "scene_id": "s", "covers_saved": 1},
            }),
            _FakePodResponse(200, content=_make_zip_bytes({"out/x.jpg": b"x"})),
        )

        from amg.cloud.job_backend import CloudSource

        backend.run_cloud_job(
            CloudSource(remote="gdrive_amy", path="incoming/scene4.mp4")
        )

        assert captured_body.get("json") is not None
        body = captured_body["json"]
        assert body["remote"] == "gdrive_amy"
        assert body["path"] == "incoming/scene4.mp4"
        # The decrypted rclone_config is in the body — without this the pod
        # can't run rclone, so this is the test that catches "I forgot to
        # call get_remote".
        assert "[gdrive_amy]" in body["rclone_config"]
        assert "ya29.fake" in body["rclone_config"]
        # Bearer token present.
        assert captured_body["headers"]["Authorization"].startswith("Bearer ")
