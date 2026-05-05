import json


def test_append_feedback_rows_includes_soft_thumbnail_feedback(tmp_path, monkeypatch):
    import amg.ui.app as app_mod

    feedback_path = tmp_path / "feedback.jsonl"
    monkeypatch.setattr(app_mod, "OPERATOR_FEEDBACK_PATH", feedback_path)
    monkeypatch.setattr(app_mod, "OPERATOR_FEEDBACK_DIR", tmp_path)

    rows_written = app_mod._append_feedback_rows(
        scene_id="scene_soft_1",
        cover_items=[],
        soft_thumbnail={
            "path": tmp_path / "00_soft_thumbnail.jpg",
            "score": 82.4,
            "timestamp_sec": 19.2,
        },
        form={
            "soft_thumb_decision": "keep",
            "soft_thumb_score": "91",
        },
        title_override=None,
        title_tone="edgy",
        notes="great soft-safe option",
    )

    assert rows_written == 1
    lines = feedback_path.read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["model"]["type"] == "SOFT_THUMBNAIL"
    assert row["operator"]["decision"] == "keep"
    assert row["operator"]["soft_thumb_decision"] == "keep"
    assert row["operator"]["soft_thumb_score_100"] == 91.0
    assert row["model"]["score_100"] == 82.4


def test_append_feedback_rows_skips_soft_thumbnail_when_empty(tmp_path, monkeypatch):
    import amg.ui.app as app_mod

    feedback_path = tmp_path / "feedback.jsonl"
    monkeypatch.setattr(app_mod, "OPERATOR_FEEDBACK_PATH", feedback_path)
    monkeypatch.setattr(app_mod, "OPERATOR_FEEDBACK_DIR", tmp_path)

    rows_written = app_mod._append_feedback_rows(
        scene_id="scene_soft_2",
        cover_items=[],
        soft_thumbnail={
            "path": tmp_path / "00_soft_thumbnail.jpg",
            "score": 70.0,
            "timestamp_sec": 1.0,
        },
        form={},
        title_override=None,
        title_tone="edgy",
        notes=None,
    )

    assert rows_written == 0
    assert feedback_path.exists()
    assert feedback_path.read_text() == ""
