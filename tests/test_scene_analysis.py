from pathlib import Path
from types import SimpleNamespace


def _entry(ts, label, score=80.0, conf=0.8, *, genre=None, subgenre=None):
    return {
        "timestamp_sec": ts,
        "score": score,
        "motion": 1.2,
        "sharpness": 610.0,
        "position_label": label,
        "position_label_confidence": conf,
        "genre_tags": genre or ["STRAIGHT"],
        "subgenre_tags": subgenre or [],
        "scored_frame": SimpleNamespace(
            score=score,
            type_="SEX_ACT",
            position_label=label,
            position_confidence=conf,
        ),
    }


def test_scene_analysis_builds_canonical_sidecar_shape(tmp_path):
    from amg.analysis.scene_analysis import build_analysis_summary, build_scene_analysis

    cover_path = tmp_path / "covers" / "01.jpg"
    saved_covers = [
        {
            "rank": 1,
            "path": cover_path,
            "timestamp_sec": 41.0,
            "score": 88.0,
            "genre_tags": ["STRAIGHT"],
            "subgenre_tags": ["POV"],
        }
    ]
    phase_results = {
        "stream_scan": {
            "segment_stats": [
                {"segment_idx": 0, "start_sec": 0.0, "end_sec": 60.0},
                {"segment_idx": 1, "start_sec": 60.0, "end_sec": 120.0},
            ]
        },
        "content_flags": {
            "flagged": True,
            "evidence": [
                {
                    "timestamp_sec": 90.0,
                    "flags": ["BLOOD"],
                    "confidence": 0.82,
                    "score": 77.0,
                }
            ],
        },
    }
    entries = [
        _entry(10.0, "DOGGY", score=82.0, conf=0.8),
        _entry(42.0, "DOGGY", score=84.0, conf=0.78),
        _entry(170.0, "ORAL_BJ", score=90.0, conf=0.91, subgenre=["BLOWJOB"]),
    ]

    analysis = build_scene_analysis(
        scene_id="scene_x",
        video_path=Path("scene.mp4"),
        metadata={"duration_sec": 240, "fps": 30, "width": 1920, "height": 1080, "codec": "h264"},
        phase_results=phase_results,
        all_scored=entries,
        candidates=entries,
        saved_covers=saved_covers,
    )

    assert analysis["schema_version"] == "1.0"
    assert analysis["source"]["resolution"] == "1920x1080"
    assert len(analysis["raw_scenes"]) == 2
    assert len(analysis["activity_curve"]) == 3
    assert analysis["thumbnail_moments"][0]["cover_rank"] == 1
    assert {s["section_tag"] for s in analysis["sections"]} == {"DOGGY", "ORAL_BJ"}
    doggy = next(s for s in analysis["sections"] if s["section_tag"] == "DOGGY")
    assert doggy["time_segments"][0]["start_seconds"] == 8.0
    assert doggy["time_segments"][0]["end_seconds"] == 44.0
    assert analysis["policy_flags"][0]["flag"] == "BLOOD"
    assert analysis["evidence"]
    assert analysis["analysis_summary"]["sections_count"] == 2

    summary = build_analysis_summary(analysis)
    assert summary["sections_count"] == 2
    assert summary["policy_flag_types"] == ["BLOOD"]
    assert summary["evidence_count"] >= 3


def test_compact_prompt_context_uses_analysis_signals():
    from amg.analysis.scene_analysis import compact_prompt_context

    ctx = compact_prompt_context(
        {
            "sections": [{"section_tag": "DOGGY"}],
            "tags": [{"tag": "POV"}],
            "thumbnail_moments": [{"timestamp_sec": 12.5, "score": 88}],
            "ocr_results": [{"full_text": "example watermark"}],
            "policy_flags": [{"flag": "URL"}],
        }
    )

    assert "DOGGY" in ctx
    assert "POV" in ctx
    assert "example watermark" in ctx
    assert "URL" in ctx


def test_scene_analysis_includes_cover_validation_evidence():
    from amg.analysis.scene_analysis import build_scene_analysis

    entry = _entry(12.0, "PENETRATION", score=96.0)
    entry["cover_validation"] = {
        "eligible": False,
        "reject_codes": ["PENETRATION_TYPE_WITHOUT_VISIBLE_PENETRATION"],
        "warnings": [],
        "penetration_confidence": 0.49,
        "validator_source": "deterministic",
    }

    analysis = build_scene_analysis(
        scene_id="scene_x",
        video_path=Path("scene.mp4"),
        metadata={"duration_sec": 120, "fps": 30, "width": 1920, "height": 1080, "codec": "h264"},
        phase_results={},
        all_scored=[entry],
        candidates=[],
        saved_covers=[],
    )

    flags = [f for f in analysis["policy_flags"] if f.get("type") == "cover_validation"]
    assert flags
    assert flags[0]["flag"] == "PENETRATION_TYPE_WITHOUT_VISIBLE_PENETRATION"
    assert any(ev.get("kind") == "cover_validation" for ev in analysis["evidence"])


def test_metadata_fact_sheet_distills_retail_signals(tmp_path):
    from amg.analysis.metadata_fact_sheet import (
        build_metadata_fact_sheet,
        compact_fact_sheet_prompt_context,
        load_metadata_fact_sheet,
        write_metadata_fact_sheet,
    )

    analysis = {
        "scene_id": "scene_x",
        "source": {"duration_sec": 120, "resolution": "1920x1080"},
        "sections": [
            {
                "section_tag": "ORAL_BJ",
                "time_segments": [{"start_seconds": 8, "end_seconds": 22}],
                "thumbnail_timestamp": 14.2,
                "confidence": 0.91,
                "relative_activity": 5.0,
                "evidence_ids": ["ev_001"],
            }
        ],
        "tags": [
            {"tag": "POV", "probability": 0.87, "count": 4},
            {"tag": "DOGGY", "probability": 0.72, "count": 2},
            {"tag": "MISSIONARY", "confidence": 0.68, "count": 1},
        ],
        "thumbnail_moments": [{"timestamp_sec": 14.2, "score": 89, "evidence_id": "ev_001"}],
        "ocr_results": [{"full_text": "example watermark"}],
        "policy_flags": [{"flag": "URL", "type": "ocr_policy", "review_only": True}],
        "evidence": [{"id": "ev_001"}],
    }

    fact = build_metadata_fact_sheet(
        analysis=analysis,
        scene_context={
            "scene_id": "scene_x",
            "studio": "StudioX",
            "performers": ["Alice Blue"],
            "scene_type": "STANDARD",
            "genres": ["POV"],
            "description": "bedroom pov scene",
        },
        saved_covers=[
            {
                "type": "SEX_ACT",
                "position_label": "DOGGY",
                "position_label_confidence": 0.8,
                "score": 88,
                "genre_tags": ["POV"],
                "subgenre_tags": ["BLOWJOB"],
            }
        ],
        insight={"setting": "bedroom", "mood": "intense"},
    )

    cats = [c["category"] for c in fact["category_candidates"]]
    tags = [t["tag"] for t in fact["tag_candidates"]]
    assert "POV" in cats
    assert "Blowjob" in cats
    assert "pov" in tags
    assert "doggy style" in tags
    assert "missionary" in tags
    assert fact["action_beats"][0]["label"] == "ORAL_BJ"
    brief = compact_fact_sheet_prompt_context(fact)
    assert "StudioX" in brief
    assert "category candidates" in brief

    out = write_metadata_fact_sheet(tmp_path, fact)
    assert out is not None
    loaded = load_metadata_fact_sheet(tmp_path)
    assert loaded["schema_version"] == "1.0"
