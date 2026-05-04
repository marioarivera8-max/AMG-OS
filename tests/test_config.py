"""Tests for config helpers — cover caps, cluster windows."""
import pytest

from amg.config import get_cover_cap, get_cluster_window, COVER_FLOOR


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
    # v11.1 widened windows from ±2/5/15/30s to ±3/7/20/40s (per CHANGELOG_v11_1.md)

    def test_score_below_5_no_expansion(self):
        window, interval = get_cluster_window(4.0)
        assert window == 0
        assert interval == 0

    def test_score_5_to_8(self):
        window, interval = get_cluster_window(7.0)
        assert window == 3
        assert interval == 1

    def test_score_8_to_9(self):
        window, interval = get_cluster_window(8.5)
        assert window == 7
        assert interval == 1

    def test_score_9_to_10(self):
        window, interval = get_cluster_window(9.5)
        assert window == 20
        assert interval == 3

    def test_perfect_score(self):
        window, interval = get_cluster_window(10.0)
        assert window == 40
        assert interval == 5

    def test_boundary_5(self):
        window, interval = get_cluster_window(5.0)
        assert window == 3

    def test_boundary_8(self):
        window, interval = get_cluster_window(8.0)
        assert window == 7
