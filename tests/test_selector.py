"""Tests for amg.scanning.selector.LiveSelector.

The selector is the quality choke-point of the streaming pipeline. It
must:
  - drop unscored / parse-failed candidates from the pool entirely,
  - rank by fused score (AI + sharpness + zone bonuses),
  - apply a post-AI sharpness gate that catches the FALLBACK-C
    "AI loved it but it's blurry" failure mode the 2026-05-08 audit
    surfaced,
  - relax the gate (with a flag) when it would leave fewer than
    target_k qualifying picks,
  - apply min-time-gap dedup so picks span the scene.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from amg.scanning.selector import LiveSelector


@dataclass
class _ScoredStub:
    """Subset of ScoredFrame used by the selector."""
    parse_succeeded: bool = True
    score: float = 0.0


def _candidate(
    *,
    ts: float,
    ai: float,
    sharp: float,
    zone_tags=None,
    parse_succeeded: bool = True,
) -> dict:
    return {
        "timestamp_sec": float(ts),
        "scored_frame": _ScoredStub(parse_succeeded=parse_succeeded, score=ai),
        "sharpness": float(sharp),
        "zone_tags": list(zone_tags or []),
        "tier": "stream",
    }


class TestPoolFiltering:
    def test_parse_failed_excluded(self):
        sel = LiveSelector(target_k=3)
        sel.add(_candidate(ts=10, ai=90, sharp=500, parse_succeeded=False))
        sel.add(_candidate(ts=20, ai=80, sharp=500))
        out = sel.finalize()
        assert len(out["picks"]) == 1
        assert out["picks"][0]["timestamp_sec"] == 20.0

    def test_score_zero_excluded(self):
        sel = LiveSelector(target_k=3)
        sel.add(_candidate(ts=10, ai=0, sharp=500))
        sel.add(_candidate(ts=20, ai=80, sharp=500))
        out = sel.finalize()
        assert len(out["picks"]) == 1


class TestFusedScoring:
    def test_ai_dominates_when_sharpness_equal(self):
        sel = LiveSelector(target_k=2, min_gap_sec=0.5)
        a = _candidate(ts=10, ai=90, sharp=500)
        b = _candidate(ts=20, ai=70, sharp=500)
        sel.add(a)
        sel.add(b)
        picks = sel.finalize()["picks"]
        assert picks[0] is a
        assert picks[1] is b

    def test_finish_zone_bonus_lifts_pick(self):
        sel = LiveSelector(
            target_k=1,
            ai_weight=0.7,
            sharp_weight=0.3,
            zone_bonus_finish=10.0,
            min_gap_sec=0.5,
            post_ai_sharp_percentile=0.0,
        )
        non_finish = _candidate(ts=10, ai=82, sharp=500)
        finish = _candidate(ts=80, ai=80, sharp=500, zone_tags=["finish_zone"])
        sel.add(non_finish)
        sel.add(finish)
        picks = sel.finalize()["picks"]
        assert picks[0] is finish

    def test_buildup_bonus_smaller_than_finish(self):
        """Finish bonus is intentionally larger than buildup bonus —
        finishing-shot covers carry the marketing weight."""
        sel = LiveSelector(
            target_k=1,
            ai_weight=0.7,
            sharp_weight=0.3,
            zone_bonus_finish=5.0,
            zone_bonus_buildup=3.0,
            min_gap_sec=0.5,
            post_ai_sharp_percentile=0.0,
        )
        # Both score the same AI + sharpness — the bonus is the
        # tiebreaker, and finish > buildup.
        finish = _candidate(ts=80, ai=72, sharp=500, zone_tags=["finish_zone"])
        buildup = _candidate(ts=30, ai=72, sharp=500, zone_tags=["buildup_zone"])
        sel.add(finish)
        sel.add(buildup)
        picks = sel.finalize()["picks"]
        assert picks[0] is finish


class TestPostAIGate:
    def test_blurry_high_score_dropped(self):
        """The FALLBACK-C failure mode in pure form: AI loves a blurry
        frame, sharpness gate drops it in favor of a sharper but lower-
        scored alternative."""
        sel = LiveSelector(
            target_k=1,
            min_gap_sec=0.5,
            post_ai_sharp_percentile=50.0,
        )
        blurry_winner = _candidate(ts=10, ai=95, sharp=120)
        sharp_runner_up = _candidate(ts=40, ai=78, sharp=900)
        sel.add(blurry_winner)
        sel.add(sharp_runner_up)
        out = sel.finalize()
        picks = out["picks"]
        assert len(picks) == 1
        assert picks[0] is sharp_runner_up
        assert out["sharpness_floor_used"] >= 120
        assert not out["gate_relaxed"]

    def test_gate_relaxes_to_meet_target_k(self):
        """If the gate would leave us short of target_k we relax with a
        flag set, rather than ship fewer covers than the floor."""
        sel = LiveSelector(
            target_k=3,
            min_gap_sec=0.5,
            post_ai_sharp_percentile=80.0,
        )
        for i in range(5):
            sel.add(_candidate(ts=i * 10, ai=80 - i, sharp=200 + i))
        out = sel.finalize()
        assert len(out["picks"]) == 3
        assert out["gate_relaxed"] is True


class TestMinGapDedup:
    def test_picks_span_scene(self):
        sel = LiveSelector(
            target_k=2,
            min_gap_sec=15.0,
            post_ai_sharp_percentile=0.0,
        )
        sel.add(_candidate(ts=10.0, ai=95, sharp=600))
        sel.add(_candidate(ts=12.0, ai=94, sharp=600))  # within 15s — dropped
        sel.add(_candidate(ts=40.0, ai=80, sharp=600))
        picks = sel.finalize()["picks"]
        ts = sorted(p["timestamp_sec"] for p in picks)
        assert ts == [10.0, 40.0]

    def test_min_gap_overridden_when_short(self):
        """When dedup leaves us short of target_k we top off ignoring
        the gap rather than ship fewer covers."""
        sel = LiveSelector(
            target_k=2,
            min_gap_sec=15.0,
            post_ai_sharp_percentile=0.0,
        )
        sel.add(_candidate(ts=10.0, ai=95, sharp=600))
        sel.add(_candidate(ts=11.0, ai=80, sharp=600))
        picks = sel.finalize()["picks"]
        assert len(picks) == 2


class TestValidation:
    def test_target_k_must_be_positive(self):
        with pytest.raises(ValueError):
            LiveSelector(target_k=0)

    def test_finalize_with_empty_pool(self):
        sel = LiveSelector(target_k=3)
        out = sel.finalize()
        assert out["picks"] == []
        assert out["stats"]["scored_pool"] == 0
