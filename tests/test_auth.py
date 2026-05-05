"""Tests for amg.ui.auth — user store + FastAPI integration."""
from __future__ import annotations

import os
import secrets

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def auth_db(tmp_path, monkeypatch):
    """Use a per-test SQLite DB and a real session secret."""
    db_path = tmp_path / "auth.sqlite"
    monkeypatch.setenv("AMG_AUTH_DB", str(db_path))
    monkeypatch.setenv("AMG_SESSION_SECRET", secrets.token_urlsafe(48))
    monkeypatch.delenv("AMG_AUTH_DISABLED", raising=False)
    yield db_path


@pytest.fixture
def auth_module(auth_db):
    """Re-import the auth module so module-level config picks up env."""
    import importlib

    import amg.ui.auth as auth_mod
    importlib.reload(auth_mod)
    return auth_mod


@pytest.fixture
def app_with_auth(auth_module):
    """Build a tiny FastAPI app with auth wired in for end-to-end tests."""
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):  # pragma: no cover - trivial
        user = request.session.get("user")
        return HTMLResponse(f"home for {user['username'] if user else 'nobody'}")

    @app.get("/healthz")
    async def healthz():  # pragma: no cover - trivial
        return {"ok": True}

    auth_module.install_auth(app)
    return app


# --- password hashing -------------------------------------------------------


class TestPasswordHashing:
    def test_add_and_verify(self, auth_module):
        auth_module.add_user("alice", "correct horse battery staple")
        user = auth_module.verify_password("alice", "correct horse battery staple")
        assert user is not None
        assert user["username"] == "alice"
        assert user["role"] == "operator"

    def test_wrong_password(self, auth_module):
        auth_module.add_user("alice", "correct horse battery staple")
        assert auth_module.verify_password("alice", "wrong password!!!") is None

    def test_unknown_user(self, auth_module):
        assert auth_module.verify_password("ghost", "any password 123") is None

    def test_username_normalized(self, auth_module):
        """Usernames are case-insensitive in storage and lookup."""
        auth_module.add_user("Mario", "supersecret123")
        assert auth_module.verify_password("MARIO", "supersecret123") is not None
        assert auth_module.verify_password("  mario  ", "supersecret123") is not None

    def test_password_min_length(self, auth_module):
        with pytest.raises(ValueError, match="at least"):
            auth_module.add_user("bob", "short")

    def test_set_password_round_trip(self, auth_module):
        auth_module.add_user("alice", "old password 12345")
        assert auth_module.set_password("alice", "new password 67890") is True
        assert auth_module.verify_password("alice", "old password 12345") is None
        assert auth_module.verify_password("alice", "new password 67890") is not None

    def test_set_password_unknown_user(self, auth_module):
        assert auth_module.set_password("ghost", "some password 123") is False

    def test_password_hash_uses_argon2(self, auth_module, auth_db):
        """Sanity check: a real argon2id hash, not a no-op fallback."""
        import sqlite3
        auth_module.add_user("alice", "the right password 123")
        with sqlite3.connect(str(auth_db)) as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE username = 'alice'").fetchone()
        assert row[0].startswith("$argon2id$")


# --- user CRUD --------------------------------------------------------------


class TestUserCrud:
    def test_duplicate_user_rejected(self, auth_module):
        auth_module.add_user("alice", "the right password 123")
        with pytest.raises(ValueError, match="already exists"):
            auth_module.add_user("alice", "another password 456")

    def test_get_user_omits_password_hash(self, auth_module):
        auth_module.add_user("alice", "the right password 123")
        u = auth_module.get_user("alice")
        assert u is not None
        assert "password_hash" not in u
        assert u["username"] == "alice"

    def test_list_users(self, auth_module):
        auth_module.add_user("alice", "the right password 123", role="admin")
        auth_module.add_user("bob", "the right password 456", role="reviewer")
        users = auth_module.list_users()
        assert {u["username"] for u in users} == {"alice", "bob"}
        assert {u["role"] for u in users} == {"admin", "reviewer"}

    def test_count_users(self, auth_module):
        assert auth_module.count_users() == 0
        auth_module.add_user("alice", "the right password 123")
        auth_module.add_user("bob", "the right password 456")
        assert auth_module.count_users() == 2

    def test_disable_blocks_login(self, auth_module):
        auth_module.add_user("alice", "the right password 123")
        assert auth_module.disable_user("alice") is True
        assert auth_module.verify_password("alice", "the right password 123") is None

    def test_enable_restores_login(self, auth_module):
        auth_module.add_user("alice", "the right password 123")
        auth_module.disable_user("alice")
        auth_module.enable_user("alice")
        assert auth_module.verify_password("alice", "the right password 123") is not None

    def test_delete_user(self, auth_module):
        auth_module.add_user("alice", "the right password 123")
        assert auth_module.delete_user("alice") is True
        assert auth_module.get_user("alice") is None
        assert auth_module.delete_user("alice") is False

    def test_last_login_updated_on_verify(self, auth_module):
        auth_module.add_user("alice", "the right password 123")
        before = auth_module.get_user("alice")
        assert before["last_login_at"] is None
        auth_module.verify_password("alice", "the right password 123")
        after = auth_module.get_user("alice")
        assert after["last_login_at"] is not None


# --- FastAPI end-to-end -----------------------------------------------------


class TestAuthIntegration:
    def test_unauth_redirected_to_login(self, app_with_auth):
        client = TestClient(app_with_auth, follow_redirects=False)
        r = client.get("/")
        assert r.status_code == 302
        assert r.headers["location"].startswith("/login")

    def test_login_page_renders(self, app_with_auth):
        client = TestClient(app_with_auth)
        r = client.get("/login")
        assert r.status_code == 200
        assert "Sign in" in r.text

    def test_no_users_warning_in_login_page(self, app_with_auth):
        client = TestClient(app_with_auth)
        r = client.get("/login")
        assert "No users have been created" in r.text

    def test_login_with_correct_credentials(self, app_with_auth, auth_module):
        auth_module.add_user("alice", "the right password 123")
        client = TestClient(app_with_auth, follow_redirects=False)
        r = client.post(
            "/login",
            data={"username": "alice", "password": "the right password 123"},
        )
        assert r.status_code == 302
        assert r.headers["location"] == "/"
        # Session cookie set
        assert "amg_session" in r.cookies

    def test_login_with_wrong_password(self, app_with_auth, auth_module):
        auth_module.add_user("alice", "the right password 123")
        client = TestClient(app_with_auth, follow_redirects=False)
        r = client.post(
            "/login",
            data={"username": "alice", "password": "the WRONG password 999"},
        )
        assert r.status_code == 401
        assert "Invalid username or password" in r.text

    def test_authed_session_can_reach_home(self, app_with_auth, auth_module):
        auth_module.add_user("alice", "the right password 123")
        client = TestClient(app_with_auth)
        client.post(
            "/login",
            data={"username": "alice", "password": "the right password 123"},
        )
        r = client.get("/")
        assert r.status_code == 200
        assert "home for alice" in r.text

    def test_logout_clears_session(self, app_with_auth, auth_module):
        auth_module.add_user("alice", "the right password 123")
        client = TestClient(app_with_auth)
        client.post(
            "/login",
            data={"username": "alice", "password": "the right password 123"},
        )
        client.post("/logout")
        client = TestClient(app_with_auth, follow_redirects=False)
        r = client.get("/")
        assert r.status_code == 302
        assert r.headers["location"].startswith("/login")

    def test_healthz_bypasses_auth(self, app_with_auth):
        client = TestClient(app_with_auth)
        r = client.get("/healthz")
        assert r.status_code == 200

    def test_next_param_preserved_round_trip(self, app_with_auth, auth_module):
        auth_module.add_user("alice", "the right password 123")
        client = TestClient(app_with_auth, follow_redirects=False)
        r = client.get("/library")
        assert r.status_code == 302
        assert "next=%2Flibrary" in r.headers["location"]
        r = client.post(
            "/login",
            data={
                "username": "alice",
                "password": "the right password 123",
                "next": "/library",
            },
        )
        assert r.status_code == 302
        assert r.headers["location"] == "/library"

    def test_open_redirect_blocked(self, app_with_auth, auth_module):
        """`next` must be a same-origin path; external URLs get rewritten to /."""
        auth_module.add_user("alice", "the right password 123")
        client = TestClient(app_with_auth, follow_redirects=False)
        r = client.post(
            "/login",
            data={
                "username": "alice",
                "password": "the right password 123",
                "next": "https://evil.example.com/phish",
            },
        )
        assert r.status_code == 302
        assert r.headers["location"] == "/"

    def test_htmx_request_gets_401_not_redirect(self, app_with_auth):
        """HTMX partials should see 401 instead of a full HTML login page."""
        client = TestClient(app_with_auth, follow_redirects=False)
        r = client.get("/", headers={"HX-Request": "true"})
        assert r.status_code == 401


# --- env / disabled mode ----------------------------------------------------


class TestAuthDisabled:
    def test_disabled_mode_lets_everything_through(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AMG_AUTH_DISABLED", "1")
        monkeypatch.setenv("AMG_AUTH_DB", str(tmp_path / "auth.sqlite"))
        monkeypatch.delenv("AMG_SESSION_SECRET", raising=False)
        import importlib

        import amg.ui.auth as auth_mod
        importlib.reload(auth_mod)
        app = FastAPI()

        @app.get("/", response_class=HTMLResponse)
        async def home():  # pragma: no cover - trivial
            return HTMLResponse("ok")

        auth_mod.install_auth(app)
        client = TestClient(app)
        r = client.get("/")
        assert r.status_code == 200
        assert "ok" in r.text

    def test_get_current_user_synthetic_when_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AMG_AUTH_DISABLED", "1")
        monkeypatch.setenv("AMG_AUTH_DB", str(tmp_path / "auth.sqlite"))
        import importlib

        import amg.ui.auth as auth_mod
        importlib.reload(auth_mod)

        class FakeRequest:
            session: dict = {}

        u = auth_mod.get_current_user(FakeRequest())
        assert u is not None
        assert u["username"] == "local"
        assert u["role"] == "admin"

    def test_missing_secret_when_enabled_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AMG_AUTH_DB", str(tmp_path / "auth.sqlite"))
        monkeypatch.delenv("AMG_SESSION_SECRET", raising=False)
        monkeypatch.delenv("AMG_AUTH_DISABLED", raising=False)
        import importlib

        import amg.ui.auth as auth_mod
        importlib.reload(auth_mod)
        app = FastAPI()
        with pytest.raises(RuntimeError, match="AMG_SESSION_SECRET"):
            auth_mod.install_auth(app)

    def test_short_secret_when_enabled_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AMG_AUTH_DB", str(tmp_path / "auth.sqlite"))
        monkeypatch.setenv("AMG_SESSION_SECRET", "tooshort")
        monkeypatch.delenv("AMG_AUTH_DISABLED", raising=False)
        import importlib

        import amg.ui.auth as auth_mod
        importlib.reload(auth_mod)
        app = FastAPI()
        with pytest.raises(RuntimeError, match="too short"):
            auth_mod.install_auth(app)
