"""
Thin Python wrapper around the ``rclone`` CLI.

This is the foundation for the Phase 2 cloud-storage transfer flow:

* The controller VM uses ``Rclone.lsjson`` to back the UI source picker
  (operator browses their Google Drive / Dropbox / Mega tree in the
  browser without uploading anything).
* The pod uses ``Rclone.copy`` to pull the chosen file directly from the
  cloud-storage CDN at datacenter-to-datacenter speed, replacing the
  current "operator's browser uploads to controller, controller forwards
  to pod" path that's slow over residential internet.

Why a CLI wrapper instead of a native API client per provider:

* rclone supports 70+ backends (Drive, Dropbox, Mega, S3, B2, …) with one
  surface area. Adding a new provider becomes "operator runs `rclone
  authorize <provider>` and pastes the resulting config" — no new code on
  our side.
* The OAuth flows for Drive / Dropbox / OneDrive are nontrivial and
  rclone already handles them correctly, including refresh tokens and
  token rotation. Reimplementing them per-provider would be 1k+ lines of
  fragile code we'd then have to maintain.
* Mega has no OAuth at all and the only reasonable Python clients
  (mega.py, megapy) are unmaintained or rely on reversed unofficial APIs.
  rclone's Mega backend is actively maintained.

Trade-off accepted: rclone has to be on PATH (or pointed at via
``binary``). Our Dockerfile installs a pinned static binary into
``/usr/local/bin/rclone`` so this is satisfied in the cloud edition.
For local dev, the operator can ``brew install rclone``.

Auth/credentials:

* All credentials live in an rclone config file, not in env vars. Each
  remote is a section like ``[gdrive_amy]`` containing the OAuth tokens
  or username/password.
* Phase 2 commit 2 will add encrypted-at-rest storage for the config
  file in ``data/credentials.sqlite`` and a CLI/UI to add/list/remove
  remotes. This module just takes the config_path as a parameter and
  trusts the caller to manage encryption.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from amg.utils.logging import get_logger

log = get_logger("amg.cloud.rclone")


DEFAULT_BINARY = "rclone"
DEFAULT_LSJSON_TIMEOUT_SEC = 60.0
DEFAULT_COMMAND_TIMEOUT_SEC = 30.0

# Video extensions mirrored from amg.ingest.inventory.VIDEO_EXTENSIONS so
# the source picker can default to "show me videos only" without dragging
# in the ingest module (which pulls in opencv etc. and is overkill for a
# simple file-list filter).
_VIDEO_EXTENSIONS = (
    ".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".wmv", ".flv",
)


# --- exceptions -------------------------------------------------------------


class RcloneError(Exception):
    """Base class for rclone failures."""


class RcloneNotFoundError(RcloneError):
    """The rclone binary isn't on PATH (or at the configured location)."""


class RcloneCommandError(RcloneError):
    """rclone exited non-zero. Carries returncode + stderr for debugging."""

    def __init__(self, returncode: int, stderr: str, cmd: List[str]) -> None:
        super().__init__(
            f"rclone exited {returncode} for command {cmd!r}: {stderr.strip()[:500]}"
        )
        self.returncode = returncode
        self.stderr = stderr
        self.cmd = cmd


# --- progress parsing -------------------------------------------------------


# Format from `rclone copy --progress --stats-one-line --stats=2s`:
#   Transferred:        500.0 MiB / 1.000 GiB, 50%, 50 MiBps, ETA 10s
_PROGRESS_RE = re.compile(
    r"Transferred:\s+(?P<done>[^,]+?)\s*/\s*(?P<total>[^,]+?),\s*(?P<pct>\d+)%"
)


def _parse_progress(line: str) -> Optional[Dict[str, Any]]:
    """Parse one ``--stats-one-line`` status line. Returns None if line is
    not a stats line (rclone interleaves info/error lines)."""
    m = _PROGRESS_RE.search(line)
    if not m:
        return None
    return {
        "done": m.group("done").strip(),
        "total": m.group("total").strip(),
        "pct": int(m.group("pct")),
    }


# --- main wrapper -----------------------------------------------------------


class Rclone:
    """Thin subprocess wrapper around rclone.

    Construction is cheap; the binary is only invoked when a method runs.
    Pass an explicit ``config_path`` if you want to use a non-default
    rclone config (typically ``data/rclone.conf`` written from the
    encrypted credential store at job time)."""

    def __init__(
        self,
        binary: Optional[str] = None,
        config_path: Optional[Path] = None,
    ) -> None:
        env_binary = os.environ.get("AMG_RCLONE_BINARY", "").strip()
        self._binary = binary or env_binary or DEFAULT_BINARY
        self._config_path = Path(config_path) if config_path else None

    # --- internals ---

    def _which(self) -> str:
        resolved = shutil.which(self._binary)
        if resolved is None:
            raise RcloneNotFoundError(
                f"rclone binary not found (looked for {self._binary!r}). "
                "Install with: brew install rclone (Mac) / apt install rclone (Debian) "
                "or use the AMG Docker image which bundles a pinned version. "
                "Override via AMG_RCLONE_BINARY env var if it lives elsewhere."
            )
        return resolved

    def _base_cmd(self) -> List[str]:
        cmd = [self._which()]
        if self._config_path is not None:
            cmd += ["--config", str(self._config_path)]
        return cmd

    def _run(
        self,
        args: List[str],
        *,
        timeout: float = DEFAULT_COMMAND_TIMEOUT_SEC,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        cmd = self._base_cmd() + args
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RcloneError(
                f"rclone command timed out after {timeout}s: {cmd!r}"
            ) from exc
        if check and result.returncode != 0:
            raise RcloneCommandError(result.returncode, result.stderr, cmd)
        return result

    # --- public api ---

    def version(self) -> Dict[str, str]:
        """Return ``{version: 'v1.74.0', go_version: 'go1.23.4', ...}``.

        Useful as a smoke test that rclone is wired up correctly."""
        result = self._run(["version", "--check=false"], timeout=DEFAULT_COMMAND_TIMEOUT_SEC)
        out: Dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "-" in line and ":" in line:
                key, _, val = line.partition(":")
                out[key.strip().lower().replace(" ", "_")] = val.strip()
        # First non-empty line typically is "rclone v1.74.0".
        first = (result.stdout.splitlines() or [""])[0].strip()
        m = re.search(r"v\d+\.\d+\.\d+", first)
        if m:
            out["version"] = m.group(0)
        return out

    def list_remotes(self) -> List[str]:
        """Return the names of configured remotes (e.g. ``['gdrive_amy', 'mega_main']``)."""
        result = self._run(["listremotes"], timeout=DEFAULT_COMMAND_TIMEOUT_SEC)
        # Lines look like 'gdrive_amy:' — strip trailing colon.
        return [line.rstrip(":") for line in result.stdout.splitlines() if line.strip()]

    def lsjson(
        self,
        remote: str,
        path: str = "",
        *,
        max_depth: int = 1,
        videos_only: bool = False,
        timeout: float = DEFAULT_LSJSON_TIMEOUT_SEC,
    ) -> List[Dict[str, Any]]:
        """List entries at ``<remote>:<path>``. Returns parsed lsjson output.

        Each entry has at least ``Name``, ``Path``, ``Size``, ``IsDir``,
        ``MimeType``. ``videos_only`` filters to extensions in the AMG
        video set so the source picker can default to scenes."""
        if ":" in remote:
            raise ValueError("pass remote name without trailing colon (e.g. 'gdrive_amy')")
        target = f"{remote}:{path.lstrip('/')}"
        args = ["lsjson", target, f"--max-depth={int(max_depth)}"]
        result = self._run(args, timeout=timeout)
        try:
            entries: List[Dict[str, Any]] = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RcloneError(f"lsjson returned non-JSON output: {exc}") from exc
        if videos_only:
            entries = [
                e for e in entries
                if e.get("IsDir") or
                Path(e.get("Name", "")).suffix.lower() in _VIDEO_EXTENSIONS
            ]
        return entries

    def copy(
        self,
        src: str,
        dst: Path,
        *,
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        stats_interval_sec: int = 2,
        timeout: float = 4 * 60 * 60,
    ) -> None:
        """Copy ``src`` (e.g. ``'gdrive_amy:Amy/incoming/scene4.mp4'``) to a
        local directory ``dst``. Streams progress lines through
        ``on_progress`` (parsed dict) and other rclone log lines through
        ``on_log`` (raw string).

        rclone's copy is idempotent and resumable (skips files that already
        exist at the destination with matching size + mtime). For our flow,
        the destination is a fresh per-job directory so there's nothing to
        skip — but the property is nice if a future commit reuses warm pods."""
        dst = Path(dst)
        dst.mkdir(parents=True, exist_ok=True)
        cmd = self._base_cmd() + [
            "copy",
            src,
            str(dst),
            "--progress",
            "--stats-one-line",
            f"--stats={int(stats_interval_sec)}s",
        ]
        log.info(f"rclone copy: {src} -> {dst}")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        # Watchdog so a wedged transfer can't hang the worker forever.
        watchdog = threading.Timer(timeout, proc.kill)
        watchdog.daemon = True
        watchdog.start()
        try:
            for raw in proc.stdout or []:
                line = raw.rstrip("\n")
                if not line:
                    continue
                stats = _parse_progress(line)
                if stats is not None:
                    if on_progress is not None:
                        try:
                            on_progress(stats)
                        except Exception:  # noqa: BLE001 - hooks must not kill the transfer
                            pass
                else:
                    if on_log is not None:
                        try:
                            on_log(line)
                        except Exception:  # noqa: BLE001
                            pass
            rc = proc.wait()
        finally:
            watchdog.cancel()
        if rc != 0:
            raise RcloneCommandError(rc, "(streamed to on_log)", cmd)

    def cat(
        self,
        src: str,
        *,
        chunk_size: int = 1024 * 1024,
        timeout: float = 4 * 60 * 60,
    ):
        """Stream a remote file's bytes via ``rclone cat``.

        The caller consumes the returned generator. No full local copy is
        created, which keeps controller-side source-video downloads explicit
        and low disk impact.
        """
        cmd = self._base_cmd() + ["cat", src]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        watchdog = threading.Timer(timeout, proc.kill)
        watchdog.daemon = True
        watchdog.start()

        def _chunks():
            try:
                while True:
                    chunk = proc.stdout.read(chunk_size) if proc.stdout else b""
                    if not chunk:
                        break
                    yield chunk
                _stdout, stderr = proc.communicate()
                if proc.returncode != 0:
                    err = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else str(stderr)
                    raise RcloneCommandError(proc.returncode or 1, err, cmd)
            finally:
                watchdog.cancel()
                if proc.poll() is None:
                    proc.kill()

        return _chunks()

    def validate_config(self, config_text: str) -> bool:
        """Write ``config_text`` to a temp file and run ``rclone listremotes``
        against it. Returns True if rclone parses the config without error.

        Used by the credential store to refuse to save a malformed paste."""
        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".conf",
            delete=False,
        ) as tmp:
            tmp.write(config_text)
            tmp_path = tmp.name
        try:
            cmd = [self._which(), "--config", tmp_path, "listremotes"]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=DEFAULT_COMMAND_TIMEOUT_SEC,
                check=False,
            )
            return result.returncode == 0
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


__all__ = [
    "DEFAULT_BINARY",
    "Rclone",
    "RcloneCommandError",
    "RcloneError",
    "RcloneNotFoundError",
]
