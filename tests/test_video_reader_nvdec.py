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


def test_ffmpeg_cuda_scaled_iter_pipes_smaller_frames(monkeypatch, tmp_path):
    """Low-res analysis mode should ask ffmpeg for scaled rawvideo so the
    streaming scanner does not pipe full-resolution BGR frames through
    Python for every sampled timestamp."""
    import amg.video.reader as reader

    ffmpeg_bin = tmp_path / "ffmpeg-cuda"
    ffmpeg_bin.write_text("stub")

    class _DummyPyAVBackend:
        def __init__(self, _video_path):
            self._size = (1920, 1080)
            self.fps = 30.0
            self.frame_count = 60
            self.duration_sec = 2.0

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
            yield (0.0, np.zeros((1080, 1920, 3), dtype=np.uint8))

        def iter_frames_sequential_scaled(self, _start_sec, _end_sec, _interval_sec, _max_size):
            yield (0.0, np.zeros((378, 672, 3), dtype=np.uint8))

    captured_cmd = {}
    frame_bytes = bytes([7]) * (672 * 378 * 3)

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
    rows = list(backend.iter_frames_sequential_scaled(0.0, 1.0, 1.0, (672, 672)))

    assert len(rows) == 1
    _ts, frame = rows[0]
    assert frame.shape == (378, 672, 3)
    vf_idx = captured_cmd["cmd"].index("-vf") + 1
    assert "scale=672:378" in captured_cmd["cmd"][vf_idx]


def test_ffmpeg_cuda_get_frames_at_uses_pipe(monkeypatch, tmp_path):
    """Random-access frame extraction should use ffmpeg-cuda too.

    This covers calibration/fallback/output, not just the sequential scan path.
    """
    import amg.video.reader as reader

    ffmpeg_bin = tmp_path / "ffmpeg-cuda"
    ffmpeg_bin.write_text("stub")

    class _DummyPyAVBackend:
        def __init__(self, _video_path):
            self._size = (2, 2)
            self.duration_sec = 10.0
            self.fallback_calls = 0

        def open(self):
            return None

        def close(self):
            return None

        @property
        def frame_size(self):
            return self._size

        def get_frame_at(self, _timestamp_sec):
            self.fallback_calls += 1
            return np.zeros((2, 2, 3), dtype=np.uint8)

        def get_frames_at(self, timestamps_sec):
            self.fallback_calls += len(timestamps_sec)
            return [np.zeros((2, 2, 3), dtype=np.uint8) for _ in timestamps_sec]

    captured_cmds = []
    frame_bytes = bytes(range(12))  # 2*2*3

    class _FakeProc:
        returncode = 0

        def __init__(self, cmd, **_kwargs):
            captured_cmds.append(cmd)

        def communicate(self, timeout=None):
            return frame_bytes, b""

    monkeypatch.setattr(reader, "_PyAVBackend", _DummyPyAVBackend)
    monkeypatch.setattr(reader, "_HWACCEL_MODE", "cuda")
    monkeypatch.setattr(reader, "_FFMPEG_CUDA_PATH", ffmpeg_bin)
    monkeypatch.setattr(reader.platform, "system", lambda: "Linux")
    monkeypatch.setattr(reader.subprocess, "Popen", _FakeProc)

    backend = reader._FFmpegCudaBackend(Path("dummy.mp4"))
    rows = backend.get_frames_at([1.0, 2.5])

    assert len(rows) == 2
    assert all(frame is not None and frame.shape == (2, 2, 3) for frame in rows)
    assert len(captured_cmds) == 2
    assert all("-hwaccel" in cmd and "cuda" in cmd for cmd in captured_cmds)
    assert all("-frames:v" in cmd and "1" in cmd for cmd in captured_cmds)
    assert backend._delegate.fallback_calls == 0


def test_ffmpeg_cuda_get_frames_at_batches_clustered_triplets(monkeypatch, tmp_path):
    import amg.video.reader as reader

    ffmpeg_bin = tmp_path / "ffmpeg-cuda"
    ffmpeg_bin.write_text("stub")

    class _DummyPyAVBackend:
        def __init__(self, _video_path):
            self._size = (2, 2)
            self.fps = 4.0
            self.duration_sec = 10.0
            self.fallback_calls = 0

        def open(self):
            return None

        def close(self):
            return None

        @property
        def frame_size(self):
            return self._size

        def get_frame_at(self, _timestamp_sec):
            self.fallback_calls += 1
            return np.zeros((2, 2, 3), dtype=np.uint8)

        def get_frames_at(self, timestamps_sec):
            self.fallback_calls += len(timestamps_sec)
            return [np.zeros((2, 2, 3), dtype=np.uint8) for _ in timestamps_sec]

    captured_cmds = []
    frame_bytes = bytes([7]) * 12

    class _FakeProc:
        def __init__(self, cmd, **_kwargs):
            captured_cmds.append(cmd)
            self.stdout = io.BytesIO(frame_bytes * 7)
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
    rows = backend.get_frames_at([1.0, 1.5, 2.0])

    assert len(rows) == 3
    assert all(frame is not None and frame.shape == (2, 2, 3) for frame in rows)
    assert len(captured_cmds) == 1
    assert "-vf" in captured_cmds[0]
    assert "-frames:v" not in captured_cmds[0]
    assert backend._delegate.fallback_calls == 0


def test_ffmpeg_cuda_batch_partial_failure_falls_back(monkeypatch, tmp_path):
    import amg.video.reader as reader

    ffmpeg_bin = tmp_path / "ffmpeg-cuda"
    ffmpeg_bin.write_text("stub")

    class _DummyPyAVBackend:
        def __init__(self, _video_path):
            self._size = (2, 2)
            self.fps = 4.0
            self.duration_sec = 10.0
            self.fallback_calls = 0

        def open(self):
            return None

        def close(self):
            return None

        @property
        def frame_size(self):
            return self._size

        def get_frame_at(self, _timestamp_sec):
            self.fallback_calls += 1
            return np.full((2, 2, 3), 9, dtype=np.uint8)

        def get_frames_at(self, timestamps_sec):
            self.fallback_calls += len(timestamps_sec)
            return [np.full((2, 2, 3), 9, dtype=np.uint8) for _ in timestamps_sec]

    class _FakeProc:
        def __init__(self, _cmd, **_kwargs):
            self.stdout = io.BytesIO(bytes([7]) * 12)
            self.stderr = io.BytesIO(b"hardware decode failed")
            self._rc = 1

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
    rows = backend.get_frames_at([1.0, 1.5, 2.0])

    assert [int(frame[0, 0, 0]) for frame in rows if frame is not None] == [9, 9, 9]
    assert backend._delegate.fallback_calls == 3


def test_ffmpeg_cuda_get_frames_at_falls_back_per_failed_frame(monkeypatch, tmp_path):
    import amg.video.reader as reader

    ffmpeg_bin = tmp_path / "ffmpeg-cuda"
    ffmpeg_bin.write_text("stub")

    class _DummyPyAVBackend:
        def __init__(self, _video_path):
            self._size = (2, 2)
            self.duration_sec = 10.0
            self.fallback_calls = 0

        def open(self):
            return None

        def close(self):
            return None

        @property
        def frame_size(self):
            return self._size

        def get_frame_at(self, _timestamp_sec):
            self.fallback_calls += 1
            return np.full((2, 2, 3), 9, dtype=np.uint8)

    class _FakeProc:
        returncode = 1

        def __init__(self, _cmd, **_kwargs):
            pass

        def communicate(self, timeout=None):
            return b"", b"decode failed"

    monkeypatch.setattr(reader, "_PyAVBackend", _DummyPyAVBackend)
    monkeypatch.setattr(reader, "_HWACCEL_MODE", "cuda")
    monkeypatch.setattr(reader, "_FFMPEG_CUDA_PATH", ffmpeg_bin)
    monkeypatch.setattr(reader.platform, "system", lambda: "Linux")
    monkeypatch.setattr(reader.subprocess, "Popen", _FakeProc)

    backend = reader._FFmpegCudaBackend(Path("dummy.mp4"))
    rows = backend.get_frames_at([1.0])

    assert rows[0] is not None
    assert int(rows[0][0, 0, 0]) == 9
    assert backend._delegate.fallback_calls == 1
