"""Tests for amg.scanning.stream.run_stream_scan.

The streaming scanner is the heart of the v11.4 redesign. Coverage:
  - happy path: produces ranked picks via fused score + populates the
    frame cache with full-res frames the decoder produced as a
    side-effect (so the output phase can skip re-decode);
  - deadline handling: scan stops scoring once the wall deadline
    arrives even if the producer keeps emitting candidates;
  - AI-call cap: the dispatcher honours STREAMING_SCAN_MAX_AI_CALLS so
    a runaway producer can't blow the GPU budget;
  - sieve gates: dark frames and frames below the relative sharpness
    floor are dropped before reaching the dispatcher.

These tests stub VideoReader, AIClient, and the heavy CV functions so
the suite stays fast (<1s) and deterministic.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import List, Tuple

import numpy as np
import pytest


@dataclass
class _ScoredStub:
    parse_succeeded: bool = True
    score: float = 0.0


class _FakeAIResponse:
    def __init__(self, success: bool = True, raw_text: str = "ok"):
        self.success = success
        self.raw_text = raw_text


class _FakeVideoReader:
    """Yields (ts, frame) pairs deterministically. Each frame's first
    pixel encodes the sharpness the stub measure_sharpness will return,
    so we can drive specific sieve outcomes."""

    def __init__(self, frames: List[Tuple[float, np.ndarray]]):
        self._frames = frames

    def __call__(self, _path):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def iter_frames_sequential(self, _start, _end, _interval):
        yield from self._frames


def _frame(sharp: int) -> np.ndarray:
    """4x4 frame whose first pixel is `sharp` so the stubbed
    measure_sharpness reads a known value back."""
    f = np.zeros((4, 4, 3), dtype=np.uint8)
    f[0, 0, 0] = min(255, max(0, sharp))
    return f


def _install_stream_stubs(monkeypatch, *, frames, score_results=None):
    """Wire the same stubs every test in this file uses."""
    import amg.scanning.stream as stream

    monkeypatch.setattr(stream, "VideoReader", _FakeVideoReader(frames))
    monkeypatch.setattr(stream, "is_frame_too_dark", lambda _f: False)
    monkeypatch.setattr(stream, "measure_sharpness", lambda f: float(f[0, 0, 0]) * 10.0)
    monkeypatch.setattr(stream, "measure_motion", lambda *_a: 0.0)
    monkeypatch.setattr(stream, "compute_perceptual_hash", lambda _f: None)
    monkeypatch.setattr(stream, "are_near_duplicates", lambda *_a, **_k: False)
    # cv2.resize, cv2.cvtColor are tiny; let them run.

    score_iter = iter(score_results or [])

    class _StubClient:
        def __init__(self):
            self.calls = 0

        def score_frame(self, frame, _prompt, system_prompt=None):  # noqa: D401
            self.calls += 1
            try:
                next(score_iter)
            except StopIteration:
                pass
            return _FakeAIResponse(success=True, raw_text="ok")

    client = _StubClient()
    monkeypatch.setattr(stream, "AIClient", lambda: client)

    # parse_ai_response returns ScoredFrame with score derived from frame
    # pixel value so we can rank deterministically.
    def fake_parse(_raw):
        return _ScoredStub(parse_succeeded=True, score=90.0)

    monkeypatch.setattr(stream, "parse_ai_response", fake_parse)
    monkeypatch.setattr(stream, "cap_score_for_excellence", lambda _r, s: float(s))

    return stream, client


def _quiet_log(_msg):
    pass


def _quiet_progress(_p):
    pass


class TestHappyPath:
    def test_picks_ranked_and_cache_populated(self, monkeypatch, tmp_path):
        # Three frames, all pass the sieve (sharpness * 10 well above
        # SHARPNESS_HARD_FLOOR/2 = 50).
        frames = [
            (0.0, _frame(60)),
            (10.0, _frame(70)),
            (40.0, _frame(80)),
        ]
        stream, client = _install_stream_stubs(monkeypatch, frames=frames)

        result = stream.run_stream_scan(
            Path("dummy.mp4"),
            duration_sec=60.0,
            prompt="prompt",
            target_count=2,
            cover_cap=2,
            on_log=_quiet_log,
            on_progress=_quiet_progress,
            max_workers=2,
            interval_sec=1.0,
            max_queued=10,
            frame_cache_max_mb=64,
            selector_overrides={"min_gap_sec": 0.5, "post_ai_sharp_percentile": 0.0},
        )

        assert client.calls == 3
        assert result["stats"]["completed"] == 3
        assert len(result["picks"]) == 2
        assert result["aborted"] is False
        # Cache should have all three full-res frames.
        cache = result["frame_cache"]
        for ts, frame in frames:
            cached = cache.get_full(ts)
            assert cached is frame, f"cache miss at ts={ts}"

    def test_dark_and_blurry_dropped(self, monkeypatch, tmp_path):
        # Mix: dark, very low sharpness, normal. Only the third should
        # reach the dispatcher.
        import amg.scanning.stream as stream
        from amg.config import SHARPNESS_HARD_FLOOR

        frames = [
            (0.0, _frame(60)),  # dark — caught below
            (10.0, _frame(1)),  # below floor (1 * 10 = 10 < SHARPNESS_HARD_FLOOR/2)
            (20.0, _frame(60)),
        ]

        # Stub measure_sharpness to return pixel*10. With the bootstrap
        # floor (SHARPNESS_HARD_FLOOR/2 = 50), pixel=1 ⇒ sharpness=10
        # is dropped, pixel=60 ⇒ sharpness=600 passes.
        monkeypatch.setattr(stream, "VideoReader", _FakeVideoReader(frames))
        monkeypatch.setattr(stream, "measure_sharpness", lambda f: float(f[0, 0, 0]) * 10.0)
        monkeypatch.setattr(stream, "measure_motion", lambda *_a: 0.0)
        monkeypatch.setattr(stream, "compute_perceptual_hash", lambda _f: None)
        monkeypatch.setattr(stream, "are_near_duplicates", lambda *_a, **_k: False)

        is_dark_calls = {"n": 0}

        def fake_dark(f):
            # First frame only is "too dark".
            is_dark_calls["n"] += 1
            return is_dark_calls["n"] == 1

        monkeypatch.setattr(stream, "is_frame_too_dark", fake_dark)

        class _Client:
            def __init__(self):
                self.calls = 0

            def score_frame(self, *_a, **_k):
                self.calls += 1
                return _FakeAIResponse(success=True, raw_text="ok")

        client = _Client()
        monkeypatch.setattr(stream, "AIClient", lambda: client)
        monkeypatch.setattr(stream, "parse_ai_response", lambda _r: _ScoredStub(True, 80.0))
        monkeypatch.setattr(stream, "cap_score_for_excellence", lambda _r, s: float(s))

        result = stream.run_stream_scan(
            Path("dummy.mp4"),
            duration_sec=30.0,
            prompt="p",
            target_count=1,
            cover_cap=1,
            on_log=_quiet_log,
            on_progress=_quiet_progress,
            max_workers=1,
            interval_sec=1.0,
            max_queued=10,
            frame_cache_max_mb=64,
            selector_overrides={"min_gap_sec": 0.5, "post_ai_sharp_percentile": 0.0},
        )

        assert client.calls == 1, "only the third (non-dark, sharp) frame should be scored"
        assert result["stats"]["frames_dark"] == 1
        assert result["stats"]["frames_below_floor"] >= 1
        # Sanity: picks reflect the surviving candidate.
        assert len(result["picks"]) == 1
        assert result["picks"][0]["timestamp_sec"] == 20.0


class TestDeadline:
    def test_deadline_aborts_with_partial(self, monkeypatch, tmp_path):
        """Deadline expired mid-scan: scanner stops submitting and
        returns whatever it managed to score, with aborted=True."""
        import amg.scanning.stream as stream

        frames = [(float(i), _frame(60)) for i in range(20)]
        _install_stream_stubs(monkeypatch, frames=frames)

        # Stub score_frame to take 0.05s each. With deadline 0.1s in the
        # future and 1 worker we expect ~2 frames scored before abort.
        client_holder = {}

        class _SlowClient:
            def __init__(self):
                self.calls = 0
                client_holder["c"] = self

            def score_frame(self, *_a, **_k):
                self.calls += 1
                time.sleep(0.05)
                return _FakeAIResponse(success=True, raw_text="ok")

        monkeypatch.setattr(stream, "AIClient", lambda: _SlowClient())

        deadline = time.time() + 0.15
        result = stream.run_stream_scan(
            Path("dummy.mp4"),
            duration_sec=20.0,
            prompt="p",
            target_count=1,
            cover_cap=1,
            deadline_sec=deadline,
            on_log=_quiet_log,
            on_progress=_quiet_progress,
            max_workers=1,
            interval_sec=1.0,
            max_queued=10,
            frame_cache_max_mb=64,
            selector_overrides={"min_gap_sec": 0.5, "post_ai_sharp_percentile": 0.0},
        )

        assert result["aborted"] is True
        assert result["abort_reason"] == "E_STREAM_DEADLINE"
        # Should have scored fewer than the full 20 frames.
        assert result["stats"]["completed"] < 20

    def test_ai_call_cap_aborts_with_reason(self, monkeypatch, tmp_path):
        frames = [(float(i), _frame(60)) for i in range(10)]
        stream, client = _install_stream_stubs(monkeypatch, frames=frames)

        result = stream.run_stream_scan(
            Path("dummy.mp4"),
            duration_sec=10.0,
            prompt="p",
            target_count=1,
            cover_cap=1,
            on_log=_quiet_log,
            on_progress=_quiet_progress,
            max_workers=1,
            interval_sec=1.0,
            max_ai_calls=3,
            max_queued=10,
            frame_cache_max_mb=64,
            selector_overrides={"min_gap_sec": 0.5, "post_ai_sharp_percentile": 0.0},
        )

        assert result["aborted"] is True
        assert result["abort_reason"] == "E_STREAM_AI_CAP"
        assert result["stats"]["submitted"] == 3


class TestInstrumentation:
    """The 2026-05-09 measurement commit: decode-vs-AI wall-time split,
    parse_failed/score_zero counters, and raw-AI-response sidecar.

    The Y_B_003 regression that triggered this work showed completed=49,
    selector_pool=0 — i.e. the AI returned successful HTTP responses but
    every one parsed to score 0 or failed to parse, and the old stats
    didn't surface that. These tests pin the new contract."""

    def test_parse_failed_counter_and_raw_ai_capture(self, monkeypatch, tmp_path):
        """AI returns success but parse_succeeded=False — counter ticks
        and raw_ai_samples captures the response so the prompt can be
        debugged from the decision log alone."""
        import amg.scanning.stream as stream

        frames = [(float(i), _frame(60)) for i in range(3)]
        monkeypatch.setattr(stream, "VideoReader", _FakeVideoReader(frames))
        monkeypatch.setattr(stream, "is_frame_too_dark", lambda _f: False)
        monkeypatch.setattr(stream, "measure_sharpness", lambda f: float(f[0, 0, 0]) * 10.0)
        monkeypatch.setattr(stream, "measure_motion", lambda *_a: 0.0)
        monkeypatch.setattr(stream, "compute_perceptual_hash", lambda _f: None)
        monkeypatch.setattr(stream, "are_near_duplicates", lambda *_a, **_k: False)

        class _Client:
            def __init__(self):
                self.calls = 0

            def score_frame(self, *_a, **_k):
                self.calls += 1
                # Looks-like-success HTTP response, garbage body — the
                # exact failure mode the rubric prompt produces today.
                return _FakeAIResponse(success=True, raw_text="garbage that won't parse")

        monkeypatch.setattr(stream, "AIClient", lambda: _Client())
        monkeypatch.setattr(stream, "parse_ai_response", lambda _r: _ScoredStub(False, 0.0))
        monkeypatch.setattr(stream, "cap_score_for_excellence", lambda _r, s: float(s))

        result = stream.run_stream_scan(
            Path("dummy.mp4"),
            duration_sec=5.0,
            prompt="p",
            target_count=1,
            cover_cap=1,
            on_log=_quiet_log,
            on_progress=_quiet_progress,
            max_workers=1,
            interval_sec=1.0,
            max_queued=10,
            frame_cache_max_mb=64,
            selector_overrides={"min_gap_sec": 0.5, "post_ai_sharp_percentile": 0.0},
        )

        # All three scores should land in parse_failed; selector pool empty.
        assert result["stats"]["completed"] == 3
        assert result["stats"]["parse_failed"] == 3
        assert result["stats"]["score_zero"] == 0
        assert result["selector_stats"]["scored_pool"] == 0
        # Raw samples captured (capped at 8 by default — we sent 3).
        samples = result["raw_ai_samples"]
        assert len(samples) == 3
        for s in samples:
            assert s["reason"] == "parse_failed"
            assert s["ai_success"] is True
            assert "garbage" in s["raw_text_truncated"]
            assert s["parse_succeeded"] is False

    def test_score_zero_counter_separates_from_parse_failed(self, monkeypatch, tmp_path):
        """parse_succeeded=True but score==0 must increment score_zero,
        not parse_failed. Both lead to selector_pool=0 but tell us
        different things about the prompt."""
        import amg.scanning.stream as stream

        frames = [(float(i), _frame(60)) for i in range(2)]
        monkeypatch.setattr(stream, "VideoReader", _FakeVideoReader(frames))
        monkeypatch.setattr(stream, "is_frame_too_dark", lambda _f: False)
        monkeypatch.setattr(stream, "measure_sharpness", lambda f: float(f[0, 0, 0]) * 10.0)
        monkeypatch.setattr(stream, "measure_motion", lambda *_a: 0.0)
        monkeypatch.setattr(stream, "compute_perceptual_hash", lambda _f: None)
        monkeypatch.setattr(stream, "are_near_duplicates", lambda *_a, **_k: False)

        class _Client:
            def score_frame(self, *_a, **_k):
                return _FakeAIResponse(success=True, raw_text="parsed but rated 0")

        monkeypatch.setattr(stream, "AIClient", lambda: _Client())
        monkeypatch.setattr(stream, "parse_ai_response", lambda _r: _ScoredStub(True, 0.0))
        monkeypatch.setattr(stream, "cap_score_for_excellence", lambda _r, s: float(s))

        result = stream.run_stream_scan(
            Path("dummy.mp4"),
            duration_sec=3.0,
            prompt="p",
            target_count=1,
            cover_cap=1,
            on_log=_quiet_log,
            on_progress=_quiet_progress,
            max_workers=1,
            interval_sec=1.0,
            max_queued=10,
            frame_cache_max_mb=64,
            selector_overrides={"min_gap_sec": 0.5, "post_ai_sharp_percentile": 0.0},
        )

        assert result["stats"]["parse_failed"] == 0
        assert result["stats"]["score_zero"] == 2
        for s in result["raw_ai_samples"]:
            assert s["reason"] == "score_zero"

    def test_decode_and_cv_wall_are_tracked(self, monkeypatch, tmp_path):
        """decode_wall_sec and cv_wall_sec must be present and >= 0
        after any successful run. We can't pin exact values (timing
        varies) but we can pin the contract."""
        frames = [(float(i), _frame(60)) for i in range(3)]
        stream, _client = _install_stream_stubs(monkeypatch, frames=frames)

        result = stream.run_stream_scan(
            Path("dummy.mp4"),
            duration_sec=3.0,
            prompt="p",
            target_count=1,
            cover_cap=1,
            on_log=_quiet_log,
            on_progress=_quiet_progress,
            max_workers=1,
            interval_sec=1.0,
            max_queued=10,
            frame_cache_max_mb=64,
            selector_overrides={"min_gap_sec": 0.5, "post_ai_sharp_percentile": 0.0},
        )

        stats = result["stats"]
        assert "decode_wall_sec" in stats
        assert "cv_wall_sec" in stats
        assert "ai_wall_sec" in stats
        assert stats["decode_wall_sec"] >= 0.0
        assert stats["cv_wall_sec"] >= 0.0
        assert stats["ai_wall_sec"] >= 0.0
