from fastapi.testclient import TestClient
import json
import zipfile
from pathlib import Path


def test_healthz_route_returns_ok():
    from amg.ui.app import create_app

    app = create_app()
    client = TestClient(app)
    res = client.get("/healthz")
    assert res.status_code == 200
    payload = res.json()
    assert payload.get("ok") is True
    assert "snapshot" in payload


def test_healthz_reports_runpod_parallel(monkeypatch):
    import amg.ui.app as app_module
    from amg.ui.app import create_app

    monkeypatch.setenv("AMG_JOB_BACKEND", "runpod")
    monkeypatch.setenv("AMG_RUNPOD_AI_PARALLEL_WORKERS", "6")
    app_module._health_cache = {}
    app_module._health_ts = 0.0

    app = create_app()
    client = TestClient(app)
    res = client.get("/healthz")

    assert res.status_code == 200
    assert res.json()["snapshot"]["parallel"] == "6"


def test_create_job_rejects_invalid_path():
    from amg.ui.app import create_app

    app = create_app()
    client = TestClient(app)
    res = client.post("/jobs", data={"path": "/definitely/not/a/video/path"})
    assert res.status_code == 400


def test_library_route_accepts_new_filters():
    from amg.ui.app import create_app

    app = create_app()
    client = TestClient(app)
    res = client.get(
        "/library",
        params={"status": "failed", "sort": "action_queue", "limit": "24", "min_score": "70"},
    )
    assert res.status_code == 200


def test_process_jobs_partial_returns_queues():
    from amg.ui.app import create_app

    app = create_app()
    client = TestClient(app)
    res = client.get("/partials/process-jobs")
    assert res.status_code == 200
    body = res.text
    assert 'class="process-board"' in body
    assert "RUNNING" in body
    assert "QUEUED" in body


def test_feedback_route_accepts_filter_params():
    from amg.ui.app import create_app

    app = create_app()
    client = TestClient(app)
    res = client.get(
        "/feedback",
        params={"scene": "demo_scene", "studio": "Demo", "since_days": "7", "view": "all"},
    )
    assert res.status_code == 200


def test_artifact_route_blocks_paths_outside_allowed_roots(tmp_path, monkeypatch):
    import amg.ui.app as app_mod

    allowed_root = tmp_path / "allowed"
    blocked_root = tmp_path / "blocked"
    allowed_root.mkdir(parents=True, exist_ok=True)
    blocked_root.mkdir(parents=True, exist_ok=True)
    blocked_file = blocked_root / "secret.txt"
    blocked_file.write_text("nope")

    monkeypatch.setattr(app_mod, "_artifact_allowed_roots", lambda: [allowed_root.resolve()])
    app = app_mod.create_app()
    client = TestClient(app)
    res = client.get("/artifact", params={"path": str(blocked_file)})
    assert res.status_code == 403


def test_artifact_zip_downloads_directory_as_zip(tmp_path, monkeypatch):
    import amg.ui.app as app_mod

    allowed_root = tmp_path / "allowed"
    covers_dir = allowed_root / "covers"
    nested = covers_dir / "nested"
    nested.mkdir(parents=True, exist_ok=True)
    (covers_dir / "01.jpg").write_bytes(b"jpg")
    (nested / "meta.txt").write_text("ok")

    monkeypatch.setattr(app_mod, "_artifact_allowed_roots", lambda: [allowed_root.resolve()])
    app = app_mod.create_app()
    client = TestClient(app)
    res = client.get("/artifact-zip", params={"path": str(covers_dir)})

    assert res.status_code == 200
    assert res.headers.get("content-type") == "application/zip"
    assert "covers.zip" in res.headers.get("content-disposition", "")

    out_zip = tmp_path / "covers.zip"
    out_zip.write_bytes(res.content)
    with zipfile.ZipFile(out_zip, "r") as zf:
        names = sorted(zf.namelist())
    assert names == ["01.jpg", "nested/meta.txt"]


def test_find_work_dir_resolves_cloud_extracted_layout(tmp_path, monkeypatch):
    """Cloud edition: RunpodBackend extracts pod artifacts to
    DATA_DIR/work_dirs/<scene_id>/. The legacy lookup only checked the
    INCOMING_ROOTS + UPLOADS_DIR for a *_amg_v11 suffix, so the scene
    detail page rendered "No covers found in None" even when covers
    were sitting on disk. Regression: 2026-05-06.
    """
    import amg.ui.app as app_mod

    fake_data_dir = tmp_path / "data"
    cloud_extracted = fake_data_dir / "work_dirs" / "muvie"
    (cloud_extracted / "covers").mkdir(parents=True)
    (cloud_extracted / "covers" / "01_test.jpg").write_bytes(b"jpg")

    monkeypatch.setattr(app_mod, "DATA_DIR", fake_data_dir)
    monkeypatch.setattr(app_mod, "UPLOADS_DIR", fake_data_dir / "ui_uploads")

    resolved = app_mod._find_work_dir("muvie")
    assert resolved == cloud_extracted


def test_scene_page_renders_submission_workspace(tmp_path, monkeypatch):
    import amg.ui.app as app_mod

    data_dir = tmp_path / "data"
    scene_id = "scene_workspace"
    work_dir = data_dir / "work_dirs" / scene_id
    work_dir.mkdir(parents=True)
    source_video = data_dir / "source.mp4"
    source_video.write_bytes(b"video")
    (data_dir / "decision_logs").mkdir(parents=True)
    (data_dir / "decision_logs" / f"{scene_id}.json").write_text(
        json.dumps(
            {
                "scene_id": scene_id,
                "scene_path": str(source_video),
                "input": {"studio": "Demo", "resolution": "1920x1080", "duration_sec": 120},
                "execution": {"total_duration_sec": 5, "error_codes": []},
                "outcomes": {"covers_delivered": 0, "saved_covers": []},
            }
        ),
        encoding="utf-8",
    )
    (work_dir / "submission_manifest.json").write_text(
        json.dumps(
            {
                "scene_id": scene_id,
                "source_mode": "local",
                "selected_video": {"filename": "source.mp4", "original_path": str(source_video)},
                "secondary_videos": [],
                "compliance_docs": [
                    {
                        "id": "doc_001",
                        "filename": "2257.pdf",
                        "document_type": "2257",
                        "verified_default": True,
                    }
                ],
                "provided_images": [{"id": "img_001", "filename": "actor.jpg"}],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(app_mod, "DECISION_LOGS_DIR", data_dir / "decision_logs")
    monkeypatch.setattr(app_mod, "REVIEWED_DIR", data_dir / "reviewed")

    app = app_mod.create_app()
    client = TestClient(app)
    res = client.get(f"/scene/{scene_id}")
    assert res.status_code == 200
    assert "Submission Workspace" in res.text
    assert "2257.pdf" in res.text
    assert "actor.jpg" in res.text


def test_review_save_persists_submission_workspace_decisions(tmp_path, monkeypatch):
    import amg.ui.app as app_mod

    data_dir = tmp_path / "data"
    scene_id = "scene_review_workspace"
    work_dir = data_dir / "work_dirs" / scene_id
    (work_dir / "covers").mkdir(parents=True)
    (data_dir / "decision_logs").mkdir(parents=True)
    (data_dir / "decision_logs" / f"{scene_id}.json").write_text(
        json.dumps({"scene_id": scene_id, "scene_path": str(tmp_path / "source.mp4"), "input": {}, "outcomes": {}}),
        encoding="utf-8",
    )
    (work_dir / "submission_manifest.json").write_text(
        json.dumps(
            {
                "scene_id": scene_id,
                "selected_video": {"filename": "source.mp4"},
                "secondary_videos": [],
                "compliance_docs": [{"id": "doc_001", "filename": "2257.pdf", "document_type": "2257"}],
                "provided_images": [{"id": "img_001", "filename": "actor.jpg"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(app_mod, "DECISION_LOGS_DIR", data_dir / "decision_logs")
    monkeypatch.setattr(app_mod, "REVIEWED_DIR", data_dir / "reviewed")
    monkeypatch.setattr(app_mod, "OPERATOR_FEEDBACK_DIR", data_dir / "operator_feedback")
    monkeypatch.setattr(app_mod, "OPERATOR_FEEDBACK_PATH", data_dir / "operator_feedback" / "feedback.jsonl")
    monkeypatch.setattr(app_mod, "export_approved_example_bank", lambda **_: {})

    app = app_mod.create_app()
    client = TestClient(app, follow_redirects=False)
    res = client.post(
        f"/scene/{scene_id}/review",
        data={
            "title": "Retail Ready Title",
            "long_description": "A complete description for review.",
            "tags_csv": "tag1,tag2",
            "categories_csv": "Category",
            "performers_confirmed": "Jane Doe, Jill Doe",
            "target_platforms": "AEBN",
            "submission_doc_id": "doc_001",
            "submission_doc_verified": "doc_001",
            "submission_doc_type_doc_001": "2257",
            "submission_doc_performer_doc_001": "Jane Doe",
            "provided_image_id": "img_001",
            "provided_image_decision_img_001": "include",
        },
    )
    assert res.status_code == 303
    reviewed = json.loads((data_dir / "reviewed" / f"{scene_id}.json").read_text(encoding="utf-8"))
    assert reviewed["performers_confirmed"] == ["Jane Doe", "Jill Doe"]
    assert reviewed["submission_docs"]["doc_001"]["verified"] is True
    assert reviewed["provided_image_decisions"]["img_001"]["decision"] == "include"


def test_source_video_download_local_file(tmp_path, monkeypatch):
    import amg.ui.app as app_mod

    data_dir = tmp_path / "data"
    scene_id = "scene_download"
    work_dir = data_dir / "work_dirs" / scene_id
    work_dir.mkdir(parents=True)
    source = data_dir / "source.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"video-bytes")
    (data_dir / "decision_logs").mkdir(parents=True)
    (data_dir / "decision_logs" / f"{scene_id}.json").write_text(
        json.dumps({"scene_id": scene_id, "scene_path": str(source)}),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(app_mod, "DECISION_LOGS_DIR", data_dir / "decision_logs")

    app = app_mod.create_app()
    client = TestClient(app)
    res = client.get(f"/scene/{scene_id}/source-video")
    assert res.status_code == 200
    assert res.content == b"video-bytes"


def test_source_video_download_cloud_streams_rclone(tmp_path, monkeypatch):
    import amg.ui.app as app_mod
    import amg.cloud.credentials as creds_mod
    import amg.cloud.rclone as rclone_mod

    data_dir = tmp_path / "data"
    scene_id = "scene_cloud_download"
    work_dir = data_dir / "work_dirs" / scene_id
    work_dir.mkdir(parents=True)
    (data_dir / "decision_logs").mkdir(parents=True)
    (data_dir / "decision_logs" / f"{scene_id}.json").write_text(
        json.dumps({"scene_id": scene_id, "scene_path": "cloud://gdrive_amy/incoming/source.mp4"}),
        encoding="utf-8",
    )
    (work_dir / "submission_manifest.json").write_text(
        json.dumps(
            {
                "scene_id": scene_id,
                "source_mode": "cloud",
                "selected_video": {"filename": "source.mp4"},
                "cloud_source": {"remote": "gdrive_amy", "path": "incoming/source.mp4"},
            }
        ),
        encoding="utf-8",
    )

    class _CM:
        def __enter__(self):
            return tmp_path / "rclone.conf"

        def __exit__(self, *_args):
            return False

    class _Store:
        def materialize_config(self, names=None):
            assert names == ["gdrive_amy"]
            return _CM()

    class _Rclone:
        def __init__(self, config_path=None):
            assert config_path == tmp_path / "rclone.conf"

        def cat(self, src):
            assert src == "gdrive_amy:incoming/source.mp4"
            yield b"cloud-bytes"

    monkeypatch.setattr(creds_mod, "CredentialStore", _Store)
    monkeypatch.setattr(rclone_mod, "Rclone", _Rclone)
    monkeypatch.setattr(app_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(app_mod, "DECISION_LOGS_DIR", data_dir / "decision_logs")

    app = app_mod.create_app()
    client = TestClient(app)
    res = client.get(f"/scene/{scene_id}/source-video")
    assert res.status_code == 200
    assert res.content == b"cloud-bytes"


def test_legacy_review_route_redirects_to_scene_path():
    from amg.ui.app import create_app

    app = create_app()
    client = TestClient(app, follow_redirects=False)
    res = client.get("/covers/review/demo_scene_1")
    assert res.status_code == 307
    assert res.headers.get("location") == "/scene/demo_scene_1"


def test_legacy_review_query_route_resolves_scene_from_job_id(monkeypatch):
    import amg.ui.app as app_mod

    with app_mod._jobs_lock:
        app_mod._jobs["job_legacy_1"] = {"job_id": "job_legacy_1", "scene_id": "legacy_scene_1", "result": None}

    try:
        app = app_mod.create_app()
        client = TestClient(app, follow_redirects=False)
        res = client.get("/covers/review", params={"job_id": "job_legacy_1"})
        assert res.status_code == 307
        assert res.headers.get("location") == "/scene/legacy_scene_1"
    finally:
        with app_mod._jobs_lock:
            app_mod._jobs.pop("job_legacy_1", None)


def test_scene_rerun_queues_job_and_redirects_home(monkeypatch):
    import amg.ui.app as app_mod

    with app_mod._jobs_lock:
        app_mod._jobs.clear()
        app_mod._job_fifo.clear()
        app_mod._job_seq_counter = 0

    monkeypatch.setattr(
        app_mod,
        "_load_decision_log",
        lambda _sid: {"scene_id": "demo_scene", "scene_path": "/tmp/demo_scene.mp4"},
    )
    monkeypatch.setattr(app_mod, "_resolve_video_path", lambda _p: Path("/tmp/demo_scene.mp4"))
    monkeypatch.setattr(app_mod, "_start_dispatcher_if_needed", lambda: None)

    app = app_mod.create_app()
    client = TestClient(app, follow_redirects=False)
    res = client.post("/scene/demo_scene/rerun")
    assert res.status_code == 303
    assert res.headers.get("location", "").startswith("/?job_id=")

    with app_mod._jobs_lock:
        assert len(app_mod._jobs) == 1
        job = next(iter(app_mod._jobs.values()))
    assert job["scene_id"] == "demo_scene"
    assert job["source_mode"] == "path"
    assert "rerun" in str(job.get("message") or "").lower()


def test_scene_rerun_redirects_with_error_when_source_missing(monkeypatch):
    import amg.ui.app as app_mod

    monkeypatch.setattr(
        app_mod,
        "_load_decision_log",
        lambda _sid: {"scene_id": "demo_scene", "scene_path": ""},
    )
    app = app_mod.create_app()
    client = TestClient(app, follow_redirects=False)
    res = client.post("/scene/demo_scene/rerun")
    assert res.status_code == 303
    assert "/scene/demo_scene?rerun_error=" in (res.headers.get("location") or "")


def test_scene_rerun_falls_back_to_cloud_source_when_local_path_missing(monkeypatch):
    import amg.ui.app as app_mod

    with app_mod._jobs_lock:
        app_mod._jobs.clear()
        app_mod._job_fifo.clear()
        app_mod._job_seq_counter = 0
        app_mod._jobs["j_done"] = {
            "job_id": "j_done",
            "status": "done",
            "scene_id": "demo_scene",
            "cloud_source": {
                "remote": "gdrive_amy",
                "path": "incoming/demo_scene.mp4",
                "scene_id": "demo_scene",
                "download_root": "incoming",
                "relative_path": "demo_scene.mp4",
            },
            "result": {"scene_id": "demo_scene"},
        }

    monkeypatch.setattr(
        app_mod,
        "_load_decision_log",
        lambda _sid: {"scene_id": "demo_scene", "scene_path": "/missing/path.mp4"},
    )
    monkeypatch.setattr(app_mod, "_resolve_video_path", lambda _p: None)
    monkeypatch.setattr(app_mod, "_start_dispatcher_if_needed", lambda: None)

    app = app_mod.create_app()
    client = TestClient(app, follow_redirects=False)
    res = client.post("/scene/demo_scene/rerun")
    assert res.status_code == 303
    assert res.headers.get("location", "").startswith("/?job_id=")

    with app_mod._jobs_lock:
        queued = [j for j in app_mod._jobs.values() if j.get("status") == "queued"]
    assert queued, "expected queued rerun job"
    q = queued[-1]
    assert q["source_mode"] == "cloud"
    assert q["cloud_source"]["remote"] == "gdrive_amy"


def test_latest_cloud_source_falls_back_to_persisted_registry(tmp_path, monkeypatch):
    import json
    import amg.ui.app as app_mod

    with app_mod._jobs_lock:
        app_mod._jobs.clear()

    monkeypatch.setattr(app_mod, "RERUN_SOURCES_PATH", tmp_path / "rerun_sources.json")
    payload = {
        "demo_scene": {
            "remote": "gdrive_amy",
            "path": "incoming/demo.mp4",
            "download_root": "incoming",
            "relative_path": "demo.mp4",
        }
    }
    (tmp_path / "rerun_sources.json").write_text(json.dumps(payload), encoding="utf-8")
    got = app_mod._latest_cloud_source_for_scene("demo_scene")
    assert isinstance(got, dict)
    assert got.get("remote") == "gdrive_amy"


def test_scene_rerun_falls_back_to_local_video_path_when_cloud_missing(monkeypatch):
    import amg.ui.app as app_mod

    with app_mod._jobs_lock:
        app_mod._jobs.clear()
        app_mod._job_fifo.clear()
        app_mod._job_seq_counter = 0
        app_mod._jobs["j_done_local"] = {
            "job_id": "j_done_local",
            "status": "done",
            "scene_id": "demo_scene",
            "video_path": "/tmp/demo_scene.mp4",
            "result": {"scene_id": "demo_scene"},
        }

    monkeypatch.setattr(
        app_mod,
        "_load_decision_log",
        lambda _sid: {"scene_id": "demo_scene", "scene_path": "/missing/path.mp4"},
    )
    monkeypatch.setattr(
        app_mod,
        "_resolve_video_path",
        lambda p: Path("/tmp/demo_scene.mp4") if "demo_scene.mp4" in str(p).replace("\\", "/") else None,
    )
    monkeypatch.setattr(app_mod, "_start_dispatcher_if_needed", lambda: None)

    app = app_mod.create_app()
    client = TestClient(app, follow_redirects=False)
    res = client.post("/scene/demo_scene/rerun")
    assert res.status_code == 303
    assert res.headers.get("location", "").startswith("/?job_id=")

    with app_mod._jobs_lock:
        queued = [j for j in app_mod._jobs.values() if j.get("status") == "queued"]
    assert queued, "expected queued rerun job"
    q = queued[-1]
    assert q["source_mode"] == "path"
    assert str(q["video_path"]).replace("\\", "/") == "/tmp/demo_scene.mp4"
