"""Tests for v11.1 performer code parsing — lesbian/gay/solo handling."""
from pathlib import Path
import pytest

from amg.ingest.performer_code import (
    parse_performer_code,
    detect_scene_type_from_code,
    get_authoritative_performer_count,
)


class TestMixedScenes:
    """Mixed M/F codes — most common case."""

    def test_bbgg_foursome(self, tmp_path):
        scene_dir = tmp_path / "27 BBGG - couple swap"
        scene_dir.mkdir()
        video = scene_dir / "video.mp4"
        video.touch()

        result = parse_performer_code(video)
        assert result["code"] == "BBGG"
        assert result["total"] == 4
        assert result["is_foursome"] is True
        assert result["is_lesbian"] is False
        assert result["is_gay"] is False
        assert result["is_mixed"] is True
        assert result["flag_for_review"] is False

    def test_bg_couple(self, tmp_path):
        scene_dir = tmp_path / "4 BG - couple scene"
        scene_dir.mkdir()
        video = scene_dir / "video.mp4"
        video.touch()

        result = parse_performer_code(video)
        assert result["is_couple"] is True
        assert result["is_mixed"] is True

    def test_gangbang(self, tmp_path):
        scene_dir = tmp_path / "7 BBBBBBG - gangbang"
        scene_dir.mkdir()
        (scene_dir / "video.mp4").touch()

        result = parse_performer_code(scene_dir / "video.mp4")
        assert result["is_gangbang"] is True
        assert result["male_count"] == 6
        assert result["female_count"] == 1

    def test_reverse_gangbang_via_classification(self, tmp_path):
        # 1 male, 4 females
        scene_dir = tmp_path / "5 BGGGG - reverse"
        scene_dir.mkdir()
        (scene_dir / "video.mp4").touch()

        result = parse_performer_code(scene_dir / "video.mp4")
        assert result["total"] == 5
        # Pure male_count check shows it's REVERSE_GANGBANG-eligible
        assert result["female_count"] > result["male_count"]


class TestLesbianScenes:
    """v11.1 fix — all-G codes are lesbian, not couple/threesome."""

    def test_gg_is_lesbian_not_couple(self, tmp_path):
        scene_dir = tmp_path / "12 GG - lesbian fun"
        scene_dir.mkdir()
        (scene_dir / "video.mp4").touch()

        result = parse_performer_code(scene_dir / "video.mp4")
        assert result["is_lesbian"] is True
        assert result["is_couple"] is False  # NOT couple
        assert result["male_count"] == 0
        assert result["female_count"] == 2

    def test_ggg_is_lesbian_threesome(self, tmp_path):
        scene_dir = tmp_path / "8 GGG - lesbian threesome"
        scene_dir.mkdir()
        (scene_dir / "video.mp4").touch()

        result = parse_performer_code(scene_dir / "video.mp4")
        assert result["is_lesbian"] is True
        assert result["is_threesome"] is False  # NOT generic threesome
        assert result["female_count"] == 3

    def test_gggg_is_lesbian_group(self, tmp_path):
        scene_dir = tmp_path / "10 GGGG - lesbian foursome"
        scene_dir.mkdir()
        (scene_dir / "video.mp4").touch()

        result = parse_performer_code(scene_dir / "video.mp4")
        assert result["is_lesbian"] is True
        assert result["female_count"] == 4

    def test_g_alone_is_solo_female(self, tmp_path):
        # v11.1: Single-char codes now accepted
        scene_dir = tmp_path / "1 G - solo masturbation"
        scene_dir.mkdir()
        (scene_dir / "video.mp4").touch()

        result = parse_performer_code(scene_dir / "video.mp4")
        assert result is not None
        assert result["is_solo_female"] is True
        assert result["is_lesbian"] is False  # NOT lesbian (only 1 person)


class TestGayContentFlagging:
    """v11.1: All-male codes flagged for review."""

    def test_bb_is_gay_flagged(self, tmp_path):
        scene_dir = tmp_path / "3 BB - two guys"
        scene_dir.mkdir()
        (scene_dir / "video.mp4").touch()

        result = parse_performer_code(scene_dir / "video.mp4")
        assert result["is_gay"] is True
        assert result["flag_for_review"] is True
        assert result["flag_reason"] == "ALL_MALE_CONTENT"

    def test_b_alone_is_solo_male_flagged(self, tmp_path):
        scene_dir = tmp_path / "1 B - solo male"
        scene_dir.mkdir()
        (scene_dir / "video.mp4").touch()

        result = parse_performer_code(scene_dir / "video.mp4")
        assert result["is_solo_male"] is True
        assert result["flag_for_review"] is True


class TestSceneTypeDetection:
    """detect_scene_type_from_code with v11.1 categories."""

    def test_lesbian_scene_types(self):
        assert detect_scene_type_from_code(
            {"is_lesbian": True, "total": 2, "is_solo_female": False,
             "is_solo_male": False, "is_gay": False, "is_couple": False,
             "is_threesome": False, "is_foursome": False, "is_gangbang": False,
             "female_count": 2, "male_count": 0}
        ) == "LESBIAN"

        assert detect_scene_type_from_code(
            {"is_lesbian": True, "total": 3, "is_solo_female": False,
             "is_solo_male": False, "is_gay": False, "is_couple": False,
             "is_threesome": False, "is_foursome": False, "is_gangbang": False,
             "female_count": 3, "male_count": 0}
        ) == "LESBIAN_THREESOME"

        assert detect_scene_type_from_code(
            {"is_lesbian": True, "total": 4, "is_solo_female": False,
             "is_solo_male": False, "is_gay": False, "is_couple": False,
             "is_threesome": False, "is_foursome": False, "is_gangbang": False,
             "female_count": 4, "male_count": 0}
        ) == "LESBIAN_GROUP"

    def test_solo_female(self):
        result = detect_scene_type_from_code(
            {"is_lesbian": False, "total": 1, "is_solo_female": True,
             "is_solo_male": False, "is_gay": False, "is_couple": False,
             "is_threesome": False, "is_foursome": False, "is_gangbang": False,
             "female_count": 1, "male_count": 0}
        )
        assert result == "SOLO_FEMALE"

    def test_gay_types(self):
        assert detect_scene_type_from_code(
            {"is_lesbian": False, "is_gay": True, "total": 2,
             "is_solo_female": False, "is_solo_male": False, "is_couple": False,
             "is_threesome": False, "is_foursome": False, "is_gangbang": False,
             "female_count": 0, "male_count": 2}
        ) == "GAY"

    def test_reverse_gangbang(self):
        # 1 male, 4 females
        result = detect_scene_type_from_code(
            {"is_lesbian": False, "is_gay": False, "total": 5,
             "is_solo_female": False, "is_solo_male": False, "is_couple": False,
             "is_threesome": False, "is_foursome": False, "is_gangbang": True,
             "female_count": 4, "male_count": 1}
        )
        assert result == "REVERSE_GANGBANG"

    def test_foursome_bgg_g_is_reverse(self):
        # 4-person scene with 1M + 3F = REVERSE_GANGBANG
        result = detect_scene_type_from_code(
            {"is_lesbian": False, "is_gay": False, "total": 4,
             "is_solo_female": False, "is_solo_male": False, "is_couple": False,
             "is_threesome": False, "is_foursome": True, "is_gangbang": False,
             "female_count": 3, "male_count": 1}
        )
        assert result == "REVERSE_GANGBANG"


class TestAuthoritativeCount:
    def test_code_takes_priority(self, tmp_path):
        scene_dir = tmp_path / "27 BBGG - test"
        scene_dir.mkdir()
        (scene_dir / "video.mp4").touch()

        count, source = get_authoritative_performer_count(
            scene_dir / "video.mp4", fallback_count=2
        )
        assert count == 4
        assert source == "filename_code"

    def test_fallback_when_no_code(self, tmp_path):
        scene_dir = tmp_path / "no code here"
        scene_dir.mkdir()
        (scene_dir / "video.mp4").touch()

        count, source = get_authoritative_performer_count(
            scene_dir / "video.mp4", fallback_count=3
        )
        assert count == 3
        assert source == "face_detection"
