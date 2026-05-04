"""Tests for folder-context fallback ingestion."""
import json
from pathlib import Path

import pytest

from amg.ingest.folder_context import (
    FolderContext,
    is_generic_filename,
    resolve_folder_context,
    best_text_for_parsing,
)
from amg.ingest.title_parser import parse_title_with_context, parse_title
from amg.ingest.performer_code import (
    parse_performer_code,
    parse_performer_code_with_context,
)


# ---- generic filename detection ----

class TestGenericFilenameDetection:
    def test_screen_recording(self):
        assert is_generic_filename("Screen Recording 2025-10-24 at 12.47.05 PM")

    def test_iso_timestamp(self):
        assert is_generic_filename("2025-07-10 19.59.08")
        assert is_generic_filename("2025-07-10_19-59-08")

    def test_camera_image(self):
        assert is_generic_filename("IMG_4523")
        assert is_generic_filename("MVI_0001")

    def test_phone_video(self):
        assert is_generic_filename("VID_20250710_195908")

    def test_gopro(self):
        assert is_generic_filename("GH010001")

    def test_clip(self):
        assert is_generic_filename("clip_1")
        assert is_generic_filename("untitled")

    def test_descriptive_filename_not_generic(self):
        assert not is_generic_filename("4 BG - bath teasing scene")
        assert not is_generic_filename("YasminaBrady-Massage-Scene")
        assert not is_generic_filename("21 BG - nurse video")

    def test_empty_filename(self):
        assert is_generic_filename("")
        assert is_generic_filename("   ")


# ---- resolve_folder_context end-to-end ----

class TestResolveFolderContext:
    def test_with_descriptive_folder(self, tmp_path):
        scene_dir = tmp_path / "4 BG - bath teasing scene"
        scene_dir.mkdir()
        video = scene_dir / "2025-07-10 19.59.08.mov"
        video.write_bytes(b"x" * 1024)

        ctx = resolve_folder_context(video)
        assert ctx.is_generic_filename is True
        assert "4 BG - bath teasing scene" in ctx.ancestor_names
        assert ctx.performer_code == "BG"

    def test_with_metadata_json(self, tmp_path):
        creator_dir = tmp_path / "YasminaBrady_Submission"
        creator_dir.mkdir()
        meta = {
            "title": "Bath Tease — Yasmina & Brady",
            "description": "An intimate bathtub scene",
            "performers": ["Yasmina Khan", "Brady"],
            "studio": "YasminaBrady",
            "tags": ["bath", "couple", "tease"],
            "performer_code": "BG",
            "location": "Hotel master bath",
        }
        (creator_dir / "metadata.json").write_text(json.dumps(meta))
        video = creator_dir / "Screen Recording 2025-10-24 at 12.47.05 PM.mov"
        video.write_bytes(b"x" * 1024)

        ctx = resolve_folder_context(video)
        assert ctx.is_generic_filename is True
        assert ctx.title == "Bath Tease — Yasmina & Brady"
        assert ctx.description == "An intimate bathtub scene"
        assert ctx.performers == ["Yasmina Khan", "Brady"]
        assert ctx.studio == "YasminaBrady"
        assert ctx.location == "Hotel master bath"
        assert "bath" in ctx.tags
        assert ctx.performer_code == "BG"
        assert ctx.source_folder == creator_dir.resolve()

    def test_metadata_in_grandparent(self, tmp_path):
        """Generic file inside a 'scenes/' subfolder of the metadata folder."""
        creator = tmp_path / "JMacContent"
        creator.mkdir()
        scenes = creator / "scenes"
        scenes.mkdir()
        (creator / "release.json").write_text(json.dumps({
            "title": "Casting Session 14",
            "performers": [{"name": "Talent A"}, {"name": "Talent B"}],
            "studio": "JMac",
        }))
        video = scenes / "MVI_0001.MP4"
        video.write_bytes(b"x" * 1024)

        ctx = resolve_folder_context(video)
        assert ctx.is_generic_filename is True
        assert ctx.title == "Casting Session 14"
        assert ctx.performers == ["Talent A", "Talent B"]
        assert ctx.studio == "JMac"

    def test_descriptive_filename_no_fallback_needed(self, tmp_path):
        scene_dir = tmp_path / "Random"
        scene_dir.mkdir()
        video = scene_dir / "21 BG - nurse video.mov"
        video.write_bytes(b"x" * 1024)
        ctx = resolve_folder_context(video)
        assert ctx.is_generic_filename is False

    def test_performers_string_split(self, tmp_path):
        d = tmp_path / "S"
        d.mkdir()
        (d / "metadata.json").write_text(json.dumps({"performers": "Yasmina Khan, Tabatha Lust"}))
        video = d / "untitled.mov"
        video.write_bytes(b"x" * 1024)
        ctx = resolve_folder_context(video)
        assert ctx.performers == ["Yasmina Khan", "Tabatha Lust"]

    def test_corrupt_metadata_does_not_raise(self, tmp_path):
        d = tmp_path / "Bad"
        d.mkdir()
        (d / "metadata.json").write_text("not json {")
        video = d / "untitled.mov"
        video.write_bytes(b"x" * 1024)
        ctx = resolve_folder_context(video)
        assert ctx.title is None
        assert ctx.is_generic_filename is True

    def test_best_text_for_parsing_orders_correctly(self, tmp_path):
        d = tmp_path / "Outer"
        d.mkdir()
        (d / "metadata.json").write_text(json.dumps({
            "title": "Bath Tease",
            "description": "Sexy bathtub teasing scene",
        }))
        video = d / "Screen Recording 2025-10-24.mov"
        video.write_bytes(b"x" * 1024)
        ctx = resolve_folder_context(video)
        text = best_text_for_parsing(ctx)
        assert "Bath Tease" in text
        assert "Sexy bathtub teasing scene" in text
        # generic filename excluded
        assert "Screen Recording" not in text


# ---- title parser integration ----

class TestParseTitleWithContext:
    def test_generic_file_recovers_description_from_folder(self, tmp_path):
        scene_dir = tmp_path / "4 BG - bath teasing scene"
        scene_dir.mkdir()
        video = scene_dir / "2025-07-10 19.59.08.mov"
        video.write_bytes(b"x" * 1024)
        result = parse_title(video)
        assert result["description"] == "bath teasing scene"
        assert "BATH" in result["detected_genres"]
        assert result["is_generic_filename"] is True

    def test_metadata_tags_merge_into_genres(self, tmp_path):
        d = tmp_path / "Sub"
        d.mkdir()
        (d / "metadata.json").write_text(json.dumps({
            "title": "Bath Tease",
            "tags": ["bath", "couple"],
        }))
        video = d / "Screen Recording 2025-10-24.mov"
        video.write_bytes(b"x" * 1024)
        result = parse_title(video)
        assert "BATH" in result["detected_genres"]
        assert "COUPLE" in result["detected_genres"]
        assert result["metadata_title"] == "Bath Tease"

    def test_descriptive_filename_unchanged_behavior(self, tmp_path):
        scene_dir = tmp_path / "27 BBGG - couple swap with jimmy and tabatha"
        scene_dir.mkdir()
        video = scene_dir / "27 BBGG - couple swap.mp4"
        video.write_bytes(b"x" * 1024)
        result = parse_title(video)
        assert "couple swap" in result["description"]
        assert result["is_generic_filename"] is False


# ---- performer code integration ----

class TestParsePerformerCodeWithContext:
    def test_finds_code_in_grandparent(self, tmp_path):
        creator = tmp_path / "21 BG - nurse video"
        creator.mkdir()
        sub = creator / "scenes"
        sub.mkdir()
        video = sub / "Screen Recording 2025-10-24.mov"
        video.write_bytes(b"x" * 1024)
        info = parse_performer_code(video)
        assert info is not None
        assert info["code"] == "BG"
        assert info["total"] == 2

    def test_uses_metadata_code(self, tmp_path):
        d = tmp_path / "Sub"
        d.mkdir()
        (d / "metadata.json").write_text(json.dumps({"performer_code": "BBGG"}))
        video = d / "untitled.mov"
        video.write_bytes(b"x" * 1024)
        info = parse_performer_code(video)
        assert info is not None
        assert info["code"] == "BBGG"

    def test_no_code_anywhere(self, tmp_path):
        d = tmp_path / "Random"
        d.mkdir()
        video = d / "untitled.mov"
        video.write_bytes(b"x" * 1024)
        assert parse_performer_code(video) is None
