from types import SimpleNamespace


def _entry(**overrides):
    scored = SimpleNamespace(
        parse_succeeded=True,
        tier_a_fail_code=None,
        score=overrides.pop("score", 96.0),
        type_=overrides.pop("type_", "PENETRATION"),
        penetration_visible=overrides.pop("penetration_visible", True),
        penetration_confidence=overrides.pop("penetration_confidence", 0.95),
        action_evidence=overrides.pop("action_evidence", "EXPLICIT_PENETRATION"),
    )
    return {"timestamp_sec": overrides.pop("timestamp_sec", 10.0), "scored_frame": scored, **overrides}


def test_contradictory_penetration_cover_is_hard_rejected(monkeypatch):
    import amg.output.cover_validation as cv

    monkeypatch.setattr(cv, "COVER_PRESENCE_GATE_ENABLED", True)
    monkeypatch.setattr(cv, "COVER_PRESENCE_GATE_MODE", "hard_block")
    monkeypatch.setattr(cv, "COVER_MIN_PENETRATION_CONFIDENCE", 0.60)

    entry = _entry(
        penetration_visible=False,
        penetration_confidence=0.49,
        action_evidence="OCCLUDED",
    )

    validation = cv.validate_cover_candidate(entry)

    assert validation["eligible"] is False
    assert "PENETRATION_TYPE_WITHOUT_VISIBLE_PENETRATION" in validation["reject_codes"]
    assert "LOW_PENETRATION_CONFIDENCE" in validation["reject_codes"]
    assert "BAD_PENETRATION_EVIDENCE_OCCLUDED" in validation["reject_codes"]


def test_valid_explicit_penetration_cover_passes(monkeypatch):
    import amg.output.cover_validation as cv

    monkeypatch.setattr(cv, "COVER_PRESENCE_GATE_ENABLED", True)
    entry = _entry()

    validation = cv.validate_cover_candidate(entry)

    assert validation["eligible"] is True
    assert validation["reject_codes"] == []
    assert validation["needs_recheck"] is False


def test_suspicious_high_score_sex_act_recheck_can_reject(monkeypatch):
    import amg.output.cover_validation as cv

    monkeypatch.setattr(cv, "COVER_PRESENCE_GATE_ENABLED", True)
    monkeypatch.setattr(cv, "COVER_SUSPICIOUS_RECHECK_ENABLED", True)

    entry = _entry(type_="SEX_ACT", penetration_visible=False, action_evidence="NONE", frame=object())
    cv.validate_cover_candidate(entry)
    assert entry["cover_validation"]["needs_recheck"] is True

    class _Resp:
        success = True
        raw_text = """
        PERFORMER_VISIBLE: no
        NUDITY_OR_SEX_ACT_VISIBLE: no
        EMPTY_SET: yes
        REASON_CODE: EMPTY_SET
        CONFIDENCE: 0.93
        END
        """

    class _AI:
        def score_frame(self, *_args, **_kwargs):
            return _Resp()

    stats = cv.recheck_suspicious_selected([entry], ai_client=_AI(), max_rechecks=1)

    assert stats["attempted"] == 1
    assert stats["rejected"] == 1
    assert entry["cover_validation"]["eligible"] is False
    assert "RECHECK_EMPTY_SET" in entry["cover_validation"]["reject_codes"]


def test_cover_validation_summary_counts_rejects_and_warnings():
    import amg.output.cover_validation as cv

    bad = _entry(penetration_visible=False, penetration_confidence=0.1, action_evidence="OCCLUDED")
    suspicious = _entry(type_="SEX_ACT", penetration_visible=False, action_evidence="NONE")
    cv.apply_cover_validation([bad, suspicious])

    summary = cv.summarize_cover_validation([bad, suspicious])

    assert summary["total_checked"] == 2
    assert summary["rejected"] == 1
    assert summary["suspicious"] >= 1
    assert summary["reject_counts"]["PENETRATION_TYPE_WITHOUT_VISIBLE_PENETRATION"] == 1
