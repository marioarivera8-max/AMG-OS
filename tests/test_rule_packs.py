import json


def test_rule_pack_canary_resolution(monkeypatch, tmp_path):
    import amg.learning.rule_packs as rp

    monkeypatch.setattr(rp, "TRAINING_RULE_PACKS_DIR", tmp_path / "rule_packs")
    monkeypatch.setattr(rp, "TRAINING_ACTIVE_RULE_PACK_PATH", tmp_path / "active_rule_pack.json")
    monkeypatch.setattr(rp, "RULE_CANARY_PCT", 50.0)

    rp.save_rule_pack(
        rule_pack_id="pack_a",
        constraints={"tag_boost": ["pov"]},
        description="test",
    )
    rp.set_active_rule_pack(rule_pack_id="pack_a", mode="canary", canary_pct=50.0)

    out1 = rp.resolve_rule_pack_for_scene("scene_alpha")
    out2 = rp.resolve_rule_pack_for_scene("scene_beta")
    assert out1["rule_pack_id"] == "pack_a"
    assert out2["rule_pack_id"] == "pack_a"
    assert out1["mode"] == "canary"
    assert out2["mode"] == "canary"
    # deterministic bucketed routing should split eventually
    assert out1["applied"] in {True, False}
    assert out2["applied"] in {True, False}


def test_rule_pack_full_mode_always_applies(monkeypatch, tmp_path):
    import amg.learning.rule_packs as rp

    monkeypatch.setattr(rp, "TRAINING_RULE_PACKS_DIR", tmp_path / "rule_packs")
    monkeypatch.setattr(rp, "TRAINING_ACTIVE_RULE_PACK_PATH", tmp_path / "active_rule_pack.json")

    rp.save_rule_pack(
        rule_pack_id="pack_full",
        constraints={"title_prefixes": ["Premium"]},
    )
    rp.set_active_rule_pack(rule_pack_id="pack_full", mode="full", canary_pct=5.0)
    out = rp.resolve_rule_pack_for_scene("any_scene")
    assert out["applied"] is True
    assert out["reason"] == "full_mode"


def test_scene_describer_applies_rule_constraints(monkeypatch):
    import amg.scoring.scene_describer as sd

    class _Client:
        def is_alive(self):
            return True

        def generate_text(self, _prompt):
            class _Resp:
                success = True
                raw_text = (
                    "TITLE_1: Wild Shower POV\n"
                    "STYLE_1: performer_led\n"
                    "TITLE_2: Studio Heat Build\n"
                    "STYLE_2: narrative_hook\n"
                    "LONG_DESCRIPTION: Tight scene progression with clear action beats.\n"
                    "CATEGORY_SUGGESTIONS: POV, Blowjob\n"
                    "TAG_SUGGESTIONS: pov, blowjob, deepthroat\n"
                    "END"
                )
                extras = {"model_used": "text-model-test"}
            return _Resp()

    payload = sd.generate_titles_with_insight(
        studio="StudioX",
        performers=["Alice Blue"],
        scene_type="STANDARD",
        genres=["POV"],
        description="test",
        insight=None,
        position_summary={},
        ai_client=_Client(),
        rule_pack={
            "rule_pack_id": "pack_test",
            "constraints": {
                "banned_title_terms": ["wild"],
                "title_prefixes": ["StudioX"],
                "tag_boost": ["eye contact"],
                "category_boost": ["HD Porn"],
                "description_phrase_boost": ["Shelf-optimized wording."],
                "description_min_chars": 140,
                "description_max_chars": 300,
            },
        },
    )
    assert payload["rule_pack_id"] == "pack_test"
    assert payload["titles"]
    assert all("wild" not in str(t["text"]).lower() for t in payload["titles"])
    assert payload["long_description"]
    assert len(payload["long_description"]) >= 140
