"""
Unit tests for :mod:`amg.cloud.credentials`.

These tests cover the storage layer end-to-end (real SQLite, real Fernet)
plus the failure modes the operator is most likely to hit: missing key,
rotated key, malformed config paste, double-add, missing remote on get.

The materialize_config() tests verify both the contents and the on-disk
permissions, since "secret on a 0644 temp file" would silently undermine
the entire encryption story.
"""
from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from typing import Iterator

import pytest
from cryptography.fernet import Fernet

from amg.cloud import credentials as cred_mod
from amg.cloud.credentials import (
    CredentialConfigInvalidError,
    CredentialDecryptError,
    CredentialKeyInvalidError,
    CredentialKeyMissingError,
    CredentialNotFoundError,
    CredentialStore,
    RemoteRecord,
    _parse_remote_metadata,
    _resolve_db_path,
    _resolve_key,
)


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def fernet_key(monkeypatch) -> str:
    """Set AMG_CREDENTIALS_KEY to a fresh Fernet key for the duration of
    the test. Most tests need this; encryption is a dependency."""
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("AMG_CREDENTIALS_KEY", key)
    return key


@pytest.fixture
def store(tmp_path, fernet_key) -> Iterator[CredentialStore]:
    """A CredentialStore backed by a fresh per-test SQLite DB."""
    db = tmp_path / "credentials.sqlite"
    yield CredentialStore(db_path=db)


@pytest.fixture
def sample_config() -> str:
    return (
        "[gdrive_amy]\n"
        "type = drive\n"
        "client_id = abc123\n"
        "client_secret = sekrit\n"
        "scope = drive\n"
        "token = {\"access_token\":\"ya29.fake\",\"refresh_token\":\"1//fake\"}\n"
    )


# --- parse_remote_metadata -------------------------------------------------


class TestParseRemoteMetadata:
    def test_parses_name_and_kind(self, sample_config):
        name, kind = _parse_remote_metadata(sample_config)
        assert name == "gdrive_amy"
        assert kind == "drive"

    def test_handles_leading_blank_lines(self):
        text = "\n\n[mega_main]\ntype = mega\nuser = amy\npass = enc:foo\n"
        assert _parse_remote_metadata(text) == ("mega_main", "mega")

    def test_tolerates_whitespace_around_equals(self):
        text = "[dropbox_amy]\ntype   =   dropbox  \ntoken = {}\n"
        assert _parse_remote_metadata(text) == ("dropbox_amy", "dropbox")

    def test_missing_section_header_raises(self):
        with pytest.raises(CredentialConfigInvalidError, match="section"):
            _parse_remote_metadata("type = drive\ntoken = {}\n")

    def test_missing_type_raises(self):
        with pytest.raises(CredentialConfigInvalidError, match="type"):
            _parse_remote_metadata("[gdrive_amy]\nclient_id = abc\n")


# --- key resolution --------------------------------------------------------


class TestResolveKey:
    def test_missing_env_var_raises(self, monkeypatch):
        monkeypatch.delenv("AMG_CREDENTIALS_KEY", raising=False)
        with pytest.raises(CredentialKeyMissingError):
            _resolve_key()

    def test_empty_env_var_raises(self, monkeypatch):
        monkeypatch.setenv("AMG_CREDENTIALS_KEY", "   ")
        with pytest.raises(CredentialKeyMissingError):
            _resolve_key()

    def test_invalid_key_raises(self, monkeypatch):
        monkeypatch.setenv("AMG_CREDENTIALS_KEY", "not-a-fernet-key")
        with pytest.raises(CredentialKeyInvalidError):
            _resolve_key()

    def test_valid_key_returned_as_bytes(self, monkeypatch):
        key = Fernet.generate_key().decode("ascii")
        monkeypatch.setenv("AMG_CREDENTIALS_KEY", key)
        out = _resolve_key()
        assert isinstance(out, bytes)
        assert out == key.encode("ascii")


# --- db path resolution ----------------------------------------------------


class TestResolveDbPath:
    def test_explicit_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AMG_CREDENTIALS_DB", "/should/not/win.sqlite")
        explicit = tmp_path / "wins.sqlite"
        assert _resolve_db_path(explicit) == explicit.resolve()

    def test_env_var_used(self, tmp_path, monkeypatch):
        target = tmp_path / "from_env.sqlite"
        monkeypatch.setenv("AMG_CREDENTIALS_DB", str(target))
        assert _resolve_db_path() == target.resolve()

    def test_falls_back_to_data_dir(self, monkeypatch):
        monkeypatch.delenv("AMG_CREDENTIALS_DB", raising=False)
        out = _resolve_db_path()
        assert out.name == cred_mod.DEFAULT_DB_FILENAME


# --- add / get round trip --------------------------------------------------


class TestAddAndGet:
    def test_round_trip(self, store, sample_config):
        record = store.add_remote(sample_config, notes="amy's drive")
        assert record.name == "gdrive_amy"
        assert record.kind == "drive"
        assert record.notes == "amy's drive"
        assert record.last_used_at is None

        got = store.get_remote("gdrive_amy")
        assert got == sample_config

    def test_get_missing_raises(self, store):
        with pytest.raises(CredentialNotFoundError):
            store.get_remote("does_not_exist")

    def test_double_add_without_force_raises_integrity(self, store, sample_config):
        store.add_remote(sample_config)
        with pytest.raises(sqlite3.IntegrityError):
            store.add_remote(sample_config)

    def test_double_add_with_force_overwrites(self, store, sample_config):
        store.add_remote(sample_config, notes="first")
        new_text = sample_config.replace("ya29.fake", "ya29.refreshed")
        store.add_remote(new_text, notes="second", overwrite=True)
        assert store.get_remote("gdrive_amy") == new_text
        records = store.list_remotes()
        assert len(records) == 1
        assert records[0].notes == "second"
        # Overwrite resets last_used_at so timing data isn't misleading.
        assert records[0].last_used_at is None

    def test_invalid_config_raises(self, store):
        with pytest.raises(CredentialConfigInvalidError):
            store.add_remote("just some random text")

    def test_add_without_key_raises(self, store, monkeypatch, sample_config):
        monkeypatch.delenv("AMG_CREDENTIALS_KEY", raising=False)
        with pytest.raises(CredentialKeyMissingError):
            store.add_remote(sample_config)

    def test_get_with_rotated_key_raises_decrypt_error(
        self, store, monkeypatch, sample_config
    ):
        store.add_remote(sample_config)
        # Rotate the env var to a fresh, unrelated Fernet key.
        new_key = Fernet.generate_key().decode("ascii")
        monkeypatch.setenv("AMG_CREDENTIALS_KEY", new_key)
        with pytest.raises(CredentialDecryptError):
            store.get_remote("gdrive_amy")


# --- list / delete (work without a key) ------------------------------------


class TestListAndDelete:
    def test_list_returns_metadata_only(self, store, sample_config):
        store.add_remote(sample_config, notes="alpha")
        store.add_remote(
            sample_config.replace("[gdrive_amy]", "[mega_main]")
                         .replace("type = drive", "type = mega"),
            notes="beta",
        )
        records = store.list_remotes()
        assert [r.name for r in records] == ["gdrive_amy", "mega_main"]
        assert all(isinstance(r, RemoteRecord) for r in records)

    def test_list_works_without_key(self, store, monkeypatch, sample_config):
        store.add_remote(sample_config)
        # Rotate key away; list must still work (no decrypt happens).
        monkeypatch.delenv("AMG_CREDENTIALS_KEY", raising=False)
        records = store.list_remotes()
        assert len(records) == 1
        assert records[0].name == "gdrive_amy"

    def test_delete_returns_true_when_row_existed(self, store, sample_config):
        store.add_remote(sample_config)
        assert store.delete_remote("gdrive_amy") is True
        assert store.list_remotes() == []

    def test_delete_returns_false_when_missing(self, store):
        assert store.delete_remote("nope") is False

    def test_delete_works_without_key(self, store, monkeypatch, sample_config):
        store.add_remote(sample_config)
        monkeypatch.delenv("AMG_CREDENTIALS_KEY", raising=False)
        assert store.delete_remote("gdrive_amy") is True


# --- mark_used --------------------------------------------------------------


class TestMarkUsed:
    def test_bumps_last_used(self, store, sample_config):
        store.add_remote(sample_config)
        assert store.list_remotes()[0].last_used_at is None
        store.mark_used("gdrive_amy")
        assert store.list_remotes()[0].last_used_at is not None

    def test_silently_ignores_missing_remote(self, store):
        # Best-effort metric: a deleted-mid-job remote shouldn't crash.
        store.mark_used("phantom")  # must not raise


# --- materialize_config ----------------------------------------------------


class TestMaterializeConfig:
    def test_writes_combined_config(self, store, sample_config):
        store.add_remote(sample_config)
        store.add_remote(
            sample_config.replace("[gdrive_amy]", "[gdrive_other]"),
            notes="other",
        )
        with store.materialize_config(names=["gdrive_amy", "gdrive_other"]) as cfg_path:
            text = cfg_path.read_text(encoding="utf-8")
            assert "[gdrive_amy]" in text
            assert "[gdrive_other]" in text
            assert "ya29.fake" in text

    def test_temp_file_is_0600(self, store, sample_config):
        store.add_remote(sample_config)
        with store.materialize_config(names=["gdrive_amy"]) as cfg_path:
            mode = stat.S_IMODE(os.stat(cfg_path).st_mode)
            assert mode == 0o600

    def test_temp_file_cleaned_up_on_normal_exit(self, store, sample_config):
        store.add_remote(sample_config)
        captured: dict[str, Path] = {}
        with store.materialize_config(names=["gdrive_amy"]) as cfg_path:
            captured["path"] = cfg_path
            assert cfg_path.exists()
        assert not captured["path"].exists()

    def test_temp_file_cleaned_up_on_exception(self, store, sample_config):
        store.add_remote(sample_config)
        captured: dict[str, Path] = {}
        with pytest.raises(RuntimeError):
            with store.materialize_config(names=["gdrive_amy"]) as cfg_path:
                captured["path"] = cfg_path
                raise RuntimeError("boom")
        assert not captured["path"].exists()

    def test_default_names_uses_all_remotes(self, store, sample_config):
        store.add_remote(sample_config)
        store.add_remote(sample_config.replace("[gdrive_amy]", "[mega_main]")
                                       .replace("type = drive", "type = mega"))
        with store.materialize_config() as cfg_path:
            text = cfg_path.read_text(encoding="utf-8")
            assert "[gdrive_amy]" in text
            assert "[mega_main]" in text

    def test_materialize_marks_remotes_used(self, store, sample_config):
        store.add_remote(sample_config)
        with store.materialize_config(names=["gdrive_amy"]):
            pass
        assert store.list_remotes()[0].last_used_at is not None


# --- on-disk db permissions ------------------------------------------------


class TestDbPermissions:
    def test_db_file_is_0600_on_unix(self, tmp_path, fernet_key):
        if os.name != "posix":
            pytest.skip("permissions check is POSIX-only")
        db = tmp_path / "perms.sqlite"
        CredentialStore(db_path=db)
        mode = stat.S_IMODE(os.stat(db).st_mode)
        assert mode == 0o600
