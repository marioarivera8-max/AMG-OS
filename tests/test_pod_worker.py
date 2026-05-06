"""Tests for amg.cloud.pod_worker — pod-side FastAPI service."""
from __future__ import annotations

import io
import secrets
import time
import zipfile
from pathlib import Path

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

    def _fake_process_scene(video_path):
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

        def _explode(_video_path):
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


# --- /jobs/{id}/zip ---------------------------------------------------------


class TestJobZip:
    def test_zip_streams_work_dir_contents(self, authed_client, tmp_path):
        client, pw = authed_client
        # Create a job, wait for completion, drop a couple of fake artifacts
        # into its work dir, then ask for the zip.
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
        (work_dir / "out" / "decision_log.json").write_text('{"k":"v"}')

        r = client.get(f"/jobs/{job_id}/zip")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert f"{job_id}.zip" in r.headers["content-disposition"]
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        names = zf.namelist()
        assert "out/cover_001.jpg" in names
        assert "out/decision_log.json" in names
        assert zf.read("out/cover_001.jpg") == b"cover-bytes"

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
