"""Tests for v11.1 new modules: title generator, search, DVD compile, error form."""
import json
import tempfile
from pathlib import Path

from amg.scoring.title_generator import (
    _parse_title_response,
    _check_platform_fit,
    _check_warnings,
    _fallback_titles,
)
from amg.utils.error_form import (
    format_error_report,
    format_partial_success_report,
    ERROR_RECOVERY,
)


class TestTitleParsing:
    """v11.1 title generator response parsing."""

    def test_parse_clean_response(self):
        raw = """TITLE_1: Clean Title One
STYLE_1: performer_led

TITLE_2: Another Title
STYLE_2: narrative_hook
END"""
        titles = _parse_title_response(raw)
        assert len(titles) == 2
        assert titles[0]["text"] == "Clean Title One"
        assert titles[1]["style"] == "narrative_hook"

    def test_strips_quotes(self):
        raw = 'TITLE_1: "Quoted Title"\nSTYLE_1: performer_led\nEND'
        titles = _parse_title_response(raw)
        assert titles[0]["text"] == "Quoted Title"

    def test_empty_response(self):
        titles = _parse_title_response("")
        assert titles == []

    def test_partial_response(self):
        raw = "TITLE_1: Only Title\nEND"
        titles = _parse_title_response(raw)
        assert len(titles) == 1
        assert titles[0]["style"] == "unknown"


class TestPlatformFit:
    def test_short_title_fits_all(self):
        fit = _check_platform_fit("Short Title")
        assert fit["AEBN"] is True
        assert fit["SLR"] is True
        assert fit["ADE"] is True

    def test_60_char_aebn_boundary(self):
        # AEBN limit is 60
        fit = _check_platform_fit("a" * 60)
        assert fit["AEBN"] is True
        fit = _check_platform_fit("a" * 61)
        assert fit["AEBN"] is False

    def test_long_title_only_fits_ade(self):
        # 90 chars: too long for AEBN (60) and SLR (80), fits ADE (100)
        fit = _check_platform_fit("a" * 90)
        assert fit["AEBN"] is False
        assert fit["SLR"] is False
        assert fit["ADE"] is True


class TestTitleWarnings:
    def test_short_title_warns(self):
        warnings = _check_warnings("Hi")
        assert any("short" in w.lower() for w in warnings)

    def test_clean_title_no_warnings(self):
        warnings = _check_warnings("A Reasonable Length Title For This Scene")
        assert warnings == []


class TestFallbackTitles:
    def test_fallbacks_generate_n(self):
        for n in [1, 3, 5]:
            titles = _fallback_titles("Studio", ["Person"], "COUPLE", ["ANAL"], n)
            assert len(titles) == n

    def test_fallbacks_have_required_fields(self):
        titles = _fallback_titles("TestStudio", ["Test Person"], "COUPLE", ["ANAL"], 3)
        for t in titles:
            assert "text" in t
            assert "style" in t
            assert len(t["text"]) > 0


class TestErrorForm:
    def test_recovery_table_complete(self):
        critical_codes = [
            "E_AI_UNAVAILABLE",
            "E_AI_TIMEOUT",
            "E_TIMEOUT_HARD",
            "E_COMPLIANCE_NO_2257",
            "E_FLOOR_NOT_MET",
        ]
        for code in critical_codes:
            assert code in ERROR_RECOVERY

    def test_error_report_format(self):
        report = format_error_report(
            scene_id="test_scene_42",
            error_codes=["E_AI_TIMEOUT"],
            duration_sec=120,
            partial_covers=8,
            expected_covers=15,
        )
        assert "test_scene_42" in report
        assert "E_AI_TIMEOUT" in report
        assert "RECOMMENDED FIX" in report
        assert "amg resume" in report
        assert "Got 8/15 covers" in report

    def test_partial_success_report(self):
        report = format_partial_success_report(
            scene_id="partial_test",
            covers_delivered=12,
            expected=15,
            fallbacks_used=["Fallback A"],
            warnings=["Some warning"],
        )
        assert "partial_test" in report
        assert "12/15" in report
        assert "Fallback A" in report
