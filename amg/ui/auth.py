"""
Authentication for the AMG web UI.

This module provides a self-contained user store and FastAPI integration that
the existing v11.3 UI can opt into. It is the foundation for the cloud-hosted
edition (v12), but it also works locally and can be turned off entirely for
the single-Mac usage model that v11.x has shipped with so far.

Design notes:

* User store is a single-file SQLite DB at ``AMG_DATA_DIR/auth.sqlite`` (override
  via ``AMG_AUTH_DB``). Schema is small and forward-compatible: a ``role``
  column is present from day one so Phase 3 (multi-user roles) doesn't need a
  migration.
* Passwords are hashed with Argon2id via ``argon2-cffi`` using the library's
  built-in defaults (those track OWASP's recommendations and are reviewed each
  release).
* Sessions are cookie-based, signed by Starlette's ``SessionMiddleware`` (which
  uses ``itsdangerous``). Server keeps no session table; revocation is done by
  rotating ``AMG_SESSION_SECRET``. For our user count (1 today, ~3 within a
  year) this is the right trade-off.
* The auth gate is a single Starlette middleware that 302s unauthenticated
  requests to ``/login?next=<path>``. Per-route dependencies were considered
  but the UI has 14+ routes already and middleware is cleaner.

Toggles (env vars):

* ``AMG_AUTH_DISABLED=1`` — turn auth off entirely. Default for local Mac use
  while we're still building the cloud edition.
* ``AMG_SESSION_SECRET`` — required when auth is enabled. 32+ random bytes
  (``python -c 'import secrets; print(secrets.token_urlsafe(48))'``). The app
  refuses to install auth without it.
* ``AMG_AUTH_DB`` — override SQLite path (defaults to ``AMG_DATA_DIR/auth.sqlite``).
* ``AMG_SESSION_MAX_AGE_SEC`` — session lifetime (default 14 days).
* ``AMG_SESSION_HTTPS_ONLY=1`` — set the session cookie ``Secure`` flag. Off by
  default so localhost development works; set to ``1`` behind Caddy/Cloudflare.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlencode

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHash
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from amg.config import DATA_DIR

# ---------- module state ----------

_hasher = PasswordHasher()  # argon2id with library defaults

# Routes that bypass the auth gate. Add to this if a new route should be
# reachable without a session (rare).
_AUTH_EXEMPT_PATHS = {"/login", "/logout", "/healthz"}
_AUTH_EXEMPT_PREFIXES = ("/static/",)

# Templates used by the login page. The login template is colocated with the
# main UI templates so the existing Jinja2Templates loader picks it up.
_TEMPLATES_DIR = Path(__file__).parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

MIN_PASSWORD_LEN = 8


# ---------- env / config helpers ----------


def is_auth_enabled() -> bool:
    """True unless the operator explicitly disabled auth via env."""
    return os.environ.get("AMG_AUTH_DISABLED", "").lower() not in {"1", "true", "yes"}


def _session_secret() -> str:
    """
    Return the session-signing secret.

    When auth is enabled we require an explicit secret; refusing to start
    surfaces misconfiguration immediately rather than letting the app run with
    a default key (which would let anyone forge cookies).
    """
    secret = os.environ.get("AMG_SESSION_SECRET", "").strip()
    if not secret:
        if is_auth_enabled():
            raise RuntimeError(
                "AMG_SESSION_SECRET is required when auth is enabled. "
                "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(48))'"
            )
        # Auth disabled — a stable in-process default is fine; nothing relies
        # on session integrity in this mode.
        return "amg-dev-no-auth-do-not-use-in-prod"
    if is_auth_enabled() and len(secret) < 32:
        raise RuntimeError(
            "AMG_SESSION_SECRET is too short (need >=32 chars). "
            "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(48))'"
        )
    return secret


def _session_max_age() -> int:
    raw = os.environ.get("AMG_SESSION_MAX_AGE_SEC", str(60 * 60 * 24 * 14))
    try:
        return max(60, int(raw))
    except ValueError:
        return 60 * 60 * 24 * 14


def _https_only() -> bool:
    return os.environ.get("AMG_SESSION_HTTPS_ONLY", "").lower() in {"1", "true", "yes"}


def _resolve_db_path(db_path: Optional[Path] = None) -> Path:
    if db_path is not None:
        return Path(db_path)
    raw = os.environ.get("AMG_AUTH_DB", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return DATA_DIR / "auth.sqlite"


# ---------- SQLite layer ----------


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    UNIQUE NOT NULL,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'operator',
    created_at    REAL    NOT NULL,
    last_login_at REAL,
    disabled_at   REAL
);
CREATE INDEX IF NOT EXISTS users_username_idx ON users(username);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Optional[Path] = None) -> Path:
    """Create the auth schema if missing. Idempotent."""
    path = _resolve_db_path(db_path)
    with _connect(path) as conn:
        conn.executescript(_SCHEMA)
    return path


def _normalize_username(username: str) -> str:
    """Trim + lowercase. Usernames are case-insensitive in storage and lookup."""
    return (username or "").strip().lower()


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    return dict(row)


# ---------- user CRUD ----------


def add_user(
    username: str,
    password: str,
    role: str = "operator",
    db_path: Optional[Path] = None,
) -> int:
    """Create a new user. Returns the new user id. Raises ValueError on conflict."""
    username = _normalize_username(username)
    if not username:
        raise ValueError("username is required")
    if len(password or "") < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")

    path = init_db(db_path)
    pw_hash = _hasher.hash(password)
    now = time.time()
    with _connect(path) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) "
                "VALUES (?, ?, ?, ?)",
                (username, pw_hash, role, now),
            )
            conn.commit()
            return int(cur.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"user already exists: {username}") from exc


def set_password(
    username: str,
    password: str,
    db_path: Optional[Path] = None,
) -> bool:
    """Replace a user's password. Returns False if user not found."""
    username = _normalize_username(username)
    if len(password or "") < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    path = init_db(db_path)
    pw_hash = _hasher.hash(password)
    with _connect(path) as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (pw_hash, username),
        )
        conn.commit()
        return cur.rowcount > 0


def get_user(username: str, db_path: Optional[Path] = None) -> Optional[dict]:
    username = _normalize_username(username)
    path = init_db(db_path)
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT id, username, role, created_at, last_login_at, disabled_at "
            "FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    return _row_to_dict(row)


def list_users(db_path: Optional[Path] = None) -> List[dict]:
    path = init_db(db_path)
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT id, username, role, created_at, last_login_at, disabled_at "
            "FROM users ORDER BY username ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def count_users(db_path: Optional[Path] = None) -> int:
    path = init_db(db_path)
    with _connect(path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"]) if row else 0


def disable_user(username: str, db_path: Optional[Path] = None) -> bool:
    username = _normalize_username(username)
    path = init_db(db_path)
    now = time.time()
    with _connect(path) as conn:
        cur = conn.execute(
            "UPDATE users SET disabled_at = ? WHERE username = ?",
            (now, username),
        )
        conn.commit()
        return cur.rowcount > 0


def enable_user(username: str, db_path: Optional[Path] = None) -> bool:
    username = _normalize_username(username)
    path = init_db(db_path)
    with _connect(path) as conn:
        cur = conn.execute(
            "UPDATE users SET disabled_at = NULL WHERE username = ?",
            (username,),
        )
        conn.commit()
        return cur.rowcount > 0


def delete_user(username: str, db_path: Optional[Path] = None) -> bool:
    username = _normalize_username(username)
    path = init_db(db_path)
    with _connect(path) as conn:
        cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
        return cur.rowcount > 0


# ---------- password verification ----------


def verify_password(
    username: str,
    password: str,
    db_path: Optional[Path] = None,
) -> Optional[dict]:
    """
    Verify credentials. Returns the user dict on success (and bumps
    last_login_at), None on any failure including wrong password, missing user,
    or disabled account. Argon2 rehashes are written back transparently when
    the hash parameters drift below current defaults.
    """
    username = _normalize_username(username)
    path = init_db(db_path)
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, role, disabled_at "
            "FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None:
            return None
        if row["disabled_at"] is not None:
            return None
        pw_hash = row["password_hash"]
        try:
            _hasher.verify(pw_hash, password)
        except (VerifyMismatchError, InvalidHash):
            return None
        # Rehash if argon2 defaults have moved on.
        if _hasher.check_needs_rehash(pw_hash):
            new_hash = _hasher.hash(password)
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (new_hash, row["id"]),
            )
        conn.execute(
            "UPDATE users SET last_login_at = ? WHERE id = ?",
            (time.time(), row["id"]),
        )
        conn.commit()
        return {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
        }


# ---------- FastAPI integration ----------


def get_current_user(request: Request) -> Optional[dict]:
    """Return the authenticated user dict from the session, or None."""
    if not is_auth_enabled():
        # Synthetic principal so downstream code can treat the request as authed.
        return {"id": 0, "username": "local", "role": "admin"}
    user = request.session.get("user") if hasattr(request, "session") else None
    if not user:
        return None
    return user


def _is_path_exempt(path: str) -> bool:
    if path in _AUTH_EXEMPT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _AUTH_EXEMPT_PREFIXES)


class _AuthGateMiddleware(BaseHTTPMiddleware):
    """
    Redirect unauthenticated browser requests to /login and reject API requests
    with 401 JSON. The split lets HTMX partials surface auth failures cleanly
    instead of swallowing the full login HTML into a partial slot.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if _is_path_exempt(path):
            return await call_next(request)
        user = request.session.get("user") if hasattr(request, "session") else None
        if user:
            return await call_next(request)
        # No session — bounce.
        is_htmx = request.headers.get("HX-Request") == "true"
        if is_htmx or path.startswith("/api/") or path.startswith("/partials/"):
            return Response(status_code=401, content="not authenticated")
        target = "/login"
        if path and path != "/":
            target += "?" + urlencode({"next": path})
        return RedirectResponse(target, status_code=302)


def install_auth(app: FastAPI) -> None:
    """
    Attach session middleware, the auth gate, and login/logout routes to
    ``app``. Safe to call when auth is disabled (becomes a no-op aside from
    registering /login, which simply renders a banner).
    """
    auth_on = is_auth_enabled()
    # Starlette processes middleware in reverse-registration order on the
    # request path: the LAST registered middleware is the OUTER layer. The
    # auth gate needs ``request.session`` set up by SessionMiddleware, so the
    # gate is registered first (becomes inner) and SessionMiddleware second
    # (becomes outer, runs first on each request).
    if auth_on:
        app.add_middleware(_AuthGateMiddleware)
        # Make sure the schema exists before the first login attempt so a fresh
        # deployment doesn't 500 on the first POST /login.
        init_db()
    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_secret(),
        max_age=_session_max_age(),
        same_site="lax",
        https_only=_https_only(),
        session_cookie="amg_session",
    )

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request, next: str = "/"):
        if not auth_on:
            return _templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"request": request, "auth_disabled": True, "next": next},
            )
        # Already logged in? Bounce home.
        if request.session.get("user"):
            return RedirectResponse(next or "/", status_code=302)
        no_users = count_users() == 0
        return _templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "request": request,
                "auth_disabled": False,
                "no_users": no_users,
                "next": next,
                "error": None,
            },
        )

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        next: str = Form("/"),
    ):
        if not auth_on:
            return RedirectResponse(next or "/", status_code=302)
        user = verify_password(username, password)
        if user is None:
            return _templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "request": request,
                    "auth_disabled": False,
                    "no_users": count_users() == 0,
                    "next": next,
                    "error": "Invalid username or password.",
                },
                status_code=401,
            )
        request.session["user"] = user
        # Only allow same-origin redirect targets to avoid open-redirect abuse.
        target = next if next.startswith("/") else "/"
        return RedirectResponse(target, status_code=302)

    @app.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=302)


__all__ = [
    "MIN_PASSWORD_LEN",
    "add_user",
    "count_users",
    "delete_user",
    "disable_user",
    "enable_user",
    "get_current_user",
    "get_user",
    "init_db",
    "install_auth",
    "is_auth_enabled",
    "list_users",
    "set_password",
    "verify_password",
]
