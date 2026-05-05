"""Tests for quality-based score refinement in scoring orchestrator."""

from amg.scoring.orchestrator import _sharpness_score_adjustment


def test_sharpness_adjustment_penalizes_blur():
    assert _sharpness_score_adjustment(90) < _sharpness_score_adjustment(280)
    assert _sharpness_score_adjustment(170) < 0


def test_sharpness_adjustment_rewards_sharp_frames():
    assert _sharpness_score_adjustment(700) > _sharpness_score_adjustment(450)
    assert _sharpness_score_adjustment(1100) > 0
