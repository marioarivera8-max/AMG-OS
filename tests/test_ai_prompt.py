"""Tests for AI prompt building and response parsing."""
import pytest

from amg.scoring.prompt import build_scoring_prompt, build_simplified_prompt
from amg.scoring.parser import parse_ai_response, ScoredFrame


class TestBuildScoringPrompt:

    def test_basic_prompt(self):
        prompt = build_scoring_prompt()
        assert "TIER A" in prompt
        assert "TIER B" in prompt
        assert "DB1" in prompt
        assert "OUTPUT FORMAT" in prompt

    def test_with_scene_type(self):
        prompt = build_scoring_prompt(primary_scene_type="GANGBANG", performer_count=5)
        assert "GANGBANG" in prompt
        assert "5 performers expected" in prompt

    def test_with_genres(self):
        prompt = build_scoring_prompt(detected_genres=["ANAL", "DP"])
        assert "ANAL" in prompt
        assert "DP" in prompt

    def test_with_studio_hint(self):
        prompt = build_scoring_prompt(studio_hint="BlondeHexe — German MILF content",
                                      studio_language="de")
        assert "BlondeHexe" in prompt
        assert "de market" in prompt

    def test_simplified_prompt(self):
        prompt = build_simplified_prompt()
        assert "SCORE:" in prompt
        assert "TYPE:" in prompt
        assert "GAZE:" in prompt
        # Should be much shorter than full prompt
        full = build_scoring_prompt()
        assert len(prompt) < len(full) / 2


class TestParseAiResponse:

    def test_full_pass_response(self):
        text = """
        TIER_A_PASS: yes
        TIER_B_PRESENT: B1,B3,B6
        TIER_C_PRESENT: C1
        SCORE: 8.5
        TYPE: SEX_ACT
        GAZE: SINGLE
        AESTHETIC: PROFESSIONAL
        END
        """
        result = parse_ai_response(text)
        assert result.parse_succeeded
        assert result.tier_a_pass
        assert result.score == 8.5
        assert "B1" in result.tier_b_present
        assert "B3" in result.tier_b_present
        assert "B6" in result.tier_b_present
        assert "C1" in result.tier_c_present
        assert result.type_ == "SEX_ACT"
        assert result.gaze == "SINGLE"
        assert result.aesthetic == "PROFESSIONAL"

    def test_tier_a_fail(self):
        text = """
        TIER_A_FAIL: DB2
        SCORE: 0
        END
        """
        result = parse_ai_response(text)
        assert result.parse_succeeded
        assert not result.tier_a_pass
        assert result.tier_a_fail_code == "DB2"
        assert result.score == 0

    def test_simplified_response(self):
        text = """
        SCORE: 6.0
        TYPE: NUDE
        GAZE: AVERTED
        END
        """
        result = parse_ai_response(text)
        assert result.parse_succeeded
        assert result.score == 6.0
        assert result.type_ == "NUDE"
        assert result.gaze == "AVERTED"

    def test_score_clamped(self):
        # Score above 10 should be clamped
        text = "SCORE: 15.0\nTYPE: SEX_ACT\nEND"
        result = parse_ai_response(text)
        assert result.score == 10.0

    def test_negative_score_clamped(self):
        text = "SCORE: -2.0\nTYPE: NUDE\nEND"
        result = parse_ai_response(text)
        assert result.score == 0.0

    def test_malformed_response(self):
        text = "I'm sorry, I cannot help with that request."
        result = parse_ai_response(text)
        assert not result.parse_succeeded

    def test_empty_response(self):
        result = parse_ai_response("")
        assert not result.parse_succeeded

    def test_score_with_extra_text(self):
        text = """
        Looking at this image, I see:
        SCORE: 7.5
        TYPE: PENETRATION
        GAZE: SINGLE
        END
        Hope this helps!
        """
        result = parse_ai_response(text)
        assert result.parse_succeeded
        assert result.score == 7.5

    def test_lowercase_field_names(self):
        text = """
        score: 5.5
        type: nude
        gaze: direct
        end
        """
        result = parse_ai_response(text)
        assert result.parse_succeeded
        assert result.score == 5.5
        assert result.type_ == "NUDE"
