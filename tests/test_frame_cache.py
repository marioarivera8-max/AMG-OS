"""Tests for amg.video.frame_cache.FrameCache.

The cache is the bridge between the streaming scan (which already
decoded a frame to compute sharpness) and the output phase (which
otherwise re-decodes the same frame to save the cover). These tests
pin: byte-LRU eviction, partial-hit accounting, upgrade semantics,
and rounded-timestamp keying — the same float can come from two
different code paths with different float-to-string trip noise.
"""
import numpy as np
import pytest

from amg.video.frame_cache import FrameCache


def _frame(h: int = 4, w: int = 4) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


class TestFrameCacheBasics:
    def test_round_trip(self):
        cache = FrameCache(max_bytes=10_000_000)
        f = _frame()
        cache.put(1.234, full_frame=f)
        assert cache.get_full(1.234) is f
        assert len(cache) == 1
        assert 1.234 in cache

    def test_rounded_timestamp_key(self):
        cache = FrameCache(max_bytes=10_000_000)
        f = _frame()
        cache.put(1.2345678, full_frame=f)
        # Same key after rounding to ms.
        assert cache.get_full(1.2345002) is f

    def test_get_full_miss_returns_none_and_counts(self):
        cache = FrameCache(max_bytes=10_000_000)
        assert cache.get_full(99.0) is None
        stats = cache.stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.0

    def test_partial_hit_counts_as_miss(self):
        """An analysis-only entry can't satisfy a get_full() request, and
        the cache must report that as a miss so hit_rate honestly
        reflects the output phase's experience."""
        cache = FrameCache(max_bytes=10_000_000)
        cache.put(2.0, analysis_frame=_frame())
        assert cache.get_full(2.0) is None
        stats = cache.stats()
        assert stats["misses"] == 1
        assert stats["hits"] == 0


class TestFrameCacheUpgrade:
    def test_upgrade_preserves_analysis(self):
        cache = FrameCache(max_bytes=10_000_000)
        analysis = _frame(h=2, w=2)
        full = _frame(h=8, w=8)
        cache.put(1.0, analysis_frame=analysis)
        cache.upgrade_to_full(1.0, full)
        assert cache.get_full(1.0) is full
        assert cache.get_analysis(1.0) is analysis

    def test_put_with_none_does_not_clobber(self):
        cache = FrameCache(max_bytes=10_000_000)
        full = _frame(h=8, w=8)
        cache.put(1.0, full_frame=full)
        # Caller "puts" with both None — nothing happens.
        cache.put(1.0)
        assert cache.get_full(1.0) is full


class TestFrameCacheEviction:
    def test_byte_bounded_lru_eviction(self):
        # 4x4x3 uint8 = 48 bytes per frame; cap at 100 bytes ⇒ holds 2.
        cache = FrameCache(max_bytes=100)
        f1 = _frame()
        f2 = _frame()
        f3 = _frame()
        cache.put(1.0, full_frame=f1)
        cache.put(2.0, full_frame=f2)
        # Touch 1.0 so 2.0 is the LRU victim.
        assert cache.get_full(1.0) is f1
        cache.put(3.0, full_frame=f3)
        assert cache.get_full(2.0) is None
        assert cache.get_full(1.0) is f1
        assert cache.get_full(3.0) is f3
        stats = cache.stats()
        assert stats["evictions"] >= 1

    def test_discard(self):
        cache = FrameCache(max_bytes=10_000_000)
        cache.put(1.0, full_frame=_frame())
        cache.discard(1.0)
        assert 1.0 not in cache
        assert cache.current_bytes == 0


class TestFrameCacheValidation:
    def test_max_bytes_must_be_positive(self):
        with pytest.raises(ValueError):
            FrameCache(max_bytes=0)
