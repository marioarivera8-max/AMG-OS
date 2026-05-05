from types import SimpleNamespace

import numpy as np


def test_process_scene_mocked_smoke(monkeypatch, tmp_path):
    import amg.pipeline as p

    video_path = tmp_path / "scene" / "clip.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_text("stub")

    folder_ctx = SimpleNamespace(
        is_generic_filename=False,
        ancestor_names=[],
        source_folder=video_path.parent,
        metadata_documents=[],
        studio="DemoStudio",
        performers=["PerformerA"],
        location=None,
        title="Demo title",
    )
    monkeypatch.setattr(p, "resolve_folder_context", lambda _: folder_ctx)
    monkeypatch.setattr(p, "detect_studio", lambda _: "DemoStudio")
    monkeypatch.setattr(
        p,
        "get_or_create_profile",
        lambda _: {"performers": {"regular": ["PerformerA"]}, "primary_language": "en", "display_name": "Demo"},
    )
    monkeypatch.setattr(
        p,
        "parse_performer_code_with_context",
        lambda *_: {
            "code": "BG",
            "male_count": 1,
            "female_count": 1,
            "total": 2,
            "is_solo_female": False,
            "is_solo_male": False,
            "is_lesbian": False,
            "is_gay": False,
            "is_couple": True,
            "is_threesome": False,
            "is_foursome": False,
            "is_gangbang": False,
            "is_mixed": True,
            "flag_for_review": False,
            "flag_reason": None,
        },
    )
    monkeypatch.setattr(
        p,
        "parse_title_with_context",
        lambda *_: {"detected_genres": ["POV"], "description": "desc", "metadata_title": "meta"},
    )
    monkeypatch.setattr(p, "verify_2257", lambda *_: {"should_block": False})
    monkeypatch.setattr(
        p,
        "get_metadata",
        lambda *_: {
            "duration_sec": 120.0,
            "fps": 30,
            "width": 1920,
            "height": 1080,
            "codec": "h264",
            "size_gb": 0.1,
            "has_audio": True,
        },
    )
    monkeypatch.setattr(
        p,
        "calibrate_thresholds",
        lambda *_: {
            "tier_1_floor": 10.0,
            "tier_2_floor": 8.0,
            "tier_3_floor": 7.0,
            "samples_collected": 10,
            "all_blurry": False,
        },
    )

    class _AI:
        def is_alive(self):
            return True

    monkeypatch.setattr(p, "AIClient", _AI)
    monkeypatch.setattr(p, "build_scoring_prompt", lambda **_: "prompt")

    scored = SimpleNamespace(
        score=88.0,
        parse_succeeded=True,
        type_="PENETRATION",
        gaze="DIRECT",
        penetration_visible=True,
        penetration_confidence=0.9,
        action_evidence="EXPLICIT_PENETRATION",
    )
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    entry = {"timestamp_sec": 10.0, "frame": frame, "scored_frame": scored, "tier": "tier_1"}

    monkeypatch.setattr(
        p,
        "run_tiered_scan",
        lambda *_, **__: {"tier_used": 1, "candidates": [entry], "all_scored": [entry], "aborted": False},
    )
    monkeypatch.setattr(p, "quota_satisfied", lambda *_: True)
    monkeypatch.setattr(p, "get_cover_cap", lambda *_: 1)
    monkeypatch.setattr(p, "COVER_FLOOR", 1)
    monkeypatch.setattr(p, "classify_candidate_positions", lambda *_args, **_kwargs: {"classified": 1})
    monkeypatch.setattr(p, "select_quota_fill", lambda cand, **_: (cand, {"selected_total": len(cand)}))
    monkeypatch.setattr(p, "make_work_dir", lambda *_args, **_kwargs: tmp_path / "work")
    monkeypatch.setattr(p, "make_covers_dir", lambda wd: wd / "covers")
    monkeypatch.setattr(
        p,
        "save_covers",
        lambda *_args, **_kwargs: [
            {
                "path": str(tmp_path / "work" / "covers" / "01.jpg"),
                "score": 88.0,
                "type": "PENETRATION",
                "timestamp_sec": 10.0,
            }
        ],
    )
    monkeypatch.setattr(p, "score_and_save_provided_thumbnails", lambda **_: ([], {"discovered": 0, "scanned": 0, "accepted": 0, "imported": 0}))
    monkeypatch.setattr(p, "build_contact_sheet", lambda *_, **__: None)
    monkeypatch.setattr(
        p,
        "_generate_scene_insight_and_titles",
        lambda **_: (
            {"setting": "indoor", "notable_features": [], "action_summary": "", "mood": "", "location_hint": "", "raw_text": ""},
            {"titles": [], "long_description": "", "title_tone": "retail_safe", "categories": [], "tags": [], "ai_used": False},
        ),
    )
    monkeypatch.setattr(p, "SOFT_THUMB_ENABLED", False)
    monkeypatch.setattr(p, "write_decision_log", lambda **_: tmp_path / "decision.json")
    monkeypatch.setattr(p, "record_scene_outcome", lambda **_: None)
    monkeypatch.setattr(p, "audit_event", lambda *_, **__: None)
    monkeypatch.setattr(p, "init_logging", lambda **_: None)
    monkeypatch.setattr(p, "log", SimpleNamespace(info=lambda *a, **k: None, warn=lambda *a, **k: None, error=lambda *a, **k: None))

    result = p.process_scene(video_path)
    assert result["success"] is True
    assert result["covers_saved"] == 1
    assert result["decision_log_path"] == tmp_path / "decision.json"
