"""End-to-end check that auth is wired into the real UI app via create_app()."""
from __future__ import annotations

import importlib
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def authed_app(tmp_path, monkeypatch):
    """Build the real UI app with auth ON and a per-test SQLite DB."""
    monkeypatch.setenv("AMG_AUTH_DB", str(tmp_path / "auth.sqlite"))
    monkeypatch.setenv("AMG_SESSION_SECRET", secrets.token_urlsafe(48))
    monkeypatch.delenv("AMG_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("AMG_DATA_DIR", str(tmp_path / "data"))

    import amg.config as cfg_mod
    importlib.reload(cfg_mod)
    import amg.ui.auth as auth_mod
    importlib.reload(auth_mod)
    import amg.ui.app as app_mod
    importlib.reload(app_mod)

    app = app_mod.create_app()
    return app, auth_mod


def test_unauth_request_to_index_redirects_to_login(authed_app):
    app, _ = authed_app
    client = TestClient(app, follow_redirects=False)
    r = client.get("/")
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login")


def test_login_page_is_reachable_without_auth(authed_app):
    app, _ = authed_app
    client = TestClient(app)
    r = client.get("/login")
    assert r.status_code == 200
    assert "Sign in" in r.text


def test_healthz_is_reachable_without_auth(authed_app):
    app, _ = authed_app
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200


def test_full_login_flow_against_real_app(authed_app):
    app, auth_mod = authed_app
    auth_mod.add_user("alice", "the right password 123")

    client = TestClient(app)
    r = client.post(
        "/login",
        data={"username": "alice", "password": "the right password 123"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    # After login, the index page should render with status 200.
    r2 = client.get("/")
    assert r2.status_code == 200
    # The base.html topbar should now show the avatar with first letter.
    assert ">A<" in r2.text or "alice" in r2.text


def test_static_assets_bypass_auth(authed_app):
    """Static files don't need a session — needed for the login page CSS/JS."""
    app, _ = authed_app
    client = TestClient(app, follow_redirects=False)
    # Even if the file doesn't exist, the auth gate should not redirect to /login.
    r = client.get("/static/does-not-exist.css")
    assert r.status_code in (200, 404)
    assert r.status_code != 302


def test_htmx_partial_unauth_returns_401_with_hx_redirect(authed_app):
    app, _ = authed_app
    client = TestClient(app, follow_redirects=False)
    r = client.get("/partials/process-jobs", headers={"HX-Request": "true"})
    assert r.status_code == 401
    assert "HX-Redirect" in r.headers
