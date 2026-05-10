"""Tests for amg.cloud.job_backend — pluggable job runner."""
from __future__ import annotations

import io
import json
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
        self.calls.append({
            "method": "GET",
            "url": url,
            "stream": stream,
            "headers": dict(headers) if headers else {},
        })
        # /healthz is the TCP/proxy readiness probe. /jobs proves FastAPI/auth
        # are mounted, and /readyz proves Ollama/model readiness. Auto-respond
        # so queue-based tests only model POST/poll/zip traffic.
        path = urlparse(url).path.rstrip("/")
        if self._auto_healthz_ok and path == "/healthz":
            return _FakePodResponse(200, {"status": "ok"})
        if self._auto_healthz_ok:
            if path == "/jobs":
                return _FakePodResponse(200, {"job_ids": []})
            if path == "/readyz":
                return _FakePodResponse(200, {
                    "ok": True,
                    "vision_model": "qwen2.5vl:7b",
                    "ai_parallel_workers": "6",
                })
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
    # Keep classic per-job teardown semantics for these tests unless a test
    # explicitly sets a warm-pod timeout.
    monkeypatch.setenv("AMG_RUNPOD_IDLE_TERMINATE_SEC", "0")
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
    assert client.provisioned[0].env.get("OLLAMA_NUM_PARALLEL") == "6"
    assert client.provisioned[0].env.get("AMG_AI_PARALLEL_WORKERS") == "6"
    assert client.provisioned[0].env.get("AMG_VIDEO_BACKEND") == "ffmpeg_cuda"
    assert client.provisioned[0].env.get("AMG_VIDEO_HWACCEL") == "cuda"
    assert client.provisioned[0].env.get("AMG_GPU_CV_ENABLED") == "1"
    assert client.provisioned[0].env.get("AMG_GPU_CV_BACKEND") == "opencv_cuda"
    assert client.provisioned[0].env.get("AMG_GPU_DEDUP_ENABLED") == "1"
    assert client.provisioned[0].env.get("AMG_PROCESSING_PROFILE") == "balanced"
    assert client.provisioned[0].env.get("AMG_STREAMING_SCAN") == "1"
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


def test_runpod_backend_forwards_processing_profile_env(runpod_backend, tmp_path, monkeypatch):
    backend, client, session, _work_root = runpod_backend
    import amg.cloud.job_backend as jb
    monkeypatch.setattr(jb.time, "sleep", lambda _s: None)
    monkeypatch.setenv("AMG_PROCESSING_PROFILE", "fast")
    monkeypatch.setenv("AMG_CALIBRATION_SAMPLE_COUNT", "20")
    monkeypatch.setenv("AMG_CALIBRATION_MAX_DURATION_SEC", "480")
    monkeypatch.setenv("AMG_TIER_SCAN_MODE", "single_pass")
    monkeypatch.setenv("AMG_SINGLE_PASS_MAX_AI_FRAMES", "24")
    monkeypatch.setenv("AMG_ENABLE_CLUSTER_EXPANSION", "0")
    monkeypatch.setenv("AMG_VISION_MODEL_OVERRIDE", "qwen2.5vl:3b")

    session.queue(
        _FakePodResponse(200, {"job_id": "j1", "status": "queued"}),
        _FakePodResponse(200, {"status": "done", "progress_pct": 100, "result": {"success": True, "scene_id": "s", "covers_saved": 1}}),
        _FakePodResponse(200, content=_make_zip_bytes({"decision_log.json": b"{}"})),
    )
    video = tmp_path / "s.mp4"
    video.write_bytes(b"video")

    backend.run_job(video, on_log=lambda _: None, on_progress=lambda _: None)
    env = client.provisioned[0].env
    assert env["AMG_PROCESSING_PROFILE"] == "fast"
    assert env["AMG_CALIBRATION_SAMPLE_COUNT"] == "20"
    assert env["AMG_CALIBRATION_MAX_DURATION_SEC"] == "480"
    assert env["AMG_TIER_SCAN_MODE"] == "single_pass"
    assert env["AMG_SINGLE_PASS_MAX_AI_FRAMES"] == "24"
    assert env["AMG_ENABLE_CLUSTER_EXPANSION"] == "0"
    assert env["AMG_VISION_MODEL_OVERRIDE"] == "qwen2.5vl:3b"


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


def test_runpod_backend_tolerates_transient_404_while_polling(runpod_backend, tmp_path, monkeypatch):
    """A short burst of 404s on GET /jobs/{id} should retry, not fail."""
    backend, client, session, work_root = runpod_backend
    import amg.cloud.job_backend as jb
    monkeypatch.setattr(jb.time, "sleep", lambda _s: None)

    session.queue(
        _FakePodResponse(200, {"job_id": "j1", "status": "queued"}),       # POST /jobs
        _FakePodResponse(404, {"detail": "unknown job"}),                   # transient poll miss
        _FakePodResponse(404, {"detail": "unknown job"}),                   # transient poll miss
        _FakePodResponse(200, {                                             # poll eventually succeeds
            "status": "done",
            "log_tail": [],
            "progress_pct": 100,
            "result": {"success": True, "scene_id": "scene-404", "covers_saved": 2},
        }),
        _FakePodResponse(200, content=_make_zip_bytes({"out/x.jpg": b"x"})),  # zip
    )

    video_dir = tmp_path / "scene-404"
    video_dir.mkdir()
    video = video_dir / "v.mp4"
    video.write_bytes(b"x")

    result = backend.run_job(video)
    assert result["success"] is True
    assert result["scene_id"] == "scene-404"
    assert client.terminated == ["pod_test"]
    extracted = work_root / "work_dirs" / "scene-404"
    assert (extracted / "out" / "x.jpg").read_bytes() == b"x"


def test_runpod_backend_reuses_warm_pod_across_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("AMG_RUNPOD_API_KEY", "rk_test")
    monkeypatch.setenv("AMG_RUNPOD_IMAGE", "ghcr.io/test/amg:latest")
    monkeypatch.setenv("AMG_POD_AUTH_TOKEN", "x" * 48)
    monkeypatch.setenv("AMG_JOB_POLL_INTERVAL_SEC", "0")

    import importlib
    import amg.cloud.runpod as runpod_mod
    importlib.reload(runpod_mod)
    import amg.cloud.job_backend as jb_mod
    importlib.reload(jb_mod)
    from amg.cloud.job_backend import RunpodBackend

    fake_client = _FakeRunpodClient()
    session = _FakeHttpSession()
    backend = RunpodBackend(
        client=fake_client,
        spec=runpod_mod.PodSpec.from_env(),
        http_session=session,
        work_dirs_root=tmp_path / "work_dirs",
        run_timeout_sec=60.0,
        idle_terminate_sec=120.0,
    )
    monkeypatch.setattr(jb_mod.time, "sleep", lambda _s: None)

    session.queue(
        _FakePodResponse(200, {"job_id": "j1", "status": "queued"}),
        _FakePodResponse(200, {
            "status": "done",
            "log_tail": [],
            "progress_pct": 100,
            "result": {"success": True, "scene_id": "scene-a", "covers_saved": 2},
        }),
        _FakePodResponse(200, content=_make_zip_bytes({"out/a.jpg": b"a"})),
        _FakePodResponse(200, {"job_id": "j2", "status": "queued"}),
        _FakePodResponse(200, {
            "status": "done",
            "log_tail": [],
            "progress_pct": 100,
            "result": {"success": True, "scene_id": "scene-b", "covers_saved": 3},
        }),
        _FakePodResponse(200, content=_make_zip_bytes({"out/b.jpg": b"b"})),
    )

    scene_a = tmp_path / "scene-a"
    scene_a.mkdir()
    v1 = scene_a / "a.mp4"
    v1.write_bytes(b"a")
    scene_b = tmp_path / "scene-b"
    scene_b.mkdir()
    v2 = scene_b / "b.mp4"
    v2.write_bytes(b"b")

    r1 = backend.run_job(v1)
    r2 = backend.run_job(v2)
    assert r1["success"] is True
    assert r2["success"] is True
    assert len(fake_client.provisioned) == 1, "second job should reuse warm pod"
    # Idle timer has not fired in test; terminate happens on shutdown.
    assert fake_client.terminated == []
    backend.shutdown()
    assert fake_client.terminated == ["pod_test"]


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
    pod_work_dir = "/data/pod_uploads/j1/scene-bundle/video_amg_v11"
    bundle_zip = _make_zip_bytes({
        "work_dir/covers/cover_001.jpg": b"cover-bytes",
        "work_dir/insight.json": b'{"i":1}',
        "work_dir/scene_analysis.json": json.dumps({
            "preview_outputs": [
                {"path": f"{pod_work_dir}/previews/preview_01.mp4"}
            ],
            "evidence": [
                {"frame_path": f"{pod_work_dir}/covers/cover_001.jpg"}
            ],
        }).encode(),
        "work_dir/previews/preview_manifest.json": json.dumps({
            "outputs": [
                {"path": f"{pod_work_dir}/previews/preview_01.mp4"}
            ]
        }).encode(),
        "decision_log.json": json.dumps({
            "scene_id": "scene-bundle",
            "execution": {"phases": {}},
            "analysis_path": f"{pod_work_dir}/scene_analysis.json",
            "outcomes": {
                "saved_covers": [
                    {"path": f"{pod_work_dir}/covers/cover_001.jpg"}
                ]
            },
        }).encode(),
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
    dlog = json.loads(dlog_dest.read_text())
    assert dlog["analysis_path"] == str(extracted / "scene_analysis.json")
    assert dlog["outcomes"]["saved_covers"][0]["path"] == str(extracted / "covers" / "cover_001.jpg")
    analysis = json.loads((extracted / "scene_analysis.json").read_text())
    assert analysis["preview_outputs"][0]["path"] == str(extracted / "previews" / "preview_01.mp4")
    assert analysis["evidence"][0]["frame_path"] == str(extracted / "covers" / "cover_001.jpg")
    manifest = json.loads((extracted / "previews" / "preview_manifest.json").read_text())
    assert manifest["outputs"][0]["path"] == str(extracted / "previews" / "preview_01.mp4")
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
        _FakePodResponse(200, {"ok": True, "vision_model": "qwen2.5vl:7b",
                               "ai_parallel_workers": "6"}),           # GET /readyz - model ready
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

    healthz_calls = [c for c in session.calls if urlparse(c["url"]).path.endswith("/healthz")]
    assert len(healthz_calls) == 3, f"expected 3 /healthz polls, got {len(healthz_calls)}: {healthz_calls}"
    readyz_calls = [c for c in session.calls if urlparse(c["url"]).path.endswith("/readyz")]
    assert len(readyz_calls) == 1
    assert all("attempt=" in c["url"] for c in healthz_calls + readyz_calls)


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


def test_runpod_backend_requires_model_readyz(runpod_backend, monkeypatch):
    """A mounted pod-worker is not ready until /readyz proves the model exists."""
    backend, _client, session, _ = runpod_backend
    import amg.cloud.job_backend as jb

    session._auto_healthz_ok = False
    session.queue(
        _FakePodResponse(200, {"status": "ok"}),          # /healthz
        _FakePodResponse(200, {"job_ids": []}),           # /jobs
        _FakePodResponse(503, {"detail": {"model_ok": False}}),  # /readyz
    )
    ticks = iter([0.0, 0.0, 31.0, 31.0])
    monkeypatch.setattr(jb.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(jb.time, "sleep", lambda _s: None)

    with pytest.raises(RuntimeError, match="did not become ready"):
        backend._wait_for_pod_worker_ready(
            "pod_test",
            on_log=lambda _m: None,
            timeout_sec=0.01,
            poll_interval_sec=0,
        )

    assert any(urlparse(c["url"]).path.endswith("/readyz") for c in session.calls)


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
        assert urlparse(cloud_post["url"]).path == "/jobs/cloud"
        assert "cycle=" in cloud_post["url"]

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
        assert urlparse(posts[0]["url"]).path == "/jobs/cloud"
        assert urlparse(posts[1]["url"]).path == "/jobs-cloud"

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
        # No-cache headers are critical: Runpod/Cloudflare can otherwise serve
        # stale 404s from the pod's boot window even after /readyz is green.
        assert captured_body["headers"]["Cache-Control"] == "no-cache, no-store, max-age=0"
        assert captured_body["headers"]["Pragma"] == "no-cache"

    def test_runpod_cloud_job_retries_through_cached_404(
        self, runpod_backend, populated_credential_store, monkeypatch
    ):
        """Stale-cached 404s on BOTH /jobs/cloud and /jobs-cloud should
        recover via cache-busting retries, not crash the controller."""
        backend, _client, session, _ = runpod_backend
        import amg.cloud.job_backend as jb
        monkeypatch.setattr(jb.time, "sleep", lambda _s: None)

        session.queue(
            _FakePodResponse(404, {"detail": "not found"}),  # cycle 1: /jobs/cloud
            _FakePodResponse(404, {"detail": "not found"}),  # cycle 1: /jobs-cloud
            _FakePodResponse(404, {"detail": "not found"}),  # cycle 2: /jobs/cloud
            _FakePodResponse(200, {"job_id": "j1", "status": "queued"}),  # cycle 2: /jobs-cloud
            _FakePodResponse(200, {
                "status": "done",
                "log_tail": [],
                "progress_pct": 100,
                "result": {"success": True, "scene_id": "scene-r", "covers_saved": 2},
            }),
            _FakePodResponse(200, content=_make_zip_bytes({"out/x.jpg": b"x"})),
        )

        from amg.cloud.job_backend import CloudSource

        result = backend.run_cloud_job(
            CloudSource(remote="gdrive_amy", path="x.mp4", scene_id="scene-r")
        )
        assert result["covers_saved"] == 2

        posts = [c for c in session.calls if c.get("method") == "POST"]
        assert len(posts) == 4
        # Cycle 1 + 2 each tried both routes, then succeeded on /jobs-cloud
        # cycle 2.
        assert urlparse(posts[0]["url"]).path == "/jobs/cloud"
        assert urlparse(posts[1]["url"]).path == "/jobs-cloud"
        assert urlparse(posts[2]["url"]).path == "/jobs/cloud"
        assert urlparse(posts[3]["url"]).path == "/jobs-cloud"
        # Each retry cycle uses a different cache-buster so the proxy is forced
        # past any previously cached 404 response. The path itself
        # differentiates /jobs/cloud vs /jobs-cloud within a cycle.
        cycles = {c["url"].split("?", 1)[1].split("&", 1)[0] for c in posts}
        assert cycles == {"cycle=1", "cycle=2"}

    def test_runpod_cloud_job_polls_with_cache_busters_and_no_cache_headers(
        self, runpod_backend, populated_credential_store, monkeypatch
    ):
        """``_wait_for_job`` and ``_download_and_extract`` MUST cache-bust
        every request. Without this, the Runpod proxy (Cloudflare) caches
        the very first ``/jobs/{id}`` response (often
        ``status=running`` / ``status=downloading`` taken seconds after
        submit) and serves it for the rest of the run, so the controller
        never sees ``status=done`` and the dispatcher thread blocks
        indefinitely. The on-the-wire smoking gun is
        ``cf-cache-status: HIT`` with a stale ``age:`` value — see the
        2026-05-08 incident where Y&B_003 showed ``age: 2786`` (46 min)
        for every poll while the pod-side job had been done for 16 min.
        """
        backend, _client, session, _ = runpod_backend
        import amg.cloud.job_backend as jb
        monkeypatch.setattr(jb.time, "sleep", lambda _s: None)

        session.queue(
            _FakePodResponse(200, {"job_id": "j1", "status": "queued"}),  # POST /jobs/cloud
            _FakePodResponse(200, {                                        # GET /jobs/j1
                "status": "done",
                "log_tail": [],
                "progress_pct": 100,
                "result": {"success": True, "scene_id": "scene-cb", "covers_saved": 1},
            }),
            _FakePodResponse(200, content=_make_zip_bytes({"out/x.jpg": b"x"})),
        )

        from amg.cloud.job_backend import CloudSource

        backend.run_cloud_job(
            CloudSource(remote="gdrive_amy", path="x.mp4", scene_id="scene-cb")
        )

        gets = [c for c in session.calls if c.get("method") == "GET"]
        # Expect at least the poll + the zip pull.
        poll_calls = [c for c in gets if urlparse(c["url"]).path == "/jobs/j1"]
        zip_calls = [c for c in gets if urlparse(c["url"]).path == "/jobs/j1/zip"]
        assert poll_calls, "expected at least one /jobs/{id} poll"
        assert zip_calls, "expected exactly one /jobs/{id}/zip download"

        for call in poll_calls + zip_calls:
            url = call["url"]
            headers = call.get("headers") or {}
            assert "?t=" in url or "&t=" in url, (
                f"poll/zip URL must include a per-request cache-buster query "
                f"to defeat Cloudflare proxy caching; got {url!r}"
            )
            assert headers.get("Cache-Control") == "no-cache, no-store, max-age=0", (
                f"poll/zip request missing no-cache Cache-Control header; got "
                f"{headers!r}"
            )
            assert headers.get("Pragma") == "no-cache", (
                f"poll/zip request missing Pragma: no-cache header; got {headers!r}"
            )

    def test_runpod_cloud_job_no_controller_rclone_fallback_by_default(
        self, runpod_backend, populated_credential_store, monkeypatch
    ):
        """A persistently-404 cloud submit must NOT silently trigger a
        controller-side rclone download — the 4 GB Hetzner CX21 cannot
        stage multi-GB scenes without OOM-killing the controller."""
        backend, client, session, _ = runpod_backend
        import amg.cloud.job_backend as jb
        monkeypatch.setattr(jb.time, "sleep", lambda _s: None)

        # 4 cycles × 2 routes = 8 forced 404s.
        session.queue(*[_FakePodResponse(404, {}) for _ in range(8)])

        called = {"download": False}

        def _explode_if_called(*_args, **_kwargs):
            called["download"] = True
            raise AssertionError(
                "controller-side rclone fallback must be opt-in via "
                "AMG_RUNPOD_ALLOW_CONTROLLER_RCLONE_FALLBACK; running it on a "
                "small controller VM has historically OOM-killed the service."
            )

        monkeypatch.setattr(
            backend, "_download_cloud_source_for_upload", _explode_if_called
        )

        from amg.cloud.job_backend import CloudSource

        with pytest.raises(RuntimeError, match="no cloud submit route"):
            backend.run_cloud_job(
                CloudSource(remote="gdrive_amy", path="big_scene.mp4")
            )
        assert called["download"] is False
        # Pod still terminated despite the failure.
        assert client.terminated == ["pod_test"]
