"""Tests for amg.cloud.pod_worker — pod-side FastAPI service."""
from __future__ import annotations

import io
import secrets
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def pod_env(tmp_path, monkeypatch):
    """Per-test data dir + a real auth token. Stub the pipeline so we don't
    actually try to hit Ollama from CI."""
    monkeypatch.setenv("AMG_DATA_DIR", str(tmp_path / "data"))
    token = secrets.token_urlsafe(48)
    monkeypatch.setenv("AMG_POD_AUTH_TOKEN", token)

    import importlib
    import amg.config as cfg
    importlib.reload(cfg)
    import amg.cloud.pod_worker as pw
    importlib.reload(pw)

    return {"token": token, "module": pw}


@pytest.fixture
def authed_client(pod_env, monkeypatch):
    """A TestClient with the bearer token preset on every request, plus a
    stubbed process_scene that finishes immediately so the runner thread
    completes deterministically."""
    pw = pod_env["module"]
    token = pod_env["token"]

    fake_result = {"success": True, "covers_saved": 7, "scene_id": "stub"}

    def _fake_process_scene(video_path, **_kwargs):
        return fake_result

    # Stubbing via the runner thread's import path. The runner does
    # ``from amg.pipeline import process_scene`` inside the function, so we
    # patch the module's attribute that's read at call time.
    import amg.pipeline as pipeline
    monkeypatch.setattr(pipeline, "process_scene", _fake_process_scene)

    app = pw.create_app(auth_token=token)
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {token}"
    return client, pw


def _wait_for_status(client, job_id, target, *, timeout=3.0):
    """Poll /jobs/{id} until status == target (or timeout). Helps the test
    block until the background runner thread finishes without sprinkling
    sleeps everywhere."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/jobs/{job_id}")
        if r.status_code == 200 and r.json().get("status") == target:
            return r.json()
        time.sleep(0.02)
    return None


# --- startup / config -------------------------------------------------------


class TestStartup:
    def test_missing_token_refuses_to_start(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AMG_POD_AUTH_TOKEN", raising=False)
        monkeypatch.setenv("AMG_DATA_DIR", str(tmp_path / "data"))
        import importlib
        import amg.cloud.pod_worker as pw
        importlib.reload(pw)
        with pytest.raises(RuntimeError, match="AMG_POD_AUTH_TOKEN"):
            pw.create_app()

    def test_short_token_refuses_to_start(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AMG_POD_AUTH_TOKEN", "tooshort")
        monkeypatch.setenv("AMG_DATA_DIR", str(tmp_path / "data"))
        import importlib
        import amg.cloud.pod_worker as pw
        importlib.reload(pw)
        with pytest.raises(RuntimeError, match="too short"):
            pw.create_app()


# --- auth -------------------------------------------------------------------


class TestAuth:
    def test_healthz_is_unauthenticated(self, pod_env):
        pw = pod_env["module"]
        app = pw.create_app(auth_token=pod_env["token"])
        client = TestClient(app)
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_other_routes_require_token(self, pod_env):
        pw = pod_env["module"]
        app = pw.create_app(auth_token=pod_env["token"])
        client = TestClient(app)
        r = client.get("/jobs")
        assert r.status_code == 401
        assert "missing bearer token" in r.json()["error"]

    def test_wrong_token_rejected(self, pod_env):
        pw = pod_env["module"]
        app = pw.create_app(auth_token=pod_env["token"])
        client = TestClient(app)
        r = client.get("/jobs", headers={"Authorization": "Bearer not-the-right-token"})
        assert r.status_code == 401
        assert "invalid token" in r.json()["error"]

    def test_correct_token_accepted(self, pod_env):
        pw = pod_env["module"]
        app = pw.create_app(auth_token=pod_env["token"])
        client = TestClient(app)
        r = client.get(
            "/jobs",
            headers={"Authorization": f"Bearer {pod_env['token']}"},
        )
        assert r.status_code == 200

    def test_readyz_waits_for_real_vision_warmup(self, pod_env, monkeypatch):
        pw = pod_env["module"]
        monkeypatch.setenv("AMG_POD_READY_WARMUP_ENABLED", "1")

        import amg.scoring.ai_client as ai_client

        class FakeAIClient:
            vision_model = "qwen2.5vl:7b"

            def is_alive(self):
                return True

            def is_model_loaded(self):
                return True

            def warm_vision_model(self, timeout_sec=180):
                return SimpleNamespace(
                    success=True,
                    duration_sec=0.01,
                    error_code=None,
                    error_message=None,
                )

        monkeypatch.setattr(ai_client, "AIClient", FakeAIClient)
        app = pw.create_app(auth_token=pod_env["token"])
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {pod_env['token']}"}

        ready = None
        for _ in range(20):
            r = client.get("/readyz", headers=headers)
            if r.status_code == 200:
                ready = r.json()
                break
            time.sleep(0.02)

        assert ready is not None
        assert ready["ok"] is True
        assert ready["warmup_enabled"] is True
        assert ready["warmup_ok"] is True
        assert ready["warmup_state"] == "done"


# --- jobs -------------------------------------------------------------------


class TestJobs:
    def test_create_job_starts_pipeline_and_completes(self, authed_client):
        client, _pw = authed_client
        files = {"video": ("scene.mp4", b"fake mp4 bytes")}
        r = client.post("/jobs", files=files, data={"scene_id": "scene-1"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "queued"
        assert body["bytes_received"] == len(b"fake mp4 bytes")
        job_id = body["job_id"]

        final = _wait_for_status(client, job_id, "done")
        assert final is not None, "job did not reach 'done'"
        assert final["result"]["covers_saved"] == 7
        assert final["progress_pct"] == 100
        assert any("starting process_scene" in line for line in final["log_tail"])

    def test_pipeline_failure_marks_job_error(self, pod_env, monkeypatch):
        pw = pod_env["module"]
        app = pw.create_app(auth_token=pod_env["token"])
        client = TestClient(app)
        client.headers["Authorization"] = f"Bearer {pod_env['token']}"

        def _explode(_video_path, **_kwargs):
            raise RuntimeError("ollama unreachable")

        import amg.pipeline as pipeline
        monkeypatch.setattr(pipeline, "process_scene", _explode)

        r = client.post(
            "/jobs",
            files={"video": ("scene.mp4", b"fake")},
            data={"scene_id": "scene-1"},
        )
        job_id = r.json()["job_id"]
        final = _wait_for_status(client, job_id, "error")
        assert final is not None, "job did not reach 'error'"
        assert "ollama unreachable" in final["error"]
        assert any("pipeline raised" in line for line in final["log_tail"])

    def test_get_unknown_job_404(self, authed_client):
        client, _ = authed_client
        r = client.get("/jobs/does-not-exist")
        assert r.status_code == 404

    def test_list_jobs(self, authed_client):
        client, _ = authed_client
        ids = []
        for i in range(3):
            r = client.post(
                "/jobs",
                files={"video": (f"s{i}.mp4", b"x")},
                data={"scene_id": f"scene-{i}"},
            )
            ids.append(r.json()["job_id"])
            _wait_for_status(client, r.json()["job_id"], "done")
        listed = client.get("/jobs").json()["job_ids"]
        for jid in ids:
            assert jid in listed

    def test_create_job_without_filename_400(self, authed_client):
        client, _ = authed_client
        # Empty filename on the upload — FastAPI accepts this case but we
        # surface 400 explicitly.
        files = {"video": ("", b"x")}
        r = client.post("/jobs", files=files, data={"scene_id": "x"})
        # Depending on multipart impl, the absent filename might short-circuit
        # before our handler runs (422). Both 400 and 422 are acceptable here —
        # the point is the request is rejected.
        assert r.status_code in (400, 422)

    def test_work_dir_repointed_to_pipeline_output_after_success(
        self, pod_env, monkeypatch, tmp_path
    ):
        """Regression: zipping the entire download dir (which holds the
        multi-GB source video) makes /jobs/{id}/zip block its streaming
        response by minutes while zipfile builds the archive into BytesIO.

        After process_scene returns, the tracker's work_dir must be
        narrowed to result["work_dir"] (the small out/ folder containing
        only covers + contact sheet + decision log)."""
        pw = pod_env["module"]
        app = pw.create_app(auth_token=pod_env["token"])
        client = TestClient(app)
        client.headers["Authorization"] = f"Bearer {pod_env['token']}"

        # Stand up a fake "pipeline output dir" that the pipeline result
        # will point to. It must exist on disk for the work_dir update
        # to take effect (the pod-worker validates is_dir()).
        pipeline_out = tmp_path / "fake_out" / "scene_amg_v11"
        pipeline_out.mkdir(parents=True)
        (pipeline_out / "cover_001.jpg").write_bytes(b"x")

        def _fake_process_scene(_video_path, **_kwargs):
            return {
                "success": True,
                "covers_saved": 12,
                "scene_id": "stub",
                "work_dir": str(pipeline_out),
            }

        import amg.pipeline as pipeline
        monkeypatch.setattr(pipeline, "process_scene", _fake_process_scene)

        r = client.post(
            "/jobs",
            files={"video": ("muvie.mp4", b"x")},
            data={"scene_id": "muvie"},
        )
        job_id = r.json()["job_id"]
        final = _wait_for_status(client, job_id, "done")
        assert final is not None
        assert final["work_dir"] == str(pipeline_out), (
            "tracker.work_dir must be re-pointed at the pipeline's output "
            "dir; otherwise /jobs/{id}/zip will try to zip the entire "
            "download folder including the multi-GB source video"
        )

    def test_work_dir_unchanged_if_pipeline_omits_work_dir(
        self, pod_env, monkeypatch
    ):
        """If the pipeline result lacks a work_dir field, leave the tracker
        as-is. Better to ship a slightly larger zip than to crash."""
        pw = pod_env["module"]
        app = pw.create_app(auth_token=pod_env["token"])
        client = TestClient(app)
        client.headers["Authorization"] = f"Bearer {pod_env['token']}"

        def _fake_process_scene(_video_path, **_kwargs):
            return {"success": True, "covers_saved": 3}  # no work_dir

        import amg.pipeline as pipeline
        monkeypatch.setattr(pipeline, "process_scene", _fake_process_scene)

        r = client.post(
            "/jobs",
            files={"video": ("scene.mp4", b"x")},
            data={"scene_id": "scene"},
        )
        job_id = r.json()["job_id"]
        final = _wait_for_status(client, job_id, "done")
        assert final is not None
        assert "scene" in final["work_dir"]  # original download dir kept


class TestObservability:
    def test_capacity_endpoint_reports_queue_and_running(self, pod_env, tmp_path):
        pw = pod_env["module"]
        tracker = pw._JobTracker()
        tracker.create(
            "queued1",
            scene_id="queued",
            video_path=tmp_path / "queued.mp4",
            work_dir=tmp_path,
        )
        tracker.create(
            "running1",
            scene_id="running",
            video_path=tmp_path / "running.mp4",
            work_dir=tmp_path,
        )
        tracker.update("running1", status="running")

        app = pw.create_app(auth_token=pod_env["token"], tracker=tracker)
        client = TestClient(app)
        client.headers["Authorization"] = f"Bearer {pod_env['token']}"

        r = client.get("/used-system-capacity")
        assert r.status_code == 200
        body = r.json()
        assert body["running_jobs"] == 1
        assert body["queued_jobs"] == 1
        assert body["max_active_jobs"] >= 1
        assert 0 <= body["used_system_capacity"] <= 1

    def test_latency_endpoint_uses_completed_video_jobs(self, pod_env, tmp_path):
        pw = pod_env["module"]
        tracker = pw._JobTracker()
        tracker.create(
            "done1",
            scene_id="scene",
            video_path=tmp_path / "scene.mp4",
            work_dir=tmp_path,
        )
        tracker.update(
            "done1",
            status="done",
            pipeline_started_at_ts=100.0,
            pipeline_finished_at_ts=110.0,
            result={"success": True, "source_duration_sec": 50.0},
        )

        app = pw.create_app(auth_token=pod_env["token"], tracker=tracker)
        client = TestClient(app)
        client.headers["Authorization"] = f"Bearer {pod_env['token']}"

        r = client.get("/latency?media_type=video")
        assert r.status_code == 200
        body = r.json()
        assert body["unit"] == "video_second"
        assert body["latency_ms_per_unit"] == 200.0
        assert body["sample_count"] == 1


# --- /jobs/{id}/zip ---------------------------------------------------------


class TestJobZip:
    def test_zip_streams_work_dir_contents_under_work_dir_prefix(self, authed_client, tmp_path):
        """Pod ships an artifact bundle: ``work_dir/<files>`` plus an
        optional top-level ``decision_log.json``. The controller's
        extractor unpacks ``work_dir/...`` to its canonical local path
        and copies the decision log to ``DATA_DIR/decision_logs/``."""
        client, pw = authed_client
        r = client.post(
            "/jobs",
            files={"video": ("scene.mp4", b"x")},
            data={"scene_id": "scene-1"},
        )
        job_id = r.json()["job_id"]
        _wait_for_status(client, job_id, "done")

        work_dir = Path(client.get(f"/jobs/{job_id}").json()["work_dir"])
        (work_dir / "out").mkdir(parents=True, exist_ok=True)
        (work_dir / "out" / "cover_001.jpg").write_bytes(b"cover-bytes")
        (work_dir / "out" / "insight.json").write_text('{"k":"v"}')
        (work_dir / "out" / "scene_analysis.json").write_text('{"schema_version":"1.0"}')
        (work_dir / "out" / "previews").mkdir(parents=True, exist_ok=True)
        (work_dir / "out" / "previews" / "preview_manifest.json").write_text('{"outputs":[]}')

        r = client.get(f"/jobs/{job_id}/zip")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert f"{job_id}.zip" in r.headers["content-disposition"]
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        names = zf.namelist()
        assert "work_dir/out/cover_001.jpg" in names
        assert "work_dir/out/insight.json" in names
        assert "work_dir/out/scene_analysis.json" in names
        assert "work_dir/out/previews/preview_manifest.json" in names
        assert zf.read("work_dir/out/cover_001.jpg") == b"cover-bytes"

    def test_zip_includes_decision_log_when_pipeline_returns_path(
        self, pod_env, monkeypatch, tmp_path
    ):
        """When the pipeline result carries a ``decision_log_path`` pointing
        at an existing file, the zip ships it at the bundle root so the
        controller can drop it into ``DATA_DIR/decision_logs/``."""
        pw = pod_env["module"]
        decision_log = tmp_path / "fake_dlog.json"
        decision_log.write_text('{"scene_id": "stub", "execution": {"phases": {}}}')

        def _fake_process_scene(_video_path, **_kwargs):
            return {
                "success": True,
                "covers_saved": 5,
                "scene_id": "stub",
                "decision_log_path": str(decision_log),
            }

        import amg.pipeline as pipeline
        monkeypatch.setattr(pipeline, "process_scene", _fake_process_scene)

        app = pw.create_app(auth_token=pod_env["token"])
        client = TestClient(app)
        client.headers["Authorization"] = f"Bearer {pod_env['token']}"

        r = client.post(
            "/jobs",
            files={"video": ("scene.mp4", b"x")},
            data={"scene_id": "stub"},
        )
        job_id = r.json()["job_id"]
        _wait_for_status(client, job_id, "done")

        r = client.get(f"/jobs/{job_id}/zip")
        assert r.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        names = zf.namelist()
        assert "decision_log.json" in names
        body = zf.read("decision_log.json").decode()
        assert "stub" in body

    def test_zip_unknown_job_404(self, authed_client):
        client, _ = authed_client
        r = client.get("/jobs/ghost/zip")
        assert r.status_code == 404


# --- cloud-source jobs (POST /jobs/cloud) -----------------------------------


class TestCloudJobs:
    """The cloud-job path adds an rclone copy step in front of the pipeline.
    Both the rclone wrapper and the pipeline are stubbed so these tests
    don't touch the network or Ollama."""

    SAMPLE_CONFIG = (
        "[gdrive_amy]\n"
        "type = drive\n"
        "token = {\"access_token\":\"ya29.fake\"}\n"
    )

    def _stub_rclone_copy(self, monkeypatch, *, drop_file_named: str = "scene4.mp4"):
        """Replace amg.cloud.rclone.Rclone with a stub that 'downloads' a
        single fake file into the destination directory and emits one
        progress callback so we can assert the wiring."""
        from amg.cloud import rclone as rclone_mod

        class _StubRclone:
            def __init__(self, *_args, **_kwargs):
                pass

            def copy(self, src, dst, *, on_progress=None, on_log=None, **_kw):
                dst_path = Path(dst)
                dst_path.mkdir(parents=True, exist_ok=True)
                (dst_path / drop_file_named).write_bytes(b"fake video bytes")
                if on_progress is not None:
                    on_progress({"done": "1 MiB", "total": "1 MiB", "pct": 100})
                if on_log is not None:
                    on_log("INFO  : copied 1 file")

        monkeypatch.setattr(rclone_mod, "Rclone", _StubRclone)

    def test_cloud_job_downloads_then_runs_pipeline(self, authed_client, monkeypatch):
        client, pw = authed_client
        self._stub_rclone_copy(monkeypatch, drop_file_named="scene4.mp4")

        r = client.post(
            "/jobs/cloud",
            json={
                "remote": "gdrive_amy",
                "path": "incoming/scene4.mp4",
                "rclone_config": self.SAMPLE_CONFIG,
                "scene_id": "scene-cloud",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "queued"
        assert body["source_kind"] == "cloud"
        assert body["remote"] == "gdrive_amy"
        job_id = body["job_id"]

        final = _wait_for_status(client, job_id, "done")
        assert final is not None, "cloud job did not reach 'done'"
        assert final["source_kind"] == "cloud"
        assert final["cloud_source"] == {
            "remote": "gdrive_amy",
            "path": "incoming/scene4.mp4",
        }
        assert final["download_pct"] == 100
        assert final["progress_pct"] == 100
        assert final["result"]["covers_saved"] == 7
        # Both phases logged.
        assert any("downloading" in line for line in final["log_tail"])
        assert any("download complete" in line for line in final["log_tail"])
        assert any("starting process_scene" in line for line in final["log_tail"])
        # The video the pipeline ran against is the file rclone "downloaded".
        assert Path(final["video_path"]).name == "scene4.mp4"
        assert Path(final["video_path"]).exists()

    def test_jobs_hyphen_alias_matches_jobs_cloud(self, authed_client, monkeypatch):
        client, _ = authed_client
        self._stub_rclone_copy(monkeypatch, drop_file_named="scene4.mp4")

        r = client.post(
            "/jobs-cloud",
            json={
                "remote": "gdrive_amy",
                "path": "incoming/scene4.mp4",
                "rclone_config": self.SAMPLE_CONFIG,
                "scene_id": "scene-hyphen",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["source_kind"] == "cloud"
        job_id = body["job_id"]
        final = _wait_for_status(client, job_id, "done")
        assert final is not None
        assert final["result"]["covers_saved"] == 7

    def test_cloud_job_credential_temp_file_cleaned_up(
        self, authed_client, monkeypatch, pod_env
    ):
        client, pw = authed_client
        self._stub_rclone_copy(monkeypatch)

        r = client.post(
            "/jobs/cloud",
            json={
                "remote": "gdrive_amy",
                "path": "scene4.mp4",
                "rclone_config": self.SAMPLE_CONFIG,
            },
        )
        job_id = r.json()["job_id"]
        _wait_for_status(client, job_id, "done")

        # The _creds dir is a sibling of the per-job download dir; after
        # cleanup neither it nor any *.conf inside it should remain.
        uploads_root = pw.POD_UPLOADS_DIR / job_id
        creds_dir = uploads_root / "_creds"
        assert not creds_dir.exists(), \
            "credential temp dir should be cleaned up after rclone copy"
        # And no stray .conf files anywhere in the job tree.
        leftover = list(uploads_root.rglob("*.conf"))
        assert leftover == [], f"leaked credential file(s): {leftover}"

    def test_cloud_job_rclone_failure_marks_error(self, authed_client, monkeypatch):
        client, pw = authed_client
        from amg.cloud import rclone as rclone_mod

        class _ExplodingRclone:
            def __init__(self, *_args, **_kwargs):
                pass

            def copy(self, *_args, **_kwargs):
                raise rclone_mod.RcloneError("auth failure")

        monkeypatch.setattr(rclone_mod, "Rclone", _ExplodingRclone)

        r = client.post(
            "/jobs/cloud",
            json={
                "remote": "gdrive_amy",
                "path": "scene4.mp4",
                "rclone_config": self.SAMPLE_CONFIG,
            },
        )
        job_id = r.json()["job_id"]

        final = _wait_for_status(client, job_id, "error")
        assert final is not None, "rclone failure should mark the job error"
        assert "rclone copy failed" in final["error"]
        assert "auth failure" in final["error"]

    def test_cloud_job_no_downloaded_file_marks_error(self, authed_client, monkeypatch):
        client, _pw = authed_client
        from amg.cloud import rclone as rclone_mod

        class _NoOpRclone:
            def __init__(self, *_args, **_kwargs):
                pass

            def copy(self, *_args, **_kwargs):
                # rclone "succeeded" but produced nothing — config bug shape.
                return None

        monkeypatch.setattr(rclone_mod, "Rclone", _NoOpRclone)

        r = client.post(
            "/jobs/cloud",
            json={
                "remote": "gdrive_amy",
                "path": "scene4.mp4",
                "rclone_config": self.SAMPLE_CONFIG,
            },
        )
        job_id = r.json()["job_id"]

        final = _wait_for_status(client, job_id, "error")
        assert final is not None
        assert "no file landed" in final["error"]

    def test_cloud_job_rejects_remote_with_colon(self, authed_client):
        client, _ = authed_client
        r = client.post(
            "/jobs/cloud",
            json={
                "remote": "gdrive_amy:",
                "path": "scene4.mp4",
                "rclone_config": self.SAMPLE_CONFIG,
            },
        )
        assert r.status_code == 400
        assert "trailing colon" in r.json()["detail"]

    def test_cloud_job_rejects_garbage_config(self, authed_client):
        client, _ = authed_client
        r = client.post(
            "/jobs/cloud",
            json={
                "remote": "gdrive_amy",
                "path": "scene4.mp4",
                "rclone_config": "not actually rclone",
            },
        )
        assert r.status_code == 400
        assert "rclone_config" in r.json()["detail"]

    def test_cloud_job_requires_auth(self, pod_env):
        pw = pod_env["module"]
        app = pw.create_app(auth_token=pod_env["token"])
        client = TestClient(app)  # no Authorization header
        r = client.post(
            "/jobs/cloud",
            json={
                "remote": "gdrive_amy",
                "path": "scene4.mp4",
                "rclone_config": self.SAMPLE_CONFIG,
            },
        )
        assert r.status_code == 401


# --- job retention cap ------------------------------------------------------


class TestRetention:
    def test_old_jobs_evicted_beyond_cap(self, pod_env):
        pw = pod_env["module"]
        tracker = pw._JobTracker(max_retained=3)
        for i in range(5):
            tracker.create(
                f"j{i}",
                scene_id=f"s{i}",
                video_path=Path(f"/tmp/s{i}.mp4"),
                work_dir=Path(f"/tmp/work{i}"),
            )
        ids = tracker.list_ids()
        assert ids == ["j2", "j3", "j4"], "tracker should evict the oldest"
        assert tracker.get("j0") is None
        assert tracker.get("j4") is not None
