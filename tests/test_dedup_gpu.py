from __future__ import annotations

import numpy as np


def _frame(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8)
    return arr


def test_perceptual_hash_distance_and_string():
    import amg.video.dedup as dedup

    h1 = dedup.compute_perceptual_hash(_frame(11))
    h2 = dedup.compute_perceptual_hash(_frame(11))
    h3 = dedup.compute_perceptual_hash(_frame(23))
    assert h1 is not None and h2 is not None and h3 is not None
    assert dedup.are_near_duplicates(h1, h2, threshold=0) is True
    assert isinstance(str(h1), str)
    assert len(str(h1)) > 0
    # Different frames should not always collide at strict threshold.
    assert dedup.are_near_duplicates(h1, h3, threshold=0) is False


def test_deduplicate_frames_keeps_first_and_drops_near_duplicates():
    import amg.video.dedup as dedup

    f = _frame(7)
    rows = [
        {"timestamp_sec": 0.0, "frame": f},
        {"timestamp_sec": 1.0, "frame": f.copy()},
        {"timestamp_sec": 2.0, "frame": _frame(111)},
    ]
    out = dedup.deduplicate_frames(rows, threshold=0)
    assert len(out) == 2
    assert out[0]["timestamp_sec"] == 0.0
    assert out[1]["timestamp_sec"] == 2.0
