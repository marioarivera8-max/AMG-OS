def test_build_review_form_state_prefills_from_reviewed_and_insight():
    from amg.ui.app import _build_review_form_state

    reviewed = {
        "title_override": "My chosen title",
        "title_tone": "creative",
        "long_description": "Saved long description",
        "notes": "Saved notes",
        "tags_csv": "tag1, tag2",
        "categories_csv": "cat1, cat2",
        "selected_covers": ["01_a.jpg"],
        "kept_covers": ["01_a.jpg"],
        "soft_thumbnail_review": {"decision": "keep", "score_100": 82.5},
        "per_cover": {
            "01_a.jpg": {
                "decision": "keep",
                "pen": "yes",
                "pos": "DOGGY",
                "reason": "great frame",
                "score": "91.0",
            }
        },
    }
    insight = {"ai_tags": ["ai_a"], "ai_categories": ["ai_c"], "title_tone": "edgy", "long_description": "AI desc"}
    covers = [{"filename": "01_a.jpg"}, {"filename": "02_b.jpg"}]

    state = _build_review_form_state(reviewed=reviewed, insight=insight, cover_items=covers)

    assert state["title"] == "My chosen title"
    assert state["title_tone"] == "creative"
    assert state["long_description"] == "Saved long description"
    assert state["notes"] == "Saved notes"
    assert state["tags_csv"] == "tag1, tag2"
    assert state["categories_csv"] == "cat1, cat2"
    assert state["soft_thumb_decision"] == "keep"
    assert state["soft_thumb_score"] == "82.5"
    assert state["per_cover"]["01_a.jpg"]["decision"] == "keep"
    assert state["per_cover"]["02_b.jpg"]["decision"] == ""


def test_scene_summary_normalizes_top_score_to_100_scale():
    from amg.ui.app import _scene_summary

    summary = _scene_summary(
        {
            "scene_id": "demo_scene",
            "outcomes": {"top_pick_score": 8.7, "covers_delivered": 12},
            "input": {"duration_sec": 120.0, "genres": []},
        }
    )

    assert summary["top_score"] == 87.0

