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
