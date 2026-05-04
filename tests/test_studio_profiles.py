"""Tests for studio detection and profile handling."""
from pathlib import Path
import pytest

from amg.ingest.studio_profiles import (
    detect_studio,
    KNOWN_STUDIOS,
    _create_stub_profile,
)


class TestDetectStudio:

    def test_yasmina_in_path(self):
        path = Path("/Users/mario/AMG_Processing/YasminaBrady/27 BBGG/video.mp4")
        assert detect_studio(path) == "YasminaBrady"

    def test_blondehexe_in_path(self):
        path = Path("/work/blondehexe/some_scene/video.mp4")
        assert detect_studio(path) == "BlondeHexe"

    def test_underscore_variant(self):
        path = Path("/incoming/maximo_garcia/scene1/v.mp4")
        assert detect_studio(path) == "MaximoGarcia"

    def test_space_variant(self):
        path = Path("/incoming/Naughty America/some_scene/v.mp4")
        assert detect_studio(path) == "NaughtyAmerica"

    def test_unknown_studio(self):
        path = Path("/random/path/scene/v.mp4")
        assert detect_studio(path) is None

    def test_known_studios_dict_complete(self):
        # Sanity: every entry has the canonical name as the dict key
        for canonical, patterns in KNOWN_STUDIOS.items():
            assert isinstance(patterns, list)
            assert len(patterns) > 0


class TestCreateStubProfile:

    def test_yasmina_has_specific_defaults(self):
        profile = _create_stub_profile("YasminaBrady")
        assert profile["name"] == "YasminaBrady"
        assert profile["primary_language"] == "en"
        assert "es" in profile["secondary_languages"]
        assert "GROUP" in profile["default_genres"]
        assert profile["filename_patterns"]["uses_performer_code"] is True
        assert not profile.get("stub", False)

    def test_blondehexe_german(self):
        profile = _create_stub_profile("BlondeHexe")
        assert profile["primary_language"] == "de"
        assert profile["primary_market"] == "EU_DE"
        assert "MILF" in profile["default_genres"]

    def test_unknown_studio_stub(self):
        profile = _create_stub_profile("NewStudio2026")
        assert profile["name"] == "NewStudio2026"
        assert profile["primary_language"] == "en"  # Default
        assert profile["stub"] is True

    def test_all_required_fields(self):
        profile = _create_stub_profile("Anything")
        required = [
            "name", "display_name", "primary_language",
            "performers", "default_genres", "platform_targets",
            "calibration_history",
        ]
        for field in required:
            assert field in profile, f"Missing required field: {field}"
