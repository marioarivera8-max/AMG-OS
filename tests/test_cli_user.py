"""Tests for the `amg user ...` CLI subcommands."""
from __future__ import annotations

import io
import secrets
import sys

import pytest


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Per-test SQLite DB + reload auth module to pick up the env."""
    db = tmp_path / "auth.sqlite"
    monkeypatch.setenv("AMG_AUTH_DB", str(db))
    monkeypatch.setenv("AMG_SESSION_SECRET", secrets.token_urlsafe(48))
    monkeypatch.delenv("AMG_AUTH_DISABLED", raising=False)
    import importlib

    import amg.ui.auth as auth_mod
    importlib.reload(auth_mod)
    return auth_mod


def _run_cli(argv, capsys, stdin_text=None):
    """Invoke the CLI as if from the shell. Returns (exit_code, stdout, stderr)."""
    from amg.cli import main

    if stdin_text is not None:
        sys.stdin = io.StringIO(stdin_text)
    try:
        sys.argv = ["amg", *argv]
        try:
            code = main()
        except SystemExit as exc:
            code = int(exc.code) if exc.code is not None else 0
    finally:
        if stdin_text is not None:
            sys.stdin = sys.__stdin__
    out, err = capsys.readouterr()
    return code, out, err


class TestUserAdd:
    def test_add_via_password_stdin(self, cli_env, capsys):
        code, out, _ = _run_cli(
            ["user", "add", "alice", "--password-stdin"],
            capsys,
            stdin_text="the right password 123\n",
        )
        assert code == 0
        assert "Created user 'alice'" in out
        assert "first user" in out  # bootstrap hint
        u = cli_env.verify_password("alice", "the right password 123")
        assert u is not None
        assert u["role"] == "operator"

    def test_add_with_role(self, cli_env, capsys):
        code, _, _ = _run_cli(
            ["user", "add", "amy", "--role", "reviewer", "--password-stdin"],
            capsys,
            stdin_text="the right password 123\n",
        )
        assert code == 0
        assert cli_env.get_user("amy")["role"] == "reviewer"

    def test_add_short_password_fails(self, cli_env, capsys):
        code, out, _ = _run_cli(
            ["user", "add", "alice", "--password-stdin"],
            capsys,
            stdin_text="short\n",
        )
        assert code == 1
        assert "at least" in out

    def test_add_duplicate_fails(self, cli_env, capsys):
        cli_env.add_user("alice", "the right password 123")
        code, out, _ = _run_cli(
            ["user", "add", "alice", "--password-stdin"],
            capsys,
            stdin_text="another password 456\n",
        )
        assert code == 1
        assert "already exists" in out

    def test_invalid_role_rejected_by_argparse(self, cli_env, capsys):
        code, _, err = _run_cli(
            ["user", "add", "alice", "--role", "godmode", "--password-stdin"],
            capsys,
            stdin_text="the right password 123\n",
        )
        assert code != 0
        assert "godmode" in err or "invalid choice" in err


class TestUserPasswd:
    def test_passwd_round_trip(self, cli_env, capsys):
        cli_env.add_user("alice", "old password 12345")
        code, out, _ = _run_cli(
            ["user", "passwd", "alice", "--password-stdin"],
            capsys,
            stdin_text="new password 67890\n",
        )
        assert code == 0
        assert "Password updated" in out
        assert cli_env.verify_password("alice", "old password 12345") is None
        assert cli_env.verify_password("alice", "new password 67890") is not None

    def test_passwd_unknown_user(self, cli_env, capsys):
        code, out, _ = _run_cli(
            ["user", "passwd", "ghost", "--password-stdin"],
            capsys,
            stdin_text="the right password 123\n",
        )
        assert code == 1
        assert "not found" in out


class TestUserList:
    def test_list_empty(self, cli_env, capsys):
        code, out, _ = _run_cli(["user", "list"], capsys)
        assert code == 0
        assert "No users yet" in out

    def test_list_shows_users(self, cli_env, capsys):
        cli_env.add_user("alice", "the right password 123", role="admin")
        cli_env.add_user("bob", "the right password 456", role="reviewer")
        code, out, _ = _run_cli(["user", "list"], capsys)
        assert code == 0
        assert "alice" in out
        assert "bob" in out
        assert "admin" in out
        assert "reviewer" in out

    def test_list_marks_disabled(self, cli_env, capsys):
        cli_env.add_user("alice", "the right password 123")
        cli_env.disable_user("alice")
        code, out, _ = _run_cli(["user", "list"], capsys)
        assert "disabled" in out


class TestUserDisableEnableDelete:
    def test_disable(self, cli_env, capsys):
        cli_env.add_user("alice", "the right password 123")
        code, out, _ = _run_cli(["user", "disable", "alice"], capsys)
        assert code == 0
        assert "Disabled 'alice'" in out
        assert cli_env.verify_password("alice", "the right password 123") is None

    def test_disable_unknown(self, cli_env, capsys):
        code, out, _ = _run_cli(["user", "disable", "ghost"], capsys)
        assert code == 1
        assert "not found" in out

    def test_enable(self, cli_env, capsys):
        cli_env.add_user("alice", "the right password 123")
        cli_env.disable_user("alice")
        code, _, _ = _run_cli(["user", "enable", "alice"], capsys)
        assert code == 0
        assert cli_env.verify_password("alice", "the right password 123") is not None

    def test_delete_with_yes(self, cli_env, capsys):
        cli_env.add_user("alice", "the right password 123")
        code, out, _ = _run_cli(["user", "delete", "alice", "--yes"], capsys)
        assert code == 0
        assert "Deleted 'alice'" in out
        assert cli_env.get_user("alice") is None

    def test_delete_unknown(self, cli_env, capsys):
        code, out, _ = _run_cli(["user", "delete", "ghost", "--yes"], capsys)
        assert code == 1

    def test_delete_cancelled_via_prompt(self, cli_env, capsys, monkeypatch):
        cli_env.add_user("alice", "the right password 123")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
        code, out, _ = _run_cli(["user", "delete", "alice"], capsys)
        assert code == 1
        assert "Cancelled" in out
        assert cli_env.get_user("alice") is not None

    def test_delete_confirmed_via_prompt(self, cli_env, capsys, monkeypatch):
        cli_env.add_user("alice", "the right password 123")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "y")
        code, _, _ = _run_cli(["user", "delete", "alice"], capsys)
        assert code == 0
        assert cli_env.get_user("alice") is None
