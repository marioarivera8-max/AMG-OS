import json


def _write_args(scene_path):
    return dict(
        scene_id="scene_x",
        scene_path=scene_path,
        metadata={"duration_sec": 120, "fps": 30, "width": 1920, "height": 1080},
        studio_info={"name": "studio"},
        performer_info={"code": "BG", "total": 2},
        title_info={"primary_scene_type": "BG", "detected_genres": ["POV"]},
        calibration={"tier_1_floor": 10, "tier_2_floor": 8, "tier_3_floor": 7, "samples_collected": 10},
        phase_results={"tier_scan": {"duration_sec": 1.2, "candidates_found": 1, "frames_scored": 1}},
        final_candidates=[],
        saved_covers=[],
        fallbacks_used=[],
        error_codes=[],
        total_duration_sec=3.0,
    )


def test_write_decision_log_returns_none_on_write_failure(monkeypatch, tmp_path):
    import amg.output.decision_log as dl

    monkeypatch.setattr(dl, "DECISION_LOGS_DIR", tmp_path / "decision_logs")
    scene_path = tmp_path / "scene.mp4"
    scene_path.write_text("x")
    monkeypatch.setattr(dl.json, "dump", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    out = dl.write_decision_log(**_write_args(scene_path))
    assert out is None


def test_decision_log_records_sensitive_review_flags(monkeypatch, tmp_path):
    import amg.output.decision_log as dl

    monkeypatch.setattr(dl, "DECISION_LOGS_DIR", tmp_path / "decision_logs")
    scene_path = tmp_path / "scene.mp4"
    scene_path.write_text("x")
    args = _write_args(scene_path)
    args["phase_results"]["content_flags"] = {
        "flagged": True,
        "flags": ["BLOOD", "URINE"],
        "max_confidence": 0.84,
        "max_confidence_by_flag": {"BLOOD": 0.84, "URINE": 0.71},
        "counts_by_flag": {"BLOOD": 2, "URINE": 1},
        "evidence": [{"timestamp_sec": 12.0, "flags": ["BLOOD"], "confidence": 0.84}],
    }

    out = dl.write_decision_log(**args)

    assert out is not None
    record = json.loads(out.read_text())
    assert record["review_flags"]["requires_review"] is True
    assert record["review_flags"]["sensitive_content"]["flags"] == ["BLOOD", "URINE"]
    assert record["review_flags"]["sensitive_content"]["counts_by_flag"]["BLOOD"] == 2


def test_decision_log_records_analysis_reference(monkeypatch, tmp_path):
    import amg.output.decision_log as dl

    monkeypatch.setattr(dl, "DECISION_LOGS_DIR", tmp_path / "decision_logs")
    scene_path = tmp_path / "scene.mp4"
    scene_path.write_text("x")
    analysis_path = tmp_path / "work" / "scene_analysis.json"
    args = _write_args(scene_path)
    args["analysis_path"] = analysis_path
    args["analysis_summary"] = {
        "sections_count": 2,
        "top_section_tags": ["DOGGY", "ORAL_BJ"],
        "policy_flags_count": 1,
    }

    out = dl.write_decision_log(**args)

    assert out is not None
    record = json.loads(out.read_text())
    assert record["analysis_path"] == str(analysis_path)
    assert record["analysis_summary"]["sections_count"] == 2
    assert record["analysis_summary"]["top_section_tags"] == ["DOGGY", "ORAL_BJ"]


def test_decision_log_records_cover_validation_summary(monkeypatch, tmp_path):
    import amg.output.decision_log as dl

    monkeypatch.setattr(dl, "DECISION_LOGS_DIR", tmp_path / "decision_logs")
    scene_path = tmp_path / "scene.mp4"
    scene_path.write_text("x")
    args = _write_args(scene_path)
    args["phase_results"]["cover_validation"] = {
        "enabled": True,
        "mode": "hard_block",
        "total_checked": 3,
        "eligible": 2,
        "rejected": 1,
        "suspicious": 1,
        "reject_counts": {"PENETRATION_TYPE_WITHOUT_VISIBLE_PENETRATION": 1},
        "warning_counts": {"HIGH_SCORE_WEAK_ACTION_EVIDENCE": 1},
        "recheck": {"attempted": 1, "rejected": 1},
    }

    out = dl.write_decision_log(**args)

    assert out is not None
    record = json.loads(out.read_text())
    summary = record["cover_validation_summary"]
    assert summary["enabled"] is True
    assert summary["rejected"] == 1
    assert summary["rechecked"] == 1
    assert record["review_flags"]["requires_review"] is True
    assert record["outcomes"]["cover_validation_summary"]["recheck_rejected"] == 1
