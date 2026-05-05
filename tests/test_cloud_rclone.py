"""
Unit tests for :mod:`amg.cloud.rclone`.

The rclone CLI is mocked at the ``shutil.which`` / ``subprocess.run`` /
``subprocess.Popen`` boundary — we don't need a real binary to verify
that argument construction, JSON parsing, progress parsing, and error
propagation work correctly. End-to-end smoke testing against a real
rclone install belongs in the Phase 1 smoke-test runbook, not here.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from amg.cloud import rclone as rclone_mod
from amg.cloud.rclone import (
    Rclone,
    RcloneCommandError,
    RcloneError,
    RcloneNotFoundError,
    _parse_progress,
)


# --- helpers ----------------------------------------------------------------


def _mk_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.stdout = stdout
    cp.stderr = stderr
    cp.returncode = returncode
    return cp


# --- progress parsing -------------------------------------------------------


class TestParseProgress:
    def test_parses_standard_stats_line(self):
        line = "Transferred:   500.0 MiB / 1.000 GiB, 50%, 50 MiBps, ETA 10s"
        stats = _parse_progress(line)
        assert stats == {"done": "500.0 MiB", "total": "1.000 GiB", "pct": 50}

    def test_parses_zero_progress(self):
        line = "Transferred:           0 / 1.500 GiB, 0%, 0 Bps, ETA -"
        stats = _parse_progress(line)
        assert stats is not None
        assert stats["pct"] == 0

    def test_parses_complete(self):
        line = "Transferred:   1.000 GiB / 1.000 GiB, 100%, 0 Bps, ETA 0s"
        stats = _parse_progress(line)
        assert stats is not None
        assert stats["pct"] == 100

    def test_returns_none_for_unrelated_line(self):
        assert _parse_progress("INFO  : something happened") is None
        assert _parse_progress("") is None
        assert _parse_progress("Error: nope") is None


# --- binary discovery -------------------------------------------------------


class TestBinaryDiscovery:
    def test_raises_when_binary_missing(self):
        with patch.object(rclone_mod.shutil, "which", return_value=None):
            with pytest.raises(RcloneNotFoundError):
                Rclone().version()

    def test_uses_env_override(self, monkeypatch):
        monkeypatch.setenv("AMG_RCLONE_BINARY", "/opt/custom/rclone")
        with patch.object(rclone_mod.shutil, "which", return_value="/opt/custom/rclone") as which_mock:
            with patch.object(rclone_mod.subprocess, "run", return_value=_mk_completed("rclone v1.74.0")):
                Rclone().version()
                # which() must have been called with the env-supplied path.
                assert which_mock.call_args[0][0] == "/opt/custom/rclone"

    def test_constructor_arg_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("AMG_RCLONE_BINARY", "/opt/custom/rclone")
        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone") as which_mock:
            with patch.object(rclone_mod.subprocess, "run", return_value=_mk_completed("rclone v1.74.0")):
                Rclone(binary="/usr/bin/rclone").version()
                assert which_mock.call_args[0][0] == "/usr/bin/rclone"


# --- version() -------------------------------------------------------------


class TestVersion:
    def test_extracts_version_string(self):
        stdout = "rclone v1.74.0\n- os/version: linux\n- os/kernel: 6.1\n"
        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with patch.object(rclone_mod.subprocess, "run", return_value=_mk_completed(stdout)) as run_mock:
                info = Rclone().version()
                assert info["version"] == "v1.74.0"
                # --check=false is in the cmd so "version --check" doesn't try to
                # phone home from a sandboxed pod.
                cmd = run_mock.call_args[0][0]
                assert "--check=false" in cmd


# --- list_remotes() ---------------------------------------------------------


class TestListRemotes:
    def test_strips_trailing_colon(self):
        stdout = "gdrive_amy:\nmega_main:\ndropbox_amy:\n"
        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with patch.object(rclone_mod.subprocess, "run", return_value=_mk_completed(stdout)):
                assert Rclone().list_remotes() == ["gdrive_amy", "mega_main", "dropbox_amy"]

    def test_empty(self):
        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with patch.object(rclone_mod.subprocess, "run", return_value=_mk_completed("")):
                assert Rclone().list_remotes() == []


# --- lsjson() --------------------------------------------------------------


class TestLsjson:
    def _entries(self) -> List[Dict[str, Any]]:
        return [
            {"Name": "scene4.mp4", "Path": "incoming/scene4.mp4", "Size": 1024, "IsDir": False, "MimeType": "video/mp4"},
            {"Name": "subdir", "Path": "incoming/subdir", "Size": 0, "IsDir": True, "MimeType": "inode/directory"},
            {"Name": "notes.txt", "Path": "incoming/notes.txt", "Size": 100, "IsDir": False, "MimeType": "text/plain"},
        ]

    def test_returns_parsed_entries(self):
        stdout = json.dumps(self._entries())
        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with patch.object(rclone_mod.subprocess, "run", return_value=_mk_completed(stdout)) as run_mock:
                entries = Rclone().lsjson("gdrive_amy", "incoming")
                assert len(entries) == 3
                cmd = run_mock.call_args[0][0]
                assert "lsjson" in cmd
                assert "gdrive_amy:incoming" in cmd
                assert "--max-depth=1" in cmd

    def test_videos_only_filters_non_videos_but_keeps_dirs(self):
        stdout = json.dumps(self._entries())
        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with patch.object(rclone_mod.subprocess, "run", return_value=_mk_completed(stdout)):
                entries = Rclone().lsjson("gdrive_amy", "incoming", videos_only=True)
                names = sorted(e["Name"] for e in entries)
                assert names == ["scene4.mp4", "subdir"]

    def test_rejects_remote_with_colon(self):
        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with pytest.raises(ValueError):
                Rclone().lsjson("gdrive_amy:")

    def test_invalid_json_raises(self):
        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with patch.object(rclone_mod.subprocess, "run", return_value=_mk_completed("not json")):
                with pytest.raises(RcloneError):
                    Rclone().lsjson("gdrive_amy", "incoming")

    def test_strips_leading_slash(self):
        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with patch.object(rclone_mod.subprocess, "run", return_value=_mk_completed("[]")) as run_mock:
                Rclone().lsjson("gdrive_amy", "/incoming")
                cmd = run_mock.call_args[0][0]
                assert "gdrive_amy:incoming" in cmd
                assert "gdrive_amy:/incoming" not in cmd


# --- error propagation -----------------------------------------------------


class TestErrorPropagation:
    def test_nonzero_exit_raises_command_error(self):
        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with patch.object(
                rclone_mod.subprocess,
                "run",
                return_value=_mk_completed(stdout="", stderr="auth failure", returncode=3),
            ):
                with pytest.raises(RcloneCommandError) as excinfo:
                    Rclone().list_remotes()
                assert excinfo.value.returncode == 3
                assert "auth failure" in excinfo.value.stderr

    def test_timeout_raises_rclone_error(self):
        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with patch.object(
                rclone_mod.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd="rclone", timeout=5),
            ):
                with pytest.raises(RcloneError) as excinfo:
                    Rclone().list_remotes()
                assert "timed out" in str(excinfo.value)


# --- copy() ----------------------------------------------------------------


class _FakePopen:
    """Minimal Popen stand-in. ``stdout`` is iterated like a file; ``wait``
    returns the configured returncode."""

    def __init__(self, lines: List[str], returncode: int = 0) -> None:
        self.stdout = iter(line if line.endswith("\n") else line + "\n" for line in lines)
        self._returncode = returncode
        self.killed = False

    def wait(self) -> int:
        return self._returncode

    def kill(self) -> None:
        self.killed = True


class TestCopy:
    def test_streams_progress_and_logs(self, tmp_path):
        lines = [
            "INFO  : Starting copy",
            "Transferred:   100 MiB / 1.000 GiB, 10%, 50 MiBps, ETA 18s",
            "Transferred:   500 MiB / 1.000 GiB, 50%, 50 MiBps, ETA 10s",
            "Transferred:   1.000 GiB / 1.000 GiB, 100%, 0 Bps, ETA 0s",
            "INFO  : copied 1 file",
        ]
        fake = _FakePopen(lines, returncode=0)
        progress: List[Dict[str, Any]] = []
        logs: List[str] = []

        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with patch.object(rclone_mod.subprocess, "Popen", return_value=fake) as popen_mock:
                Rclone().copy(
                    "gdrive_amy:incoming/scene4.mp4",
                    tmp_path / "out",
                    on_progress=progress.append,
                    on_log=logs.append,
                )

        assert [p["pct"] for p in progress] == [10, 50, 100]
        assert any("Starting copy" in line for line in logs)
        assert any("copied 1 file" in line for line in logs)
        # Destination directory was created.
        assert (tmp_path / "out").is_dir()
        # The constructed command included --progress + stats flags.
        cmd = popen_mock.call_args[0][0]
        assert "--progress" in cmd
        assert "--stats-one-line" in cmd
        assert "copy" in cmd
        assert "gdrive_amy:incoming/scene4.mp4" in cmd
        assert str(tmp_path / "out") in cmd

    def test_callback_exception_does_not_kill_transfer(self, tmp_path):
        lines = ["Transferred:   500 MiB / 1.000 GiB, 50%, 50 MiBps, ETA 10s"]
        fake = _FakePopen(lines, returncode=0)

        def bad_callback(_):
            raise RuntimeError("UI thread crashed")

        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with patch.object(rclone_mod.subprocess, "Popen", return_value=fake):
                Rclone().copy(
                    "gdrive_amy:scene.mp4",
                    tmp_path / "out",
                    on_progress=bad_callback,
                )
        # If the exception escaped, we'd never get here.

    def test_nonzero_exit_raises(self, tmp_path):
        fake = _FakePopen(["INFO  : permission denied"], returncode=1)
        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with patch.object(rclone_mod.subprocess, "Popen", return_value=fake):
                with pytest.raises(RcloneCommandError):
                    Rclone().copy("gdrive_amy:scene.mp4", tmp_path / "out")


# --- validate_config() -----------------------------------------------------


class TestValidateConfig:
    def test_valid_config_returns_true(self):
        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with patch.object(
                rclone_mod.subprocess, "run",
                return_value=_mk_completed(stdout="gdrive_amy:\n", returncode=0),
            ):
                assert Rclone().validate_config("[gdrive_amy]\ntype = drive\n") is True

    def test_invalid_config_returns_false(self):
        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with patch.object(
                rclone_mod.subprocess, "run",
                return_value=_mk_completed(stdout="", stderr="malformed", returncode=1),
            ):
                assert Rclone().validate_config("garbage{{{") is False

    def test_temp_config_file_cleaned_up(self):
        captured: Dict[str, str] = {}

        def fake_run(cmd, **_kwargs):
            # Capture config path so we can verify cleanup.
            for i, tok in enumerate(cmd):
                if tok == "--config" and i + 1 < len(cmd):
                    captured["path"] = cmd[i + 1]
            return _mk_completed(returncode=0)

        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with patch.object(rclone_mod.subprocess, "run", side_effect=fake_run):
                Rclone().validate_config("[gdrive_amy]\ntype = drive\n")

        assert "path" in captured
        assert not Path(captured["path"]).exists(), "temp config should be deleted"


# --- config_path threading -------------------------------------------------


class TestConfigPath:
    def test_config_path_added_to_commands(self, tmp_path):
        cfg = tmp_path / "rclone.conf"
        cfg.write_text("[noop]\n")
        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with patch.object(rclone_mod.subprocess, "run", return_value=_mk_completed("")) as run_mock:
                Rclone(config_path=cfg).list_remotes()
                cmd = run_mock.call_args[0][0]
                assert "--config" in cmd
                assert str(cfg) in cmd

    def test_no_config_path_means_no_flag(self):
        with patch.object(rclone_mod.shutil, "which", return_value="/usr/bin/rclone"):
            with patch.object(rclone_mod.subprocess, "run", return_value=_mk_completed("")) as run_mock:
                Rclone().list_remotes()
                cmd = run_mock.call_args[0][0]
                assert "--config" not in cmd
