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
