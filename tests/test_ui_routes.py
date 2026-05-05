from fastapi.testclient import TestClient
import zipfile


def test_healthz_route_returns_ok():
    from amg.ui.app import create_app

    app = create_app()
    client = TestClient(app)
    res = client.get("/healthz")
    assert res.status_code == 200
    payload = res.json()
    assert payload.get("ok") is True
    assert "snapshot" in payload


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
    assert 'id="active-jobs-stack"' in body
    assert 'id="completed-jobs-stack"' in body


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
