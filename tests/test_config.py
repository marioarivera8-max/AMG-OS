"""Tests for config helpers — cover caps, cluster windows, interval scaling."""

from amg.config import (
    get_cover_cap,
    get_cluster_window,
    get_interval_scale,
    get_adaptive_interval,
    COVER_FLOOR,
)


class TestCoverCap:
    # v11.1 bumped caps from 10/12/15/18 to 20/25/28/30 (per CHANGELOG_v11_1.md)

    def test_short_video(self):
        # Under 5 minutes
        assert get_cover_cap(60) == 20
        assert get_cover_cap(299) == 20

    def test_medium_video(self):
        # 5-25 minutes
        assert get_cover_cap(600) == 25
        assert get_cover_cap(1499) == 25

    def test_long_video(self):
        # 25-45 minutes
        assert get_cover_cap(1500) == 28
        assert get_cover_cap(2699) == 28

    def test_very_long_video(self):
        # > 45 minutes
        assert get_cover_cap(2700) == 30
        assert get_cover_cap(7200) == 30

    def test_floor_minimum(self):
        # Caps are above the floor
        for duration in [60, 600, 1500, 2700]:
            assert get_cover_cap(duration) >= COVER_FLOOR


class TestClusterWindow:
    # Windows keyed to 0–100 scores (CLUSTER_WINDOWS in config)

    def test_score_below_50_no_expansion(self):
        window, interval = get_cluster_window(40.0)
        assert window == 0
        assert interval == 0

    def test_score_50_to_79(self):
        window, interval = get_cluster_window(70.0)
        assert window == 3
        assert interval == 1

    def test_score_80_to_89(self):
        window, interval = get_cluster_window(85.0)
        assert window == 7
        assert interval == 1

    def test_score_90_to_99(self):
        window, interval = get_cluster_window(95.0)
        assert window == 20
        assert interval == 3

    def test_perfect_score(self):
        window, interval = get_cluster_window(100.0)
        assert window == 40
        assert interval == 5

    def test_boundary_50(self):
        window, interval = get_cluster_window(50.0)
        assert window == 3

    def test_boundary_80(self):
        window, interval = get_cluster_window(80.0)
        assert window == 7


class TestAdaptiveIntervals:
    def test_interval_scale_short(self):
        assert get_interval_scale(1200) == 1.0

    def test_interval_scale_medium_boundary(self):
        assert get_interval_scale(1800) == 1.8

    def test_interval_scale_long_boundary(self):
        assert get_interval_scale(3900) == 3.0

    def test_adaptive_interval_unbounded(self):
        assert get_adaptive_interval(1.0, 3900) == 3.0

    def test_adaptive_interval_capped(self):
        assert get_adaptive_interval(2.0, 3900, max_interval=4.0) == 4.0

    def test_adaptive_interval_never_below_base(self):
        assert get_adaptive_interval(2.0, 200, max_interval=1.0) == 2.0
