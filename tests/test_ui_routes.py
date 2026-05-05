from fastapi.testclient import TestClient


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


def test_feedback_route_accepts_filter_params():
    from amg.ui.app import create_app

    app = create_app()
    client = TestClient(app)
    res = client.get(
        "/feedback",
        params={"scene": "demo_scene", "studio": "Demo", "since_days": "7", "view": "all"},
    )
    assert res.status_code == 200
