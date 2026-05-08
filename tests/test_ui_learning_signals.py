def test_build_metadata_learning_signals_tracks_edit_distance():
    import amg.ui.app as app_mod

    insight = {
        "ai_titles": [{"text": "Alice POV Bedroom Session"}],
        "long_description": "Alice leads an explicit POV bedroom sequence.",
        "ai_tags": ["pov", "bedroom", "blowjob"],
        "ai_categories": ["POV", "Blowjob"],
    }
    out = app_mod._build_metadata_learning_signals(
        insight=insight,
        title_text="Alice POV Bedroom Session",
        long_description="Alice leads an explicit POV bedroom sequence with closer framing.",
        tags_csv="pov, bedroom, blowjob",
        categories_csv="POV, Blowjob",
        reason_codes=["platform_fit"],
    )
    assert out["title_edit_distance"] == 0.0
    assert out["description_edit_distance"] > 0.0
    assert out["metadata_acceptance_label"] == "edited"
    assert out["metadata_edit_reason_codes"] == ["platform_fit"]


def test_review_persist_includes_learning_signals(tmp_path, monkeypatch):
    import json
    import amg.ui.app as app_mod

    monkeypatch.setattr(app_mod, "DECISION_LOGS_DIR", tmp_path / "decision_logs")
    payload = {
        "title_edit_distance": 0.1,
        "description_edit_distance": 0.2,
        "tags_edit_distance": 0.0,
        "categories_edit_distance": 0.0,
        "metadata_acceptance_label": "edited",
        "metadata_edit_reason_codes": ["style"],
    }
    app_mod._persist_review_to_decision_log(
        scene_id="demo_scene",
        title_tone="edgy",
        title_override="A Title",
        long_description="A long description",
        selected_covers=["01.jpg"],
        kept_covers=["01.jpg"],
        finalized_thumbnails=True,
        per_cover={},
        tags_csv="pov",
        categories_csv="POV",
        metadata_learning_signals=payload,
    )
    path = tmp_path / "decision_logs" / "demo_scene.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    review = data.get("review") or {}
    assert review.get("title_edit_distance") == 0.1
    assert review.get("metadata_acceptance_label") == "edited"
    assert review.get("metadata_edit_reason_codes") == ["style"]
