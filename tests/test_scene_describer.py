"""Tests for scene describer + enriched title generator (no live AI)."""
from unittest.mock import patch, MagicMock

import pytest

from amg.scoring.ai_client import AIResponse
from amg.scoring.scene_describer import (
    SceneInsight,
    summarize_positions,
    generate_titles_with_insight,
    _parse_scene_insight,
    _parse_enriched_response,
)


# ---- parsers ----

class TestParseSceneInsight:
    def test_well_formed(self):
        text = """
SETTING: hotel master bath
LOCATION_HINT: white-tile bathroom with candles
NOTABLE_FEATURES: wet hair, candlelight, terrycloth robe
ACTION_SUMMARY: A couple shares a bathtub scene with playful teasing.
The action is intimate and slow-paced.
MOOD: intimate
END
"""
        out = _parse_scene_insight(text)
        assert out.setting == "hotel master bath"
        assert "wet hair" in out.notable_features
        assert "candlelight" in out.notable_features
        assert out.mood == "intimate"
        assert "intimate" in (out.action_summary or "").lower()
        assert out.location_hint == "white-tile bathroom with candles"

    def test_missing_fields(self):
        out = _parse_scene_insight("SETTING: bedroom\nEND")
        assert out.setting == "bedroom"
        assert out.notable_features == []
        assert out.mood is None

    def test_strips_quotes(self):
        out = _parse_scene_insight('SETTING: "hotel suite"')
        assert out.setting == "hotel suite"


class TestParseEnrichedResponse:
    def test_titles_and_long_description(self):
        raw = """
TITLE_1: Bath Tease — Yasmina & Brady
STYLE_1: performer_led

TITLE_2: Slow Tub Tease in the Master Bath
STYLE_2: scene_descriptive

LONG_DESCRIPTION: Yasmina and Brady share an intimate bath. Candlelight, slow teasing, and a playful mood across the scene.
CATEGORY_SUGGESTIONS: Amateur, Verified Models, HD Porn, POV
TAG_SUGGESTIONS: pov, eye contact, bathtub, teasing, blowjob

END
"""
        result = _parse_enriched_response(raw)
        assert len(result["titles"]) == 2
        assert result["titles"][0]["text"] == "Bath Tease — Yasmina & Brady"
        assert result["titles"][0]["style"] == "performer_led"
        assert "candlelight" in result["long_description"].lower()
        assert "Amateur" in result["categories"]
        assert "pov" in result["tags"]

    def test_no_titles(self):
        result = _parse_enriched_response("garbage response")
        assert result["titles"] == []
        assert result["long_description"] == ""
        assert result["categories"] == []
        assert result["tags"] == []


# ---- position summary ----

class TestSummarizePositions:
    def test_rolls_up_types_and_positions(self):
        covers = [
            {"type": "NUDE", "position_label": "OTHER"},
            {"type": "PENETRATION", "position_label": "MISSIONARY", "penetration_visible": True},
            {"type": "FINISH", "position_label": "DOGGY", "penetration_visible": True},
            {"type": "BUILDUP", "position_label": ""},
        ]
        out = summarize_positions(covers)
        assert out["NUDE"] == 1
        assert out["PENETRATION"] == 1
        assert out["MISSIONARY"] == 1
        assert out["DOGGY"] == 1
        assert out["EXPLICIT"] == 2

    def test_handles_empty(self):
        assert summarize_positions([]) == {}
        assert summarize_positions(None) == {}


# ---- title gen with mock AI (offline + live paths) ----

class TestGenerateTitlesWithInsight:
    def test_offline_falls_back_to_template(self):
        client = MagicMock()
        client.is_alive.return_value = False
        result = generate_titles_with_insight(
            studio="YasminaBrady",
            performers=["Yasmina Khan"],
            scene_type="COUPLE",
            genres=["BATH"],
            description="bath teasing scene",
            insight=None,
            position_summary={"NUDE": 3, "PENETRATION": 2},
            ai_client=client,
        )
        assert result["ai_used"] is False
        assert len(result["titles"]) > 0
        for t in result["titles"]:
            assert t["text"]
            assert "char_count" in t
            assert "platform_fit" in t

    def test_live_response_parsed(self):
        client = MagicMock()
        client.is_alive.return_value = True
        client.generate_text.return_value = AIResponse(
            success=True,
            raw_text=(
                "TITLE_1: Bath Tease with Yasmina\n"
                "STYLE_1: performer_led\n\n"
                "TITLE_2: Couple's Slow Bath Scene\n"
                "STYLE_2: scene_descriptive\n\n"
                "LONG_DESCRIPTION: Yasmina shares an intimate bath scene featuring slow teasing and candlelight.\n"
                "CATEGORY_SUGGESTIONS: Amateur, Verified Models, HD Porn, POV\n"
                "TAG_SUGGESTIONS: pov, eye contact, bathtub, teasing\n"
                "END\n"
            ),
        )
        insight = SceneInsight(setting="bathtub", mood="intimate")
        result = generate_titles_with_insight(
            studio="YasminaBrady",
            performers=["Yasmina Khan"],
            scene_type="COUPLE",
            genres=["BATH"],
            description="bath teasing scene",
            insight=insight,
            position_summary={"NUDE": 3},
            ai_client=client,
            n_suggestions=2,
        )
        assert result["ai_used"] is True
        assert len(result["titles"]) == 2
        assert result["titles"][0]["text"] == "Bath Tease with Yasmina"
        assert "yasmina" in result["titles"][1]["text"].lower()
        assert "intimate bath" in result["long_description"].lower()
        assert "POV" in result["categories"]
        assert "pov" in result["tags"]

    def test_live_response_dedupes_near_duplicate_titles(self):
        client = MagicMock()
        client.is_alive.return_value = True
        client.generate_text.return_value = AIResponse(
            success=True,
            raw_text=(
                "TITLE_1: Yasmina Bath Tease POV\n"
                "STYLE_1: performer_led\n\n"
                "TITLE_2: Yasmina Bath Tease POV Scene\n"
                "STYLE_2: scene_descriptive\n\n"
                "TITLE_3: Candlelit Tub Seduction with Yasmina\n"
                "STYLE_3: narrative_hook\n\n"
                "LONG_DESCRIPTION: Yasmina and Brady share an intimate bath sequence.\n"
                "CATEGORY_SUGGESTIONS: Amateur, Verified Models, HD Porn, POV\n"
                "TAG_SUGGESTIONS: pov, eye contact, bathtub, teasing\n"
                "END\n"
            ),
        )
        result = generate_titles_with_insight(
            studio="YasminaBrady",
            performers=["Yasmina Khan"],
            scene_type="COUPLE",
            genres=["BATH"],
            description="bath teasing scene",
            insight=SceneInsight(setting="bathtub", mood="intimate"),
            position_summary={"NUDE": 3},
            ai_client=client,
            n_suggestions=3,
        )
        texts = [t["text"] for t in result["titles"]]
        similar = [t for t in texts if "bath tease pov" in t.lower()]
        assert len(similar) <= 1

    def test_long_description_strips_overused_terms(self):
        client = MagicMock()
        client.is_alive.return_value = True
        client.generate_text.return_value = AIResponse(
            success=True,
            raw_text=(
                "TITLE_1: Bath Tease with Yasmina\n"
                "STYLE_1: performer_led\n\n"
                "TITLE_2: Couple Bath Scene with Yasmina\n"
                "STYLE_2: scene_descriptive\n\n"
                "LONG_DESCRIPTION: A wild and naughty setup gets crazy fast. A wild and naughty setup gets crazy fast.\n"
                "CATEGORY_SUGGESTIONS: Amateur, Verified Models, HD Porn, POV\n"
                "TAG_SUGGESTIONS: pov, eye contact, bathtub, teasing\n"
                "END\n"
            ),
        )
        result = generate_titles_with_insight(
            studio="YasminaBrady",
            performers=["Yasmina Khan"],
            scene_type="COUPLE",
            genres=["BATH"],
            description="bath teasing scene",
            insight=SceneInsight(setting="bathtub", mood="intimate"),
            position_summary={"NUDE": 3},
            ai_client=client,
            n_suggestions=2,
        )
        desc = result["long_description"].lower()
        assert "wild" not in desc
        assert "naughty" not in desc
        assert "crazy" not in desc
