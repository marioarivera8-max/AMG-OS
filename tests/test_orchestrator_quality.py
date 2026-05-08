"""Tests for quality-based score refinement in scoring orchestrator."""

import time

import numpy as np

from amg.scoring.orchestrator import (
    _sharpness_score_adjustment,
    get_last_scoring_stats,
    score_frames_parallel,
)


def test_sharpness_adjustment_penalizes_blur():
    assert _sharpness_score_adjustment(90) < _sharpness_score_adjustment(280)
    assert _sharpness_score_adjustment(170) < 0


def test_sharpness_adjustment_rewards_sharp_frames():
    assert _sharpness_score_adjustment(700) > _sharpness_score_adjustment(450)
    assert _sharpness_score_adjustment(1100) > 0


def test_score_frames_parallel_marks_unsubmitted_frames_as_timeout_partial():
    class FakeClient:
        def score_frame(self, *_args, **_kwargs):
            raise AssertionError("expired deadline should not submit AI calls")

    frames = [
        {"timestamp_sec": 1.0, "frame": np.zeros((8, 8, 3), dtype=np.uint8)},
        {"timestamp_sec": 2.0, "frame": np.zeros((8, 8, 3), dtype=np.uint8)},
    ]

    scored = score_frames_parallel(
        frames,
        "prompt",
        ai_client=FakeClient(),
        deadline_sec=time.time() - 1,
    )

    assert [f["ai_response"].error_code for f in scored] == [
        "E_TIMEOUT_PARTIAL",
        "E_TIMEOUT_PARTIAL",
    ]
    assert get_last_scoring_stats()["skipped_count"] == 2
    assert get_last_scoring_stats()["submitted_count"] == 0


def test_score_frames_parallel_records_worker_exceptions():
    class FakeClient:
        def score_frame(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    frames = [{"timestamp_sec": 1.0, "frame": np.zeros((8, 8, 3), dtype=np.uint8)}]

    scored = score_frames_parallel(
        frames,
        "prompt",
        ai_client=FakeClient(),
        max_workers=1,
    )

    assert scored[0]["ai_response"].error_code == "E_AI_WORKER_EXCEPTION"
    assert get_last_scoring_stats()["worker_failures"] == 1
