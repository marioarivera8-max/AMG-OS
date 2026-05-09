from __future__ import annotations

import numpy as np


def test_runtime_info_shape(monkeypatch):
    import amg.video.frames as frames

    monkeypatch.setenv("AMG_GPU_CV_ENABLED", "0")
    frames._RUNTIME = None
    info = frames.runtime_info()
    assert "mode" in info
    assert "backend" in info
    assert "reason" in info
    assert info["mode"] in {"cpu", "gpu"}


def test_analysis_gray_cpu_fallback(monkeypatch):
    import amg.video.frames as frames

    monkeypatch.setenv("AMG_GPU_CV_ENABLED", "0")
    frames._RUNTIME = None
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    gray = frames.analysis_gray(frame)
    assert gray.ndim == 2
    assert gray.shape[0] == frames.ANALYSIS_FRAME_SIZE[1]
    assert gray.shape[1] == frames.ANALYSIS_FRAME_SIZE[0]
