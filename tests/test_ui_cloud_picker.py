"""
Integration tests for the cloud-source picker UI.

Covers:
* /cloud renders with the configured remotes (or with a friendly empty
  state when none exist).
* /partials/cloud-browse calls Rclone.lsjson and renders both folders
  (clickable to drill down) and videos (clickable to submit).
* /partials/cloud-browse surfaces rclone errors as a visible warning
  rather than a 500.
* /jobs/cloud refuses unknown remotes (no useless queue entries) and
  queues a job with cloud_source set when the remote exists.
* /jobs/cloud returns HX-Redirect to / so the operator lands on the
  queue panel (which only exists on the Process page) rather than
  swapping queue HTML into the cloud-browser slot.
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


SAMPLE_RCLONE_CONFIG = (
    "[gdrive_amy]\n"
    "type = drive\n"
    "token = {\"access_token\":\"ya29.fake\"}\n"
)


@pytest.fixture
def ui_with_creds(tmp_path, monkeypatch):
    """Spin up a clean UI app with an isolated credentials.sqlite that
    already has a 'gdrive_amy' remote."""
    monkeypatch.setenv("AMG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AMG_CREDENTIALS_DB", str(tmp_path / "creds.sqlite"))
    monkeypatch.setenv("AMG_CREDENTIALS_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("AMG_AUTH_DISABLED", "1")

    import importlib
    import amg.config as cfg
    importlib.reload(cfg)
    import amg.cloud.credentials as creds_mod
    importlib.reload(creds_mod)
    import amg.ui.app as app_mod
    importlib.reload(app_mod)

    creds_mod.CredentialStore().add_remote(SAMPLE_RCLONE_CONFIG, notes="amy's drive")

    return TestClient(app_mod.create_app()), app_mod


@pytest.fixture
def ui_without_creds(tmp_path, monkeypatch):
    """UI app with NO remotes configured -- exercises the empty state."""
    monkeypatch.setenv("AMG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AMG_CREDENTIALS_DB", str(tmp_path / "creds.sqlite"))
    monkeypatch.setenv("AMG_CREDENTIALS_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("AMG_AUTH_DISABLED", "1")

    import importlib
    import amg.config as cfg
    importlib.reload(cfg)
    import amg.cloud.credentials as creds_mod
    importlib.reload(creds_mod)
    import amg.ui.app as app_mod
    importlib.reload(app_mod)

    return TestClient(app_mod.create_app()), app_mod


# --- /cloud (picker page) ---------------------------------------------------


class TestCloudPickerPage:
    def test_renders_remotes_when_present(self, ui_with_creds):
        client, _ = ui_with_creds
        r = client.get("/cloud")
        assert r.status_code == 200
        body = r.text
        assert "gdrive_amy" in body
        # Apostrophe in "amy's drive" gets HTML-escaped by Jinja autoescape.
        assert ("amy's drive" in body) or ("amy&#39;s drive" in body)
        # Each remote button targets the browse partial via HTMX.
        assert "hx-get=\"/partials/cloud-browse" in body

    def test_renders_empty_state_with_install_hint(self, ui_without_creds):
        client, _ = ui_without_creds
        r = client.get("/cloud")
        assert r.status_code == 200
        body = r.text
        assert "No remotes configured" in body
        assert "amg cloud-remote add" in body

    def test_missing_credentials_key_renders_friendly_error(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("AMG_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("AMG_CREDENTIALS_DB", str(tmp_path / "creds.sqlite"))
        monkeypatch.delenv("AMG_CREDENTIALS_KEY", raising=False)
        monkeypatch.setenv("AMG_AUTH_DISABLED", "1")

        import importlib
        import amg.config as cfg
        importlib.reload(cfg)
        import amg.cloud.credentials as creds_mod
        importlib.reload(creds_mod)
        import amg.ui.app as app_mod
        importlib.reload(app_mod)

        client = TestClient(app_mod.create_app())
        r = client.get("/cloud")
        # Page still renders -- no 500. The list_remotes() path doesn't
        # need the key, but the error message gets surfaced regardless so
        # the operator knows why no remotes appear in the picker.
        assert r.status_code == 200


# --- /partials/cloud-browse ------------------------------------------------


class TestCloudBrowsePartial:
    def _stub_rclone(self, monkeypatch, entries):
        from amg.cloud import rclone as rclone_mod

        instance = MagicMock()
        instance.lsjson.return_value = entries
        rclone_cls_mock = MagicMock(return_value=instance)
        monkeypatch.setattr(rclone_mod, "Rclone", rclone_cls_mock)
        return rclone_cls_mock, instance

    def test_renders_folders_and_videos(self, ui_with_creds, monkeypatch):
        client, _ = ui_with_creds
        entries = [
            {"Name": "subdir", "IsDir": True, "Size": 0, "Path": "incoming/subdir"},
            {"Name": "scene4.mp4", "IsDir": False, "Size": 1_500_000_000,
             "Path": "incoming/scene4.mp4"},
        ]
        _rclone_cls, instance = self._stub_rclone(monkeypatch, entries)

        r = client.get("/partials/cloud-browse?remote=gdrive_amy&path=incoming")
        assert r.status_code == 200
        body = r.text
        # Both rendered.
        assert "subdir" in body
        assert "scene4.mp4" in body
        # Folder is a drill-down (HTMX GET to deeper path). The `&` between
        # query params is literal Jinja template text, NOT a {{ }} output,
        # so autoescape doesn't turn it into `&amp;`.
        assert "/partials/cloud-browse?remote=gdrive_amy&path=incoming/subdir" in body
        # Video has a Process button + posts to /jobs/cloud.
        assert "/jobs/cloud" in body
        assert "Process" in body
        # rclone wrapper called with videos_only=True so non-videos are filtered.
        assert instance.lsjson.call_args.kwargs.get("videos_only") is True

    def test_root_browse_no_up_link(self, ui_with_creds, monkeypatch):
        client, _ = ui_with_creds
        self._stub_rclone(monkeypatch, [])
        r = client.get("/partials/cloud-browse?remote=gdrive_amy&path=")
        assert r.status_code == 200
        # The "Up" button should not appear at the remote root.
        assert "↑ Up" not in r.text

    def test_subdir_browse_has_up_link(self, ui_with_creds, monkeypatch):
        client, _ = ui_with_creds
        self._stub_rclone(monkeypatch, [])
        r = client.get("/partials/cloud-browse?remote=gdrive_amy&path=incoming/subdir")
        assert r.status_code == 200
        assert "↑ Up" in r.text
        # Parent crumb is incoming/.
        assert "path=incoming" in r.text

    def test_rclone_failure_shown_as_warning_not_500(self, ui_with_creds, monkeypatch):
        from amg.cloud import rclone as rclone_mod

        class _ExplodingRclone:
            def __init__(self, *_a, **_kw):
                pass

            def lsjson(self, *_a, **_kw):
                raise rclone_mod.RcloneError("token expired")

        monkeypatch.setattr(rclone_mod, "Rclone", _ExplodingRclone)

        client, _ = ui_with_creds
        r = client.get("/partials/cloud-browse?remote=gdrive_amy&path=")
        assert r.status_code == 200
        assert "Browse failed" in r.text
        assert "token expired" in r.text


# --- /jobs/cloud (HTMX form submit) ----------------------------------------


class TestCreateCloudJob:
    def test_unknown_remote_400(self, ui_with_creds):
        client, _ = ui_with_creds
        r = client.post(
            "/jobs/cloud",
            data={"remote": "never_added", "path": "scene.mp4"},
        )
        assert r.status_code == 400
        assert "unknown remote" in r.json()["detail"]

    def test_missing_fields_400(self, ui_with_creds):
        client, _ = ui_with_creds
        r = client.post("/jobs/cloud", data={"remote": "", "path": ""})
        assert r.status_code in (400, 422)

    def test_queues_job_with_cloud_source_and_redirects_home(
        self, ui_with_creds, monkeypatch
    ):
        client, app_mod = ui_with_creds

        # Stub the dispatcher so the queued job doesn't actually run during
        # the test (we only care about the queue-row state, not pipeline
        # execution).
        monkeypatch.setattr(app_mod, "_start_dispatcher_if_needed", lambda: None)

        r = client.post(
            "/jobs/cloud",
            data={
                "remote": "gdrive_amy",
                "path": "incoming/scene4.mp4",
                "scene_id": "scene-4-cloud",
            },
        )
        # The endpoint signals HTMX to navigate, not to swap.
        assert r.status_code == 204
        assert r.headers.get("hx-redirect", "").startswith("/?job_id=")

        # The job is in the dispatcher's queue with cloud_source set.
        with app_mod._jobs_lock:
            assert len(app_mod._jobs) == 1
            job = next(iter(app_mod._jobs.values()))
        assert job["source_mode"] == "cloud"
        assert job["cloud_source"] == {
            "remote": "gdrive_amy",
            "path": "incoming/scene4.mp4",
            "scene_id": "scene-4-cloud",
        }
        assert job["scene_id"] == "scene-4-cloud"
        assert job["status"] == "queued"

    def test_default_scene_id_uses_filename_stem(self, ui_with_creds, monkeypatch):
        client, app_mod = ui_with_creds
        monkeypatch.setattr(app_mod, "_start_dispatcher_if_needed", lambda: None)

        client.post(
            "/jobs/cloud",
            data={"remote": "gdrive_amy", "path": "incoming/some_scene.mp4"},
        )
        with app_mod._jobs_lock:
            job = next(iter(app_mod._jobs.values()))
        assert job["scene_id"] == "some_scene"
