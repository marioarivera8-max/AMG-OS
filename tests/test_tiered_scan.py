from types import SimpleNamespace

import numpy as np


def test_single_pass_scan_scores_ranked_spaced_candidates(monkeypatch, tmp_path):
    import amg.scanning.tiered as tiered

    monkeypatch.setattr(tiered, "TIER_SCAN_MODE", "single_pass")
    monkeypatch.setattr(tiered, "SINGLE_PASS_MAX_AI_FRAMES", 2)
    monkeypatch.setattr(tiered, "SINGLE_PASS_MIN_GAP_SEC", 20.0)
    monkeypatch.setattr(tiered, "TIER_SCAN_MAX_EXTRACTED_FRAMES_PER_TIER", 10)
    monkeypatch.setattr(tiered, "TIER_SCAN_MAX_WALL_SEC_PER_TIER", 0)
    monkeypatch.setattr(tiered, "is_frame_too_dark", lambda _frame: False)
    monkeypatch.setattr(tiered, "measure_sharpness", lambda frame: float(frame[0, 0, 0]))
    monkeypatch.setattr(tiered, "measure_motion", lambda *_: 0.0)
    monkeypatch.setattr(tiered, "deduplicate_frames", lambda frames, **_: frames)

    frames = [
        (0.0, np.full((4, 4, 3), 90, dtype=np.uint8)),
        (5.0, np.full((4, 4, 3), 95, dtype=np.uint8)),
        (30.0, np.full((4, 4, 3), 80, dtype=np.uint8)),
    ]

    class FakeReader:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_frames_sequential(self, *_args):
            yield from frames

    def fake_score(batch, *_args, **_kwargs):
        scored = []
        for entry in batch:
            entry = dict(entry)
            entry["scored_frame"] = SimpleNamespace(parse_succeeded=True, score=90.0)
            scored.append(entry)
        return scored

    monkeypatch.setattr(tiered, "VideoReader", FakeReader)
    monkeypatch.setattr(tiered, "score_frames_parallel", fake_score)

    result = tiered.run_tiered_scan(
        tmp_path / "scene.mp4",
        60.0,
        {"tier_1_floor": 85.0, "tier_2_floor": 70.0, "tier_3_floor": 50.0},
        "prompt",
    )

    assert result["scan_mode"] == "single_pass"
    assert len(result["all_scored"]) == 2
    assert [round(f["timestamp_sec"]) for f in result["all_scored"]] == [5, 30]
    assert result["tier_stats"]["single_pass"]["ai_scored_count"] == 2
    assert result["tier_stats"]["single_pass"]["ai_capped"] is True
