"""
Encrypted-at-rest credential store for rclone remotes.

The cloud edition needs to store rclone configurations (each holding
Drive/Dropbox OAuth refresh tokens or Mega username+password) somewhere
the controller VM can read at job-dispatch time. Plain text on disk is
unacceptable -- a compromised controller would leak Amy's permanent
access to her cloud storage.

Design:

* Single SQLite file at ``data/credentials.sqlite`` (env-overridable via
  ``AMG_CREDENTIALS_DB``). One row per rclone remote.
* Each row stores the rclone config section (e.g. the ``[gdrive_amy]``
  block including ``type = drive`` and ``token = {...}``) encrypted with
  Fernet (AES-128-CBC + HMAC-SHA256). The plaintext never hits disk.
* The Fernet key lives in env var ``AMG_CREDENTIALS_KEY`` and MUST be
  provided when reading or writing. We refuse to silently fall back to
  any unencrypted mode -- a missing key is an error, not a "store in the
  clear" hint.
* The ``kind`` field (drive/dropbox/mega/...) is parsed out of the
  config text rather than being a separate parameter the operator has
  to keep in sync with the section body.
* ``materialize_config(names=...)`` writes a temporary rclone.conf with
  ``mode 0600`` permissions, returns the path, and is the only place a
  decrypted secret lands on disk. Callers are expected to delete it
  promptly (use ``with materialize_config(...) as cfg_path:`` for
  guaranteed cleanup).

Threat model this addresses:

* Controller VM disk image leaks (snapshot exfiltration, dropped
  drive). Without ``AMG_CREDENTIALS_KEY``, the SQLite file is useless.
* Backup files copied to less-secure storage. Same property.

What this does NOT address (out of scope, separate hardening):

* Live RAM exfiltration on a running controller. The key is in process
  memory; if you're root on the box it's game over. Future hardening
  could move the key to a HashiCorp Vault sidecar.
* Operator pasting their token into an attacker-controlled instance
  (phishing). Mitigated by the auth gate + TLS-only deployment, not by
  this module.
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

from cryptography.fernet import Fernet, InvalidToken

from amg.utils.logging import get_logger

log = get_logger("amg.cloud.credentials")


DEFAULT_DB_FILENAME = "credentials.sqlite"

# Section header parser: matches ``[name]`` at the start of a line. The
# inner name is what becomes the rclone remote name and the SQLite PK.
_SECTION_HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$", re.MULTILINE)

# ``type = ...`` line parser. rclone is whitespace-tolerant around the
# equals sign so we tolerate it too.
_TYPE_LINE_RE = re.compile(r"^\s*type\s*=\s*(\S+)\s*$", re.MULTILINE)


# --- exceptions -------------------------------------------------------------


class CredentialStoreError(Exception):
    """Base class for credential-store failures."""


class CredentialKeyMissingError(CredentialStoreError):
    """``AMG_CREDENTIALS_KEY`` env var is unset or empty."""


class CredentialKeyInvalidError(CredentialStoreError):
    """The provided key is not a valid Fernet key (wrong length / bad b64)."""


class CredentialDecryptError(CredentialStoreError):
    """Stored ciphertext failed to decrypt -- usually a wrong/rotated key."""


class CredentialNotFoundError(CredentialStoreError):
    """No remote with that name exists in the store."""


class CredentialConfigInvalidError(CredentialStoreError):
    """Pasted config text is missing a section header or type line."""


# --- helpers ----------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_remote_metadata(config_text: str) -> tuple[str, str]:
    """Extract ``(remote_name, kind)`` from a pasted rclone section.

    Raises :class:`CredentialConfigInvalidError` if the text is missing
    a ``[name]`` header or a ``type = ...`` line."""
    section_match = _SECTION_HEADER_RE.search(config_text)
    if not section_match:
        raise CredentialConfigInvalidError(
            "config text does not contain a [section] header. "
            "Expected something like:\n  [gdrive_amy]\n  type = drive\n  ..."
        )
    type_match = _TYPE_LINE_RE.search(config_text)
    if not type_match:
        raise CredentialConfigInvalidError(
            "config text does not contain a 'type = ...' line. "
            "Every rclone remote needs one (e.g. 'type = drive')."
        )
    return section_match.group(1).strip(), type_match.group(1).strip()


def _resolve_key() -> bytes:
    raw = os.environ.get("AMG_CREDENTIALS_KEY", "").strip()
    if not raw:
        raise CredentialKeyMissingError(
            "AMG_CREDENTIALS_KEY is not set. Generate one with:\n"
            "  python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'\n"
            "Then add to the controller's env (and DON'T lose it -- a lost "
            "key means re-pasting every cloud-storage config)."
        )
    try:
        # Fernet validates length / urlsafe-base64 in the constructor.
        Fernet(raw.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise CredentialKeyInvalidError(
            f"AMG_CREDENTIALS_KEY is not a valid Fernet key: {exc}. "
            "Regenerate with Fernet.generate_key()."
        ) from exc
    return raw.encode("ascii")


def _resolve_db_path(explicit: Optional[Path] = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("AMG_CREDENTIALS_DB", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # Lazy import so this module can be imported in environments where
    # amg.config sets DATA_DIR via env vars that haven't been populated yet
    # (e.g. unit-test fixtures that monkeypatch).
    from amg.config import DATA_DIR
    return DATA_DIR / DEFAULT_DB_FILENAME


# --- dataclass --------------------------------------------------------------


@dataclass(frozen=True)
class RemoteRecord:
    """Public-safe view of a stored remote (no plaintext, no ciphertext)."""

    name: str
    kind: str
    notes: str
    created_at: str
    last_used_at: Optional[str]


# --- main api ---------------------------------------------------------------


class CredentialStore:
    """SQLite-backed encrypted store for rclone remote configs.

    Construction is cheap and does NOT validate the key (so callers can
    still call ``list_remotes()`` / ``delete_remote()`` after a key
    rotation if all they need is the names). Encryption-touching methods
    (``add_remote``, ``get_remote``, ``materialize_config``) lazily call
    :func:`_resolve_key` and raise on failure."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS remotes (
        name         TEXT PRIMARY KEY,
        kind         TEXT NOT NULL,
        ciphertext   BLOB NOT NULL,
        notes        TEXT NOT NULL DEFAULT '',
        created_at   TEXT NOT NULL,
        last_used_at TEXT
    )
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = _resolve_db_path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        # SQLite file should never be world-readable; tolerate a chmod
        # failure on platforms where it's a no-op.
        try:
            os.chmod(self._db_path, 0o600)
        except OSError:
            pass

    # --- internals ---

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(self.SCHEMA)

    # --- public api ---

    @property
    def db_path(self) -> Path:
        return self._db_path

    def add_remote(
        self,
        config_text: str,
        *,
        notes: str = "",
        overwrite: bool = False,
    ) -> RemoteRecord:
        """Encrypt and store ``config_text``.

        The remote's name and kind are parsed from the text itself --
        the operator does not pass them separately, eliminating the
        "name in CLI doesn't match [section] header" footgun.

        ``overwrite=False`` raises sqlite3.IntegrityError on conflict.
        Pass ``overwrite=True`` (e.g. from a CLI ``--force`` flag) to
        replace an existing entry, useful when an OAuth token expires
        and the operator re-runs ``rclone authorize``."""
        name, kind = _parse_remote_metadata(config_text)
        key = _resolve_key()
        ciphertext = Fernet(key).encrypt(config_text.encode("utf-8"))
        created_at = _utc_now_iso()
        with self._connect() as conn:
            if overwrite:
                conn.execute(
                    "INSERT INTO remotes (name, kind, ciphertext, notes, created_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET "
                    "  kind = excluded.kind, "
                    "  ciphertext = excluded.ciphertext, "
                    "  notes = excluded.notes, "
                    "  created_at = excluded.created_at, "
                    "  last_used_at = NULL",
                    (name, kind, ciphertext, notes, created_at),
                )
            else:
                conn.execute(
                    "INSERT INTO remotes (name, kind, ciphertext, notes, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (name, kind, ciphertext, notes, created_at),
                )
        log.info(f"credential store: added remote name={name} kind={kind}")
        return RemoteRecord(name=name, kind=kind, notes=notes, created_at=created_at, last_used_at=None)

    def get_remote(self, name: str) -> str:
        """Return the decrypted rclone config text for ``name``.

        Raises :class:`CredentialNotFoundError` if no such remote and
        :class:`CredentialDecryptError` on key mismatch (typically a
        rotated/lost key)."""
        key = _resolve_key()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ciphertext FROM remotes WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            raise CredentialNotFoundError(f"no remote named {name!r}")
        try:
            plaintext = Fernet(key).decrypt(row["ciphertext"])
        except InvalidToken as exc:
            raise CredentialDecryptError(
                f"failed to decrypt remote {name!r}. The AMG_CREDENTIALS_KEY may "
                "have been rotated; restore the original key or re-add the remote."
            ) from exc
        return plaintext.decode("utf-8")

    def list_remotes(self) -> List[RemoteRecord]:
        """Return metadata for all remotes. Does NOT touch ciphertext, so
        works without a valid key (useful for ``amg cloud-remote list``
        even after a key rotation)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, kind, notes, created_at, last_used_at "
                "FROM remotes ORDER BY name"
            ).fetchall()
        return [
            RemoteRecord(
                name=r["name"],
                kind=r["kind"],
                notes=r["notes"] or "",
                created_at=r["created_at"],
                last_used_at=r["last_used_at"],
            )
            for r in rows
        ]

    def delete_remote(self, name: str) -> bool:
        """Remove a remote. Returns True if a row was deleted, False if no
        such row existed. Does NOT touch ciphertext, so works without a
        valid key."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM remotes WHERE name = ?", (name,))
        deleted = cur.rowcount > 0
        if deleted:
            log.info(f"credential store: deleted remote name={name}")
        return deleted

    def mark_used(self, name: str) -> None:
        """Bump ``last_used_at`` to now. Does not raise if the remote was
        deleted between dispatch and bookkeeping (best-effort metric)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE remotes SET last_used_at = ? WHERE name = ?",
                (_utc_now_iso(), name),
            )

    @contextmanager
    def materialize_config(
        self,
        names: Optional[List[str]] = None,
    ) -> Iterator[Path]:
        """Write a temporary rclone.conf containing the requested remotes
        and yield the path. The file is created with mode 0600 in a
        per-call temp directory and is unlinked on context exit (whether
        normal or exception path).

        ``names=None`` materializes ALL stored remotes -- handy for the
        CLI ``test`` command. Production paths should pass an explicit
        list to limit blast radius if the temp file leaks."""
        if names is None:
            names = [r.name for r in self.list_remotes()]
        sections = []
        for name in names:
            sections.append(self.get_remote(name))
            self.mark_used(name)
        body = "\n\n".join(sections) + "\n"
        tmp_dir = Path(tempfile.mkdtemp(prefix="amg_rclone_", suffix=".d"))
        cfg_path = tmp_dir / "rclone.conf"
        try:
            # O_CREAT|O_EXCL with 0600 prevents a TOCTOU race on the temp
            # path and ensures no other user on the host can read the
            # decrypted config while it's on disk.
            fd = os.open(cfg_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fp:
                fp.write(body)
            yield cfg_path
        finally:
            try:
                cfg_path.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                tmp_dir.rmdir()
            except OSError:
                pass


__all__ = [
    "CredentialConfigInvalidError",
    "CredentialDecryptError",
    "CredentialKeyInvalidError",
    "CredentialKeyMissingError",
    "CredentialNotFoundError",
    "CredentialStore",
    "CredentialStoreError",
    "RemoteRecord",
]
