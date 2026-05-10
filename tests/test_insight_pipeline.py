import json
from types import SimpleNamespace
from pathlib import Path


class _FakeClient:
    def __init__(self):
        self.vision_model = "vision-test"
        self.text_model = "text-test"

    def is_alive(self):
        return False

    def generate_text(self, _prompt):
        class _R:
            success = False
            raw_text = ""
        return _R()


def test_generate_scene_insight_payload_includes_validation_telemetry(monkeypatch, tmp_path):
    import amg.scoring.insight_pipeline as mod
    captured = {}

    monkeypatch.setattr(
        mod,
        "resolve_folder_context",
        lambda _p: SimpleNamespace(
            studio="StudioX",
            performers=["Performer A"],
            title="Scene title",
            is_generic_filename=False,
            source_folder=tmp_path,
            metadata_documents=[],
            ancestor_names=["StudioX"],
        ),
    )
    monkeypatch.setattr(mod, "detect_studio", lambda _p: "StudioX")
    monkeypatch.setattr(mod, "get_or_create_profile", lambda _s: {})
    monkeypatch.setattr(mod, "parse_performer_code_with_context", lambda *_: {"total": 1, "code": "BG"})
    monkeypatch.setattr(mod, "parse_title_with_context", lambda *_: {"detected_genres": ["POV"], "description": "desc", "metadata_title": "meta"})
    monkeypatch.setattr(mod, "derive_primary_scene_type", lambda *_: "STANDARD")
    monkeypatch.setattr(mod, "detect_scene_type_from_code", lambda *_: "STANDARD")
    monkeypatch.setattr(
        mod,
        "describe_scene_from_covers",
        lambda **_: (_ for _ in ()).throw(AssertionError("scene insight should be skipped")),
    )
    monkeypatch.setattr(mod, "summarize_positions", lambda *_: {})
    def _fake_generate_titles(**kwargs):
        captured["title_kwargs"] = kwargs
        return {
            "titles": [{"text": "POV Scene With Performer"}],
            "long_description": "short",
            "categories": ["POV"],
            "tags": ["pov"],
            "title_tone": "edgy",
            "ai_used": True,
            "retrieval_stage": "titles",
            "retrieval_scope": "titles",
            "retrieved_examples_count": 2,
        }

    monkeypatch.setattr(mod, "generate_titles_with_insight", _fake_generate_titles)

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"x")

    payload = mod.generate_scene_insight_payload(
        video_path=video,
        saved_covers=[{"path": str(cover)}],
        work_dir=tmp_path / "work",
        ai_client=_FakeClient(),
        persist=False,
        include_scene_insight=False,
    )
    assert payload["vision_model_used"] == "vision-test"
    assert payload["text_model_used"] == "text-test"
    assert payload["text_model_primary"] == "text-test"
    assert payload["text_model_fallback_used"] is False
    assert "rule_pack_id" in payload
    assert "rule_pack_applied" in payload
    assert "rule_pack_mode" in payload
    assert "metadata_blockers_initial" in payload
    assert "metadata_blockers_final" in payload
    assert "repair_attempts" in payload
    assert payload["retrieval_stage"] == "titles"
    assert payload["retrieval_scope"] == "titles"
    assert payload["retrieved_examples_count"] == 2
    assert payload["metadata_fact_sheet_path"] is None
    assert payload["scene_insight_enabled"] is False
    assert "POV" in payload["metadata_fact_sheet_summary"]["category_candidates"]
    assert "pov" in payload["metadata_fact_sheet_summary"]["tag_candidates"]
    assert captured["title_kwargs"]["metadata_fact_sheet"]["source"]["studio"] == "StudioX"
    assert "category candidates" in captured["title_kwargs"]["analysis_context"]


def test_scene_filename_performers_drive_publishable_fallback_metadata(tmp_path):
    import amg.scoring.insight_pipeline as mod

    scene_dir = tmp_path / (
        "045_Gizelle__Kazumi__Nicole_Hardcore_Squirting_Orgy_"
        "045_Gizelle, Kazumi, Nicole_Hardcore Squirting Orgy"
    )
    scene_dir.mkdir()
    video = scene_dir / "clip_001.mp4"
    video.write_bytes(b"x")
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "scene_analysis.json").write_text(
        json.dumps(
            {
                "scene_id": scene_dir.name,
                "source": {"duration_sec": 2346.12, "resolution": "3840x2160"},
                "sections": [
                    {"section_tag": "GANGBANG_CIRCLE", "confidence": 0.98, "relative_activity": 8.0},
                    {"section_tag": "GROUP", "confidence": 0.98, "relative_activity": 7.5},
                ],
                "tags": [
                    {"tag": "ORGY", "probability": 0.98, "count": 35},
                    {"tag": "GANGBANG", "probability": 0.98, "count": 28},
                    {"tag": "GANGBANG_CIRCLE", "probability": 0.98, "count": 18},
                    {"tag": "GROUP", "probability": 0.98, "count": 17},
                    {"tag": "ORAL_FOCUSED", "probability": 0.95, "count": 1},
                    {"tag": "SQUIRTING", "probability": 0.95, "count": 1},
                    {"tag": "NUDE", "probability": 0.9, "count": 3},
                    {"tag": "SEX_ACT", "probability": 0.9, "count": 3},
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = mod.generate_scene_insight_payload(
        video_path=video,
        saved_covers=[],
        work_dir=work_dir,
        ai_client=_FakeClient(),
        persist=False,
        include_scene_insight=False,
    )

    assert payload["performers"] == ["Gizelle", "Kazumi", "Nicole"]
    assert payload["ai_used"] is False
    assert payload["text_generation_status"] == "ai_offline"
    assert payload["metadata_validation_passed"] is True
    assert payload["metadata_warnings_final"] == []
    assert payload["ai_titles"][0]["text"] == "Gizelle, Kazumi & Nicole in a Hardcore Squirting Orgy"
    assert "Unknown" not in payload["ai_titles"][0]["text"]
    tags = {t.lower() for t in payload["ai_tags"]}
    assert {"gangbang", "orgy", "squirting", "group sex"} <= tags
    assert not {"pov", "deepthroat", "big ass", "big tits", "big cock"} & tags
    assert "HD Porn" in payload["ai_categories"]
    assert "POV" not in payload["ai_categories"]
