"""Tests for AI prompt building and response parsing."""
import pytest

from amg.scoring.prompt import (
    build_scoring_prompt,
    build_simplified_prompt,
    build_streaming_scoring_prompt,
)
from amg.scoring.parser import parse_ai_response, ScoredFrame


class TestBuildScoringPrompt:

    def test_basic_prompt(self):
        prompt = build_scoring_prompt()
        assert "TIER A" in prompt
        assert "TIER B" in prompt
        assert "DB1" in prompt
        assert "OUTPUT FORMAT" in prompt
        assert "PENETRATION_VISIBLE" in prompt
        assert "PENETRATION_CONFIDENCE" in prompt
        assert "ACTION_EVIDENCE" in prompt
        assert "POSITION / GENRE TAXONOMY" in prompt
        assert "POSITION:" in prompt
        assert "GENRES:" in prompt
        assert "SUBGENRES:" in prompt
        assert "CONTENT_FLAGS:" in prompt
        assert "CONTENT_FLAG_CONFIDENCE:" in prompt
        assert "BLOOD" in prompt
        assert "URINE" in prompt
        assert "FECES" in prompt
        assert "DOGGY_STYLE" in prompt
        assert "BLOWJOB_KNEELING" in prompt
        assert "STEP_FAMILY" in prompt
        assert "RETAIL_BASE" in prompt
        assert "100.0" in prompt
        assert "TIER_D_PRESENT" in prompt
        assert "B10" in prompt

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
        assert "100.0" in prompt
        assert "TYPE:" in prompt
        assert "GAZE:" in prompt
        assert "PENETRATION_VISIBLE:" in prompt
        assert "POSITION:" in prompt
        assert "GENRES:" in prompt
        assert "CONTENT_FLAGS:" in prompt
        # Should be much shorter than full prompt
        full = build_scoring_prompt()
        assert len(prompt) < len(full) / 2

    def test_streaming_prompt_is_compact_but_structured(self):
        prompt = build_streaming_scoring_prompt(
            primary_scene_type="STANDARD",
            detected_genres=["POV"],
            performer_count=2,
        )
        assert "TIER_A_PASS: yes" in prompt
        assert "TIER_B_PRESENT" in prompt
        assert "PENETRATION_VISIBLE" in prompt
        assert "POSITION:" in prompt
        assert "GENRES:" in prompt
        assert "CONTENT_FLAGS:" in prompt
        assert "DOGGY_STYLE" in prompt
        assert "POV" in prompt
        assert len(prompt) < len(build_scoring_prompt()) * 0.7


class TestParseAiResponse:

    def test_full_pass_response(self):
        text = """
        TIER_A_PASS: yes
        TIER_B_PRESENT: B1,B3,B6
        TIER_C_PRESENT: C1
        TIER_D_PRESENT: NONE
        SCORE: 84.5
        TYPE: SEX_ACT
        GAZE: SINGLE
        AESTHETIC: PROFESSIONAL
        PENETRATION_VISIBLE: no
        PENETRATION_CONFIDENCE: 0.13
        ACTION_EVIDENCE: POSE_NO_CONTACT
        END
        """
        result = parse_ai_response(text)
        assert result.parse_succeeded
        assert result.tier_a_pass
        # Deterministic recompute wins: 34 + (10+16+10) + 5 = 75
        assert result.score == 75.0
        assert result.model_score_raw == 84.5
        assert "B1" in result.tier_b_present
        assert "B3" in result.tier_b_present
        assert "B6" in result.tier_b_present
        assert "C1" in result.tier_c_present
        assert result.type_ == "SEX_ACT"
        assert result.gaze == "SINGLE"
        assert result.aesthetic == "PROFESSIONAL"
        assert result.penetration_visible is False
        assert result.penetration_confidence == 0.13
        assert result.action_evidence == "POSE_NO_CONTACT"

    def test_taxonomy_fields_parse_and_normalize(self):
        text = """
        TIER_A_PASS: yes
        TIER_B_PRESENT: B1,B3
        TIER_C_PRESENT: C1
        TIER_D_PRESENT: NONE
        SCORE: 81.0
        TYPE: PENETRATION
        GAZE: SINGLE
        AESTHETIC: PROFESSIONAL
        PENETRATION_VISIBLE: yes
        PENETRATION_CONFIDENCE: 0.91
        ACTION_EVIDENCE: EXPLICIT_PENETRATION
        POSITION: doggy
        POSITION_CONFIDENCE: 0.82
        GENRES: straight, group
        SUBGENRES: teen, squirting, casting_couch, unknown_tag
        CONTENT_FLAGS: pee, poop, blood, nope
        CONTENT_FLAG_CONFIDENCE: 0.76
        END
        """
        result = parse_ai_response(text)
        assert result.parse_succeeded
        assert result.position_label == "DOGGY_STYLE"
        assert result.position_confidence == 0.82
        assert result.genre_tags == ["STRAIGHT", "GROUP"]
        assert result.subgenre_tags == ["TEEN_18_PLUS", "SQUIRTING", "CASTING_COUCH"]
        assert result.sensitive_content_flags == ["URINE", "FECES", "BLOOD"]
        assert result.sensitive_content_confidence == 0.76

    def test_position_aliases_normalize_for_coverage(self):
        from amg.config import normalize_position_label

        assert normalize_position_label("side") == "SPOON"
        assert normalize_position_label("doggy") == "DOGGY_STYLE"
        assert normalize_position_label("oral") == "ORAL_BJ"
        assert normalize_position_label("pussy licking") == "ORAL_CUNN"
        assert normalize_position_label("vibrator") == "TOY"
        assert normalize_position_label("group sex") == "GROUP"

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
        SCORE: 62.0
        TYPE: NUDE
        GAZE: AVERTED
        END
        """
        result = parse_ai_response(text)
        assert result.parse_succeeded
        assert result.score == 62.0
        assert result.type_ == "NUDE"
        assert result.gaze == "AVERTED"

    def test_score_clamped(self):
        # Score above SCORE_MAX should be clamped
        text = "SCORE: 150.0\nTYPE: SEX_ACT\nEND"
        result = parse_ai_response(text)
        assert result.score == 100.0

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
        SCORE: 76.5
        TYPE: PENETRATION
        GAZE: SINGLE
        END
        Hope this helps!
        """
        result = parse_ai_response(text)
        assert result.parse_succeeded
        assert result.score == 76.5
        assert result.model_score_raw == 76.5
        assert result.penetration_visible is True
        assert result.penetration_confidence >= 0.5

    def test_lowercase_field_names(self):
        text = """
        score: 55.5
        type: nude
        gaze: direct
        penetration_visible: no
        penetration_confidence: 0.2
        action_evidence: occluded
        end
        """
        result = parse_ai_response(text)
        assert result.parse_succeeded
        assert result.score == 55.5
        assert result.type_ == "NUDE"
        assert result.penetration_visible is False
        assert result.penetration_confidence == 0.2
        assert result.action_evidence == "OCCLUDED"

    def test_deterministic_penalty_applied(self):
        text = """
        TIER_A_PASS: yes
        TIER_B_PRESENT: B1,B9,B13
        TIER_C_PRESENT: C1,C4
        TIER_D_PRESENT: D1,D3
        SCORE: 99.0
        TYPE: SEX_ACT
        GAZE: DUAL
        AESTHETIC: PROFESSIONAL
        PENETRATION_VISIBLE: no
        PENETRATION_CONFIDENCE: 0.30
        ACTION_EVIDENCE: ORAL_CONTACT
        END
        """
        result = parse_ai_response(text)
        # 34 + (10+12+14) + (5+4) - (8+7) = 64
        assert result.score == 64.0
        assert result.model_score_raw == 99.0

    def test_non_elite_scores_capped_below_97(self):
        text = """
        TIER_A_PASS: yes
        TIER_B_PRESENT: B1,B3,B5,B6,B8
        TIER_C_PRESENT: C1,C2,C3
        TIER_D_PRESENT: NONE
        SCORE: 100.0
        TYPE: SEX_ACT
        GAZE: SINGLE
        AESTHETIC: PROFESSIONAL
        PENETRATION_VISIBLE: yes
        PENETRATION_CONFIDENCE: 0.75
        ACTION_EVIDENCE: EXPLICIT_PENETRATION
        END
        """
        result = parse_ai_response(text)
        assert result.parse_succeeded
        assert result.score <= 96.0

    def test_art_tier_can_reach_100(self):
        text = """
        TIER_A_PASS: yes
        TIER_B_PRESENT: B1,B2,B3,B5,B6,B7,B8,B9,B10,B12,B13
        TIER_C_PRESENT: C1,C2,C3,C4,C5
        TIER_D_PRESENT: NONE
        SCORE: 99.0
        TYPE: PENETRATION
        GAZE: DUAL
        AESTHETIC: PROFESSIONAL
        PENETRATION_VISIBLE: yes
        PENETRATION_CONFIDENCE: 0.95
        ACTION_EVIDENCE: EXPLICIT_PENETRATION
        END
        """
        result = parse_ai_response(text)
        assert result.parse_succeeded
        assert result.score == 100.0
