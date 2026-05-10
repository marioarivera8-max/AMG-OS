def test_blur_rescue_selects_closest_frame_that_clears_floor(monkeypatch):
    import amg.output.covers as covers

    sharpness = {
        "base": 90.0,
        "far_sharpest": 520.0,
        "closest_clear": 260.0,
    }
    monkeypatch.setattr(covers, "measure_sharpness", lambda frame: sharpness[frame])
    monkeypatch.setattr(covers, "is_frame_too_dark", lambda _frame: False)
    monkeypatch.setattr(covers, "COVER_BLUR_RESCUE_SHARPNESS_FLOOR", 250.0)
    monkeypatch.setattr(covers, "COVER_BLUR_RESCUE_OFFSETS_SEC", (-0.50, 0.25, 0.75))

    ts, frame, info = covers._rescue_blurry_frame(
        100.0,
        "base",
        nearby_frames_by_ts={
            99.5: "far_sharpest",
            100.25: "closest_clear",
        },
    )

    assert ts == 100.25
    assert frame == "closest_clear"
    assert info["applied"] is True
    assert info["sharpness_before"] == 90.0
    assert info["sharpness_after"] == 260.0


def test_blur_rescue_leaves_already_sharp_frame(monkeypatch):
    import amg.output.covers as covers

    monkeypatch.setattr(covers, "measure_sharpness", lambda _frame: 300.0)
    monkeypatch.setattr(covers, "COVER_BLUR_RESCUE_SHARPNESS_FLOOR", 250.0)

    ts, frame, info = covers._rescue_blurry_frame(
        50.0,
        "base",
        nearby_frames_by_ts={50.25: "nearby"},
    )

    assert ts == 50.0
    assert frame == "base"
    assert info["applied"] is False
    assert info["reason"] == "already_sharp"


def test_lazy_nearby_decode_skips_already_sharp_blur_rescue(monkeypatch):
    import amg.output.covers as covers

    class _Scored:
        score = 96.0

    monkeypatch.setattr(covers, "COVER_BLUR_RESCUE_ENABLED", True)
    monkeypatch.setattr(covers, "COVER_BLUR_RESCUE_MIN_SCORE", 60.0)
    monkeypatch.setattr(covers, "COVER_BLUR_RESCUE_SHARPNESS_FLOOR", 220.0)
    monkeypatch.setattr(covers, "COVER_NEARBY_POLISH_ENABLED", False)

    candidates = [
        {"timestamp_sec": 10.0, "scored_frame": _Scored()},
        {"timestamp_sec": 20.0, "scored_frame": _Scored()},
    ]
    base_sharpness = {0: 300.0, 1: 500.0}

    assert covers._collect_needed_nearby_timestamps(candidates, base_sharpness) == []


def test_lazy_nearby_decode_fetches_only_blurry_rescue_candidates(monkeypatch):
    import amg.output.covers as covers

    class _Scored:
        score = 96.0

    monkeypatch.setattr(covers, "COVER_BLUR_RESCUE_ENABLED", True)
    monkeypatch.setattr(covers, "COVER_BLUR_RESCUE_MIN_SCORE", 60.0)
    monkeypatch.setattr(covers, "COVER_BLUR_RESCUE_SHARPNESS_FLOOR", 220.0)
    monkeypatch.setattr(covers, "COVER_BLUR_RESCUE_OFFSETS_SEC", (-0.25, 0.25))
    monkeypatch.setattr(covers, "COVER_NEARBY_POLISH_ENABLED", False)

    candidates = [
        {"timestamp_sec": 10.0, "scored_frame": _Scored()},
        {"timestamp_sec": 20.0, "scored_frame": _Scored()},
    ]
    base_sharpness = {0: 150.0, 1: 500.0}

    assert covers._collect_needed_nearby_timestamps(candidates, base_sharpness) == [9.75, 10.25]
