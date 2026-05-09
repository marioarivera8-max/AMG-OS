"""
Bounded LRU cache of decoded frames keyed by rounded timestamp.

Used by the streaming scanner (``amg/scanning/stream.py``) to retain
full-resolution frames the decoder produced as a side-effect of the scan
pass. The output phase (``amg/output/covers.py``) consults this cache
before re-extracting from disk, eliminating the 6+ minutes the classic
pipeline spent re-decoding the same video for cover save + nearby-polish
extractions on the 2026-05-08 Y&B_003 run.

Eviction is byte-bounded: oldest unaccessed entries are dropped first
(LRU). Misses fall back to ``VideoReader.get_frames_at`` so the cache
never has to be complete for the output phase to work.

This module is thread-safe — the streaming scanner's decoder thread
populates the cache while the output thread reads from it.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Optional, Tuple

import numpy as np


def _round_key(ts: float) -> float:
    """Round timestamps to milliseconds so callers using slightly different
    sub-millisecond floats hit the same cache entry."""
    return round(float(ts), 3)


class FrameCache:
    """Bounded byte-LRU mapping of timestamp -> (analysis_frame, full_frame).

    Entries are stored as a tuple so callers can populate analysis frames
    cheaply during the scan and upgrade the same entry to full-resolution
    later (e.g. when the selector promotes a candidate to a top-K cover).
    """

    def __init__(self, max_bytes: int = 3 * 1024 * 1024 * 1024):
        if max_bytes <= 0:
            raise ValueError("max_bytes must be > 0")
        self._max_bytes = int(max_bytes)
        self._cur_bytes = 0
        self._cache: "OrderedDict[float, Tuple[Optional[np.ndarray], Optional[np.ndarray]]]" = OrderedDict()
        self._lock = threading.Lock()
        # Stats are useful for the decision log: hit_rate tells us whether
        # the output phase was able to skip re-decode. Low hit rate
        # signals the cache budget is too small for the scene.
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    # ----- mutation -----

    def put(
        self,
        ts: float,
        *,
        analysis_frame: Optional[np.ndarray] = None,
        full_frame: Optional[np.ndarray] = None,
    ) -> None:
        """Insert or update an entry. ``None`` values leave the existing
        slot untouched, so callers can ``put(ts, full_frame=...)`` to
        upgrade an analysis-only entry without losing the analysis copy.
        """
        if analysis_frame is None and full_frame is None:
            return
        key = _round_key(ts)
        with self._lock:
            existing = self._cache.pop(key, None)
            if existing is not None:
                self._cur_bytes -= self._size_of(existing)
                if analysis_frame is None:
                    analysis_frame = existing[0]
                if full_frame is None:
                    full_frame = existing[1]
            entry = (analysis_frame, full_frame)
            self._cache[key] = entry
            self._cur_bytes += self._size_of(entry)
            self._evict_if_needed_locked()

    def upgrade_to_full(self, ts: float, full_frame: np.ndarray) -> None:
        """Convenience for the common upgrade path."""
        self.put(ts, full_frame=full_frame)

    def discard(self, ts: float) -> None:
        key = _round_key(ts)
        with self._lock:
            entry = self._cache.pop(key, None)
            if entry is not None:
                self._cur_bytes -= self._size_of(entry)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._cur_bytes = 0

    # ----- read -----

    def get_full(self, ts: float) -> Optional[np.ndarray]:
        return self._get(ts, want_full=True)

    def get_analysis(self, ts: float) -> Optional[np.ndarray]:
        return self._get(ts, want_full=False)

    def _get(self, ts: float, *, want_full: bool) -> Optional[np.ndarray]:
        key = _round_key(ts)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            self._cache.move_to_end(key)
            analysis, full = entry
            wanted = full if want_full else analysis
            if wanted is None:
                # Partial hit (e.g. only the analysis copy is cached but
                # caller wants full-res). Counted as a miss so hit_rate
                # honestly reflects whether the cache served the request.
                self._misses += 1
                return None
            self._hits += 1
            return wanted

    # ----- introspection -----

    def __contains__(self, ts: float) -> bool:
        return _round_key(ts) in self._cache

    def __len__(self) -> int:
        return len(self._cache)

    @property
    def current_bytes(self) -> int:
        return self._cur_bytes

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total) if total else 0.0
            return {
                "entries": len(self._cache),
                "current_bytes": self._cur_bytes,
                "max_bytes": self._max_bytes,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(hit_rate, 4),
                "evictions": self._evictions,
            }

    # ----- internals -----

    @staticmethod
    def _size_of(entry: Tuple[Optional[np.ndarray], Optional[np.ndarray]]) -> int:
        analysis, full = entry
        size = 0
        if analysis is not None:
            size += int(getattr(analysis, "nbytes", 0) or 0)
        if full is not None:
            size += int(getattr(full, "nbytes", 0) or 0)
        return size

    def _evict_if_needed_locked(self) -> None:
        while self._cur_bytes > self._max_bytes and self._cache:
            _, evicted = self._cache.popitem(last=False)
            self._cur_bytes -= self._size_of(evicted)
            self._evictions += 1
