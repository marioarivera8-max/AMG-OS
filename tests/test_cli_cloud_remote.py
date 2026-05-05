"""
Integration tests for the ``amg cloud-remote`` CLI subcommands.

These hit the real argparse layer (so we catch broken wiring) and the
real CredentialStore (so we catch round-trip mismatches between CLI and
storage). The rclone binary is mocked because the CLI ``test`` command
is the only one that shells out, and we don't want to depend on rclone
being installed for unit tests to pass.
"""
from __future__ import annotations

import io
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from amg.cli import main as cli_main


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolate every test: fresh credentials.sqlite + fresh Fernet key."""
    db = tmp_path / "credentials.sqlite"
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("AMG_CREDENTIALS_DB", str(db))
    monkeypatch.setenv("AMG_CREDENTIALS_KEY", key)
    return {"db": db, "key": key, "tmp_path": tmp_path}


@pytest.fixture
def sample_config_file(env) -> Path:
    text = (
        "[gdrive_amy]\n"
        "type = drive\n"
        "client_id = abc\n"
        "token = {\"access_token\":\"ya29.fake\"}\n"
    )
    p = env["tmp_path"] / "section.ini"
    p.write_text(text, encoding="utf-8")
    return p


def _run_cli(argv, monkeypatch, capsys):
    """Invoke amg.cli.main with sys.argv = ['amg', *argv] and capture
    stdout/exit code."""
    monkeypatch.setattr("sys.argv", ["amg", *argv])
    rc = cli_main()
    out = capsys.readouterr().out
    return rc, out


# --- list -------------------------------------------------------------------


class TestList:
    def test_empty(self, env, monkeypatch, capsys):
        rc, out = _run_cli(["cloud-remote", "list"], monkeypatch, capsys)
        assert rc == 0
        assert "No cloud remotes" in out
        assert "amg cloud-remote add" in out

    def test_after_add(self, env, monkeypatch, capsys, sample_config_file):
        _run_cli(
            ["cloud-remote", "add", "--from-file", str(sample_config_file), "--notes", "amy's drive"],
            monkeypatch, capsys,
        )
        rc, out = _run_cli(["cloud-remote", "list"], monkeypatch, capsys)
        assert rc == 0
        assert "gdrive_amy" in out
        assert "drive" in out
        assert "amy's drive" in out


# --- add --------------------------------------------------------------------


class TestAdd:
    def test_add_from_file(self, env, monkeypatch, capsys, sample_config_file):
        rc, out = _run_cli(
            ["cloud-remote", "add", "--from-file", str(sample_config_file)],
            monkeypatch, capsys,
        )
        assert rc == 0
        assert "Added remote 'gdrive_amy'" in out
        assert "kind=drive" in out

    def test_add_from_missing_file(self, env, monkeypatch, capsys):
        rc, out = _run_cli(
            ["cloud-remote", "add", "--from-file", "/no/such/path"],
            monkeypatch, capsys,
        )
        assert rc == 1
        assert "cannot read --from-file" in out

    def test_add_invalid_config(self, env, monkeypatch, capsys, tmp_path):
        bad = tmp_path / "bad.ini"
        bad.write_text("not a valid rclone config", encoding="utf-8")
        rc, out = _run_cli(
            ["cloud-remote", "add", "--from-file", str(bad)],
            monkeypatch, capsys,
        )
        assert rc == 1
        assert "section" in out.lower() or "type" in out.lower()

    def test_add_duplicate_without_force_fails(
        self, env, monkeypatch, capsys, sample_config_file
    ):
        _run_cli(
            ["cloud-remote", "add", "--from-file", str(sample_config_file)],
            monkeypatch, capsys,
        )
        rc, out = _run_cli(
            ["cloud-remote", "add", "--from-file", str(sample_config_file)],
            monkeypatch, capsys,
        )
        assert rc == 1
        assert "--force" in out

    def test_add_duplicate_with_force_overwrites(
        self, env, monkeypatch, capsys, sample_config_file
    ):
        _run_cli(
            ["cloud-remote", "add", "--from-file", str(sample_config_file)],
            monkeypatch, capsys,
        )
        rc, out = _run_cli(
            ["cloud-remote", "add", "--from-file", str(sample_config_file), "--force"],
            monkeypatch, capsys,
        )
        assert rc == 0
        assert "Added remote 'gdrive_amy'" in out

    def test_add_from_stdin(self, env, monkeypatch, capsys):
        config_text = (
            "[mega_main]\n"
            "type = mega\n"
            "user = amy\n"
            "pass = obscured\n"
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(config_text))
        rc, out = _run_cli(["cloud-remote", "add"], monkeypatch, capsys)
        assert rc == 0
        assert "Added remote 'mega_main'" in out

    def test_add_empty_stdin_fails(self, env, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        rc, out = _run_cli(["cloud-remote", "add"], monkeypatch, capsys)
        assert rc == 1
        assert "empty" in out.lower()


# --- show -------------------------------------------------------------------


class TestShow:
    def test_show_with_yes_prints_decrypted(
        self, env, monkeypatch, capsys, sample_config_file
    ):
        _run_cli(
            ["cloud-remote", "add", "--from-file", str(sample_config_file)],
            monkeypatch, capsys,
        )
        rc, out = _run_cli(
            ["cloud-remote", "show", "gdrive_amy", "--yes"],
            monkeypatch, capsys,
        )
        assert rc == 0
        assert "[gdrive_amy]" in out
        assert "type = drive" in out
        assert "ya29.fake" in out

    def test_show_missing_remote(self, env, monkeypatch, capsys):
        rc, out = _run_cli(
            ["cloud-remote", "show", "phantom", "--yes"],
            monkeypatch, capsys,
        )
        assert rc == 1
        assert "phantom" in out

    def test_show_prompt_decline_aborts(
        self, env, monkeypatch, capsys, sample_config_file
    ):
        _run_cli(
            ["cloud-remote", "add", "--from-file", str(sample_config_file)],
            monkeypatch, capsys,
        )
        monkeypatch.setattr("builtins.input", lambda: "n")
        rc, out = _run_cli(["cloud-remote", "show", "gdrive_amy"], monkeypatch, capsys)
        assert rc == 0
        assert "Aborted" in out
        # Crucially the secret is NOT in the output.
        assert "ya29.fake" not in out


# --- remove -----------------------------------------------------------------


class TestRemove:
    def test_remove_with_yes(self, env, monkeypatch, capsys, sample_config_file):
        _run_cli(
            ["cloud-remote", "add", "--from-file", str(sample_config_file)],
            monkeypatch, capsys,
        )
        rc, out = _run_cli(
            ["cloud-remote", "remove", "gdrive_amy", "--yes"],
            monkeypatch, capsys,
        )
        assert rc == 0
        assert "Deleted" in out

    def test_remove_missing(self, env, monkeypatch, capsys):
        rc, out = _run_cli(
            ["cloud-remote", "remove", "phantom", "--yes"],
            monkeypatch, capsys,
        )
        assert rc == 1
        assert "No remote named" in out

    def test_remove_prompt_decline_aborts(
        self, env, monkeypatch, capsys, sample_config_file
    ):
        _run_cli(
            ["cloud-remote", "add", "--from-file", str(sample_config_file)],
            monkeypatch, capsys,
        )
        monkeypatch.setattr("builtins.input", lambda: "n")
        rc, out = _run_cli(["cloud-remote", "remove", "gdrive_amy"], monkeypatch, capsys)
        assert rc == 0
        assert "Aborted" in out


# --- test (smoke against rclone) -------------------------------------------


class TestRcloneSmokeTest:
    def test_test_succeeds_when_rclone_sees_remote(
        self, env, monkeypatch, capsys, sample_config_file
    ):
        _run_cli(
            ["cloud-remote", "add", "--from-file", str(sample_config_file)],
            monkeypatch, capsys,
        )

        # Patch the Rclone class as imported by the CLI.
        with patch("amg.cloud.rclone.Rclone") as RcloneMock:
            instance = RcloneMock.return_value
            instance.list_remotes.return_value = ["gdrive_amy"]
            rc, out = _run_cli(
                ["cloud-remote", "test", "gdrive_amy"],
                monkeypatch, capsys,
            )
        assert rc == 0
        assert "OK" in out

    def test_test_warns_when_remote_not_in_rclone_output(
        self, env, monkeypatch, capsys, sample_config_file
    ):
        _run_cli(
            ["cloud-remote", "add", "--from-file", str(sample_config_file)],
            monkeypatch, capsys,
        )

        with patch("amg.cloud.rclone.Rclone") as RcloneMock:
            instance = RcloneMock.return_value
            instance.list_remotes.return_value = ["something_else"]
            rc, out = _run_cli(
                ["cloud-remote", "test", "gdrive_amy"],
                monkeypatch, capsys,
            )
        assert rc == 1
        assert "Warning" in out

    def test_test_handles_rclone_not_installed(
        self, env, monkeypatch, capsys, sample_config_file
    ):
        from amg.cloud.rclone import RcloneNotFoundError

        _run_cli(
            ["cloud-remote", "add", "--from-file", str(sample_config_file)],
            monkeypatch, capsys,
        )
        with patch("amg.cloud.rclone.Rclone") as RcloneMock:
            instance = RcloneMock.return_value
            instance.list_remotes.side_effect = RcloneNotFoundError("not on PATH")
            rc, out = _run_cli(
                ["cloud-remote", "test", "gdrive_amy"],
                monkeypatch, capsys,
            )
        assert rc == 1
        assert "not on PATH" in out

    def test_test_handles_missing_remote(self, env, monkeypatch, capsys):
        rc, out = _run_cli(
            ["cloud-remote", "test", "phantom"],
            monkeypatch, capsys,
        )
        assert rc == 1
        assert "phantom" in out
