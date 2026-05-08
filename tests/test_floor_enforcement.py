from pathlib import Path

from amg.scanning import fallback


def test_deadline_expired_uses_cv_rescue_without_ai(monkeypatch):
    calls = {"fallback_c": 0, "fallback_d": 0}

    def fake_fallback_c(*args, **kwargs):
        calls["fallback_c"] += 1
        return []

    def fake_fallback_d(video_path, duration_sec, exclude_timestamps, target_count):
        calls["fallback_d"] += 1
        return [
            {
                "timestamp_sec": float(i + 1),
                "tier": "fallback_d",
                "scored_frame": object(),
            }
            for i in range(target_count)
        ]

    monkeypatch.setattr(fallback, "_fallback_c", fake_fallback_c)
    monkeypatch.setattr(fallback, "_fallback_d", fake_fallback_d)

    result = fallback.run_floor_enforcement_cascade(
        video_path=Path("scene.mp4"),
        duration_sec=120.0,
        current_candidates=[],
        all_scored=[],
        calibration={"tier_3_floor": 0},
        target_count=3,
        deadline_sec=0,
    )

    assert calls == {"fallback_c": 0, "fallback_d": 1}
    assert result["floor_met"] is True
    assert result["deadline_overrun"] is True
    assert result["fallbacks_used"] == ["D"]
    assert len(result["final_candidates"]) == 3
