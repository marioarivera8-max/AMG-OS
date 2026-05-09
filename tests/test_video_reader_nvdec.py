from __future__ import annotations

import io
from pathlib import Path

import numpy as np


def test_auto_backend_selects_ffmpeg_cuda_when_requested(monkeypatch, tmp_path):
    """When AMG_VIDEO_HWACCEL=cuda on Linux with a cuda ffmpeg binary
    present, auto backend should choose the ffmpeg-cuda hybrid path."""
    import amg.video.reader as reader

    ffmpeg_bin = tmp_path / "ffmpeg-cuda"
    ffmpeg_bin.write_text("stub")

    monkeypatch.setattr(reader, "_BACKEND_OVERRIDE", "auto")
    monkeypatch.setattr(reader, "_HWACCEL_MODE", "cuda")
    monkeypatch.setattr(reader, "_FFMPEG_CUDA_PATH", ffmpeg_bin)
    monkeypatch.setattr(reader, "PYAV_AVAILABLE", True)

    vr = reader.VideoReader(Path("dummy.mp4"))
    assert isinstance(vr._backend, reader._FFmpegCudaBackend)


def test_ffmpeg_cuda_iter_frames_uses_pipe_and_yields_frames(monkeypatch, tmp_path):
    """Exercise the CUDA sequential path with mocked PyAV metadata and
    mocked subprocess output."""
    import amg.video.reader as reader

    ffmpeg_bin = tmp_path / "ffmpeg-cuda"
    ffmpeg_bin.write_text("stub")

    class _DummyPyAVBackend:
        def __init__(self, _video_path):
            self._size = (2, 2)
            self.fps = 30.0
            self.frame_count = 60
            self.duration_sec = 2.0
            self.fallback_calls = 0

        def open(self):
            return None

        def close(self):
            return None

        @property
        def frame_size(self):
            return self._size

        def get_frame_at(self, _timestamp_sec):
            return None

        def get_frames_at(self, _timestamps_sec):
            return []

        def iter_frames_sequential(self, _start_sec, _end_sec, _interval_sec):
            self.fallback_calls += 1
            yield (0.0, np.zeros((2, 2, 3), dtype=np.uint8))

    captured_cmd = {}
    frame_bytes = bytes(range(12))  # 2*2*3

    class _FakeProc:
        def __init__(self, cmd, **_kwargs):
            captured_cmd["cmd"] = cmd
            self.stdout = io.BytesIO(frame_bytes)
            self.stderr = io.BytesIO(b"")
            self._rc = 0

        def wait(self, timeout=None):
            return self._rc

        def poll(self):
            return self._rc

        def kill(self):
            self._rc = -9

    monkeypatch.setattr(reader, "_PyAVBackend", _DummyPyAVBackend)
    monkeypatch.setattr(reader, "_HWACCEL_MODE", "cuda")
    monkeypatch.setattr(reader, "_FFMPEG_CUDA_PATH", ffmpeg_bin)
    monkeypatch.setattr(reader.platform, "system", lambda: "Linux")
    monkeypatch.setattr(reader.subprocess, "Popen", _FakeProc)

    backend = reader._FFmpegCudaBackend(Path("dummy.mp4"))
    rows = list(backend.iter_frames_sequential(0.0, 1.0, 1.0))

    assert len(rows) == 1
    ts, frame = rows[0]
    assert ts == 0.0
    assert frame.shape == (2, 2, 3)
    assert frame.dtype == np.uint8
    assert "-hwaccel" in captured_cmd["cmd"]
    assert "cuda" in captured_cmd["cmd"]
