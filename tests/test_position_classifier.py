from types import SimpleNamespace


def _candidate(label="OTHER", conf=0.0, *, type_="PENETRATION", score=90.0):
    scored = SimpleNamespace(
        parse_succeeded=True,
        score=score,
        type_=type_,
        penetration_visible=True,
        penetration_confidence=0.95,
        action_evidence="EXPLICIT_PENETRATION",
        position_label=label,
        position_confidence=conf,
    )
    return {
        "timestamp_sec": 10.0,
        "frame": object(),
        "scored_frame": scored,
        "position_label": label,
        "position_label_confidence": conf,
    }


def test_classifier_skips_already_confident_position():
    import amg.scoring.position_classifier as pc

    entry = _candidate(label="DOGGY_STYLE", conf=0.9)

    assert pc._is_candidate_worthy(entry) is False


def test_classifier_accepts_uncertain_high_value_candidate():
    import amg.scoring.position_classifier as pc

    entry = _candidate(label="OTHER", conf=0.0)

    assert pc._is_candidate_worthy(entry) is True


def test_classifier_prompt_discourages_other_overuse():
    import amg.scoring.position_classifier as pc

    prompt = pc._build_prompt()

    assert "Use OTHER only" in prompt
    assert "oral/toy/group" in prompt
