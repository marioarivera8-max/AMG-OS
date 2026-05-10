"""
Video reader — v11.1.3.

Backend selection (in priority order):
  1. ffmpeg-cuda + PyAV hybrid          (Linux pod, when AMG_VIDEO_HWACCEL=cuda)
  2. PyAV with videotoolbox option set  (Apple Silicon — see HWACCEL CAVEAT)
  3. PyAV with plain software decode    (cross-platform, fast)
  4. OpenCV                             (fallback, always available)

PyAV is a Python wrapper around ffmpeg's libav* libraries. For the hot
path — sequential iteration during Tier 1/2/3 scans on long 4K HEVC
videos — PyAV is meaningfully faster than OpenCV (~1.86x measured on
scene 8 at 1080p) because it can decode the bitstream linearly and emit
frames at intervals, instead of seeking-then-decoding for every frame
as OpenCV does.

HWACCEL CAVEAT — what we know vs what we don't:
PyAV 13.1.0 silently accepts options={"hwaccel": "videotoolbox"} when
opening a container, but provides no API to confirm whether VideoToolbox
actually engages. Empirically the decoded frames come back as yuv420p
(a software pixel format), suggesting the option may be a no-op in
PyAV 13.1. The measured PyAV-vs-OpenCV speedup is real but is likely
attributable to the linear decode pattern rather than HW acceleration.
Treat any "videotoolbox" log line / backend_name as "we set the option,
ffmpeg accepted it, status of actual HW engagement: unknown."

This module is cherry-picked from v11.2 in isolation per Mario's
direction. The v11.2 bundle was rolled back due to OTHER changes
(wider tier_2 sweep flooding the AI with bad candidates, too-aggressive
save-time dedup); the decode work itself ✅ worked. The handoff
explicitly endorses re-using it, and that's exactly what this is. The
frames_extracted counter hooks from v11.2 are NOT included here —
those belong to a separate "counter aggregation" change Mario hasn't
asked for.

Public API is unchanged from v11.1 — drop-in replacement.

Backend can be forced via env var
AMG_VIDEO_BACKEND={ffmpeg_cuda,pyav,opencv,auto}.
"""
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import cv2
import numpy as np

from amg.utils.logging import get_logger

log = get_logger("video.reader")


# Try PyAV — log loudly if unavailable so install issues are obvious.
try:
    import av  # type: ignore
    PYAV_AVAILABLE = True
except ImportError:  # pragma: no cover
    av = None
    PYAV_AVAILABLE = False
    log.warn("PyAV not installed — falling back to OpenCV. Install with: pip install av")


# Backend selection knob. Default 'auto' tries PyAV first.
# Override with AMG_VIDEO_BACKEND env var: 'pyav', 'opencv', or 'auto'.
_BACKEND_OVERRIDE = os.environ.get("AMG_VIDEO_BACKEND", "auto").lower()
_HWACCEL_MODE = os.environ.get("AMG_VIDEO_HWACCEL", "auto").lower()
_FFMPEG_CUDA_PATH = Path(
    os.environ.get("AMG_FFMPEG_CUDA_BIN", "/usr/local/bin/ffmpeg-cuda")
)


def _fit_within(width: int, height: int, max_size: Tuple[int, int]) -> Tuple[int, int]:
    """Return even dimensions fitting inside max_size without upscaling."""
    max_w, max_h = int(max_size[0] or 0), int(max_size[1] or 0)
    if width <= 0 or height <= 0 or max_w <= 0 or max_h <= 0:
        return width, height
    scale = min(max_w / float(width), max_h / float(height), 1.0)
    out_w = max(2, int(round(width * scale)))
    out_h = max(2, int(round(height * scale)))
    # yuv/rawvideo filters are happier with even sizes.
    if out_w % 2:
        out_w -= 1
    if out_h % 2:
        out_h -= 1
    return max(2, out_w), max(2, out_h)


def _resize_to_fit(frame: np.ndarray, max_size: Tuple[int, int]) -> np.ndarray:
    h, w = frame.shape[:2]
    out_w, out_h = _fit_within(w, h, max_size)
    if out_w == w and out_h == h:
        return frame
    return cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_AREA)


def _is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def _supports_videotoolbox() -> bool:
    """Cheap check: are we on Apple Silicon with PyAV present?"""
    return PYAV_AVAILABLE and _is_apple_silicon()


# --- Backend implementations ------------------------------------------------


class _PyAVBackend:
    """
    PyAV backend. Tries VideoToolbox hwaccel first on Apple Silicon, falls
    back to software decode if hwaccel init fails.

    Holds two containers when needed:
      - a streaming container for sequential iteration (forward-only)
      - a separately-opened container per random-access call (avoids seek
        contention with the streaming container)
    """

    def __init__(self, video_path: Path):
        self.video_path = Path(video_path)
        self._stream_container = None
        self._stream_video = None
        self._fps: Optional[float] = None
        self._frame_count: Optional[int] = None
        self._duration: Optional[float] = None
        self._width: Optional[int] = None
        self._height: Optional[int] = None
        self._hwaccel_used = False
        self._codec_name: Optional[str] = None

    def _try_open(self, hwaccel: Optional[str] = None):
        """
        Open a container. If hwaccel is set, try to attach hardware decoder.
        Returns the container or raises.
        """
        # PyAV's hwaccel API: pass options on container open or use hwaccel
        # parameter when iterating. The least-fragile approach across PyAV
        # versions is to open normally, then mark the video stream's
        # codec_context.options to use the hwaccel when present.
        if hwaccel:
            container = av.open(str(self.video_path), options={"hwaccel": hwaccel})
        else:
            container = av.open(str(self.video_path))
        return container

    def open(self) -> None:
        """Open the streaming container with the best available backend."""
        if self._stream_container is not None:
            return

        last_err = None
        # 1) Try passing the VideoToolbox hwaccel option (Apple Silicon).
        #
        # IMPORTANT — what _hwaccel_used actually means here:
        # PyAV 13.1.0 silently accepts options={"hwaccel": "videotoolbox"}
        # without raising AND without engaging hardware decode — decoded
        # frames come back as yuv420p (a software pixel format), and PyAV 13
        # exposes no API to confirm whether HW decode actually engaged.
        # So this flag really means "the option string was accepted by
        # ffmpeg without error" — NOT "HW decode is active." Real-world
        # PyAV-vs-OpenCV speedup (~1.86x measured on scene 8) appears to
        # come from PyAV's linear stream decode beating OpenCV's
        # seek-and-decode-per-frame pattern, with HW status uncertain.
        if _supports_videotoolbox():
            try:
                container = self._try_open(hwaccel="videotoolbox")
                self._init_from_container(container, hwaccel="videotoolbox")
                self._hwaccel_used = True
                log.info(
                    "PyAV opened (videotoolbox option set; HW engagement not "
                    "verifiable in PyAV 13.1 — speedup may be from linear "
                    "decode pattern alone, not actual hwaccel)",
                    codec=self._codec_name,
                    duration_sec=self._duration,
                )
                return
            except Exception as e:
                last_err = e
                log.warn(
                    "PyAV rejected videotoolbox option, falling back to plain software decode",
                    error=str(e),
                )

        # 2) Try PyAV software decode
        try:
            container = self._try_open(hwaccel=None)
            self._init_from_container(container, hwaccel=None)
            log.info(
                "PyAV opened with software decode",
                codec=self._codec_name,
                duration_sec=self._duration,
            )
            return
        except Exception as e:
            last_err = e

        raise IOError(
            f"PyAV cannot open {self.video_path}: {last_err}"
        )

    def _init_from_container(self, container, hwaccel: Optional[str]) -> None:
        """Cache stream metadata from an opened container."""
        if not container.streams.video:
            container.close()
            raise IOError("No video stream found")
        video_stream = container.streams.video[0]
        # PyAV exposes average rate as a Fraction
        if video_stream.average_rate is not None:
            self._fps = float(video_stream.average_rate)
        elif video_stream.base_rate is not None:
            self._fps = float(video_stream.base_rate)
        else:
            self._fps = 0.0

        self._codec_name = (video_stream.codec_context.name or "unknown") if video_stream.codec_context else "unknown"

        # Duration and frame count
        if container.duration is not None:
            # AV_TIME_BASE = 1_000_000
            self._duration = container.duration / 1_000_000.0
        elif video_stream.duration is not None and video_stream.time_base is not None:
            self._duration = float(video_stream.duration * video_stream.time_base)
        else:
            self._duration = 0.0

        if video_stream.frames and video_stream.frames > 0:
            self._frame_count = int(video_stream.frames)
        elif self._fps and self._duration:
            self._frame_count = int(self._fps * self._duration)
        else:
            self._frame_count = 0
        self._width = int(video_stream.width or 0)
        self._height = int(video_stream.height or 0)

        self._stream_container = container
        self._stream_video = video_stream

    def close(self) -> None:
        if self._stream_container is not None:
            try:
                self._stream_container.close()
            except Exception:
                pass
            self._stream_container = None
            self._stream_video = None

    @property
    def fps(self) -> float:
        if self._fps is None:
            self.open()
        return self._fps or 0.0

    @property
    def frame_count(self) -> int:
        if self._frame_count is None:
            self.open()
        return self._frame_count or 0

    @property
    def duration_sec(self) -> float:
        if self._duration is None:
            self.open()
        return self._duration or 0.0

    @property
    def hwaccel_used(self) -> bool:
        return self._hwaccel_used

    @property
    def frame_size(self) -> Tuple[int, int]:
        if self._width is None or self._height is None:
            self.open()
        return int(self._width or 0), int(self._height or 0)

    def get_frame_at(self, timestamp_sec: float) -> Optional[np.ndarray]:
        """
        Random access: open a fresh container, seek, decode one frame,
        close. Opening per call is cheap with file caches and avoids
        polluting the streaming container's seek state.
        """
        if timestamp_sec < 0 or (self.duration_sec and timestamp_sec > self.duration_sec):
            return None
        try:
            container = av.open(str(self.video_path))
            try:
                video_stream = container.streams.video[0]
                # Seek to nearest keyframe at or before our target
                target_pts = int(timestamp_sec / video_stream.time_base) if video_stream.time_base else 0
                container.seek(
                    target_pts,
                    backward=True,
                    any_frame=False,
                    stream=video_stream,
                )
                # Decode forward until we reach (or pass) our target time
                target_t = timestamp_sec
                last_frame = None
                for frame in container.decode(video=0):
                    frame_t = float(frame.pts * video_stream.time_base) if frame.pts is not None and video_stream.time_base else 0
                    last_frame = frame
                    if frame_t >= target_t:
                        break
                if last_frame is None:
                    return None
                # PyAV frame -> RGB ndarray -> BGR ndarray for OpenCV consistency
                rgb = last_frame.to_ndarray(format="rgb24")
                return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            finally:
                container.close()
        except Exception as e:
            log.warn("PyAV get_frame_at failed", t=timestamp_sec, error=str(e))
            return None

    def get_frames_at(self, timestamps_sec: List[float]) -> List[Optional[np.ndarray]]:
        """
        Batch random access. Sort timestamps, seek to first, decode forward
        emitting frames at each requested timestamp in order.
        """
        if not timestamps_sec:
            return []
        # Pair each requested timestamp with its original index so we can
        # restore order at the end
        indexed = sorted(enumerate(timestamps_sec), key=lambda x: x[1])
        results: List[Optional[np.ndarray]] = [None] * len(timestamps_sec)

        try:
            container = av.open(str(self.video_path))
            try:
                video_stream = container.streams.video[0]
                tb = video_stream.time_base
                # Seek to first requested timestamp (backward to keyframe)
                first_t = indexed[0][1]
                target_pts = int(first_t / tb) if tb else 0
                container.seek(target_pts, backward=True, any_frame=False, stream=video_stream)

                idx_into_indexed = 0
                next_orig_idx, next_t = indexed[idx_into_indexed]

                for frame in container.decode(video=0):
                    frame_t = float(frame.pts * tb) if frame.pts is not None and tb else 0.0
                    while idx_into_indexed < len(indexed) and frame_t >= next_t:
                        rgb = frame.to_ndarray(format="rgb24")
                        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                        results[next_orig_idx] = bgr
                        idx_into_indexed += 1
                        if idx_into_indexed >= len(indexed):
                            break
                        next_orig_idx, next_t = indexed[idx_into_indexed]
                    if idx_into_indexed >= len(indexed):
                        break
            finally:
                container.close()
        except Exception as e:
            log.warn("PyAV get_frames_at failed", error=str(e))
            # One-by-one fallback
            return [self.get_frame_at(t) for t in timestamps_sec]

        return results

    def iter_frames_sequential(
        self,
        start_sec: float,
        end_sec: float,
        interval_sec: float,
    ) -> Iterator[Tuple[float, np.ndarray]]:
        """
        THE HOT PATH. Linear stream decode, emit frames at intervals.

        Replaces v10.x/v11.1 OpenCV approach that did set(POS_MSEC) +
        read() per requested timestamp — which forces a seek-and-decode
        cycle every time and is the 4K HEVC bottleneck.

        With PyAV we open the stream once, seek to start_sec (to nearest
        keyframe), then iterate decoded frames in order. We yield only
        when the next requested timestamp is reached.
        """
        if interval_sec <= 0:
            raise ValueError("interval_sec must be > 0")

        try:
            container = av.open(str(self.video_path))
            try:
                video_stream = container.streams.video[0]
                tb = video_stream.time_base
                # Seek to start (backward to keyframe so we don't miss frames)
                start_pts = int(max(0.0, start_sec) / tb) if tb else 0
                container.seek(start_pts, backward=True, any_frame=False, stream=video_stream)

                next_target = start_sec
                for frame in container.decode(video=0):
                    if frame.pts is None or tb is None:
                        continue
                    frame_t = float(frame.pts * tb)
                    if frame_t > end_sec:
                        break
                    if frame_t >= next_target:
                        rgb = frame.to_ndarray(format="rgb24")
                        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                        yield (frame_t, bgr)
                        next_target = frame_t + interval_sec
            finally:
                container.close()
        except Exception as e:
            log.warn(
                "PyAV iter_frames_sequential failed, no frames emitted",
                start=start_sec, end=end_sec, error=str(e),
            )
            return

    def iter_frames_sequential_scaled(
        self,
        start_sec: float,
        end_sec: float,
        interval_sec: float,
        max_size: Tuple[int, int],
    ) -> Iterator[Tuple[float, np.ndarray]]:
        """Sequential frames resized for analysis/AI use.

        PyAV still decodes the original frame, but callers get the smaller
        array and can avoid holding full-resolution frames in memory.
        """
        for ts, frame in self.iter_frames_sequential(start_sec, end_sec, interval_sec):
            yield ts, _resize_to_fit(frame, max_size)


class _OpenCVBackend:
    """OpenCV fallback. Same behavior as v11.1's reader."""

    def __init__(self, video_path: Path):
        self.video_path = Path(video_path)
        self._cap: Optional[cv2.VideoCapture] = None
        self._fps: Optional[float] = None
        self._frame_count: Optional[int] = None
        self._duration: Optional[float] = None
        self._width: Optional[int] = None
        self._height: Optional[int] = None
        self._hwaccel_used = False
        self._codec_name = "unknown"

    def open(self) -> None:
        if self._cap is not None:
            return
        cap = cv2.VideoCapture(str(self.video_path))
        if not cap.isOpened():
            raise IOError(f"Cannot open video: {self.video_path}")
        self._cap = cap
        self._fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        self._frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self._width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        self._height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if self._fps > 0:
            self._duration = self._frame_count / self._fps
        else:
            self._duration = 0.0
        log.info(
            "OpenCV opened (no hardware acceleration)",
            duration_sec=self._duration,
            fps=self._fps,
        )

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def fps(self) -> float:
        if self._fps is None:
            self.open()
        return self._fps or 0.0

    @property
    def frame_count(self) -> int:
        if self._frame_count is None:
            self.open()
        return self._frame_count or 0

    @property
    def duration_sec(self) -> float:
        if self._duration is None:
            self.open()
        return self._duration or 0.0

    @property
    def hwaccel_used(self) -> bool:
        return False

    @property
    def frame_size(self) -> Tuple[int, int]:
        if self._width is None or self._height is None:
            self.open()
        return int(self._width or 0), int(self._height or 0)

    def get_frame_at(self, timestamp_sec: float) -> Optional[np.ndarray]:
        if timestamp_sec < 0 or timestamp_sec > self.duration_sec:
            return None
        if self._cap is None:
            self.open()
        self._cap.set(cv2.CAP_PROP_POS_MSEC, timestamp_sec * 1000)
        ret, frame = self._cap.read()
        if ret and frame is not None:
            return frame
        return None

    def get_frames_at(self, timestamps_sec: List[float]) -> List[Optional[np.ndarray]]:
        return [self.get_frame_at(t) for t in timestamps_sec]

    def iter_frames_sequential(
        self,
        start_sec: float,
        end_sec: float,
        interval_sec: float,
    ) -> Iterator[Tuple[float, np.ndarray]]:
        if self._cap is None:
            self.open()
        timestamp = start_sec
        while timestamp <= end_sec:
            self._cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ret, frame = self._cap.read()
            if ret and frame is not None:
                yield (timestamp, frame)
            timestamp += interval_sec

    def iter_frames_sequential_scaled(
        self,
        start_sec: float,
        end_sec: float,
        interval_sec: float,
        max_size: Tuple[int, int],
    ) -> Iterator[Tuple[float, np.ndarray]]:
        for ts, frame in self.iter_frames_sequential(start_sec, end_sec, interval_sec):
            yield ts, _resize_to_fit(frame, max_size)


class _FFmpegCudaBackend:
    """
    Hybrid backend:
      - metadata + random access via PyAV (existing stable path)
      - sequential iteration via ffmpeg-cuda subprocess (NVDEC hot path)

    This keeps API compatibility while moving the dominant decode loop
    off CPU when AMG_VIDEO_HWACCEL=cuda on pod images that ship
    /usr/local/bin/ffmpeg-cuda.
    """

    def __init__(self, video_path: Path):
        self.video_path = Path(video_path)
        self._delegate = _PyAVBackend(video_path)
        self._ffmpeg_path = _FFMPEG_CUDA_PATH

    def open(self) -> None:
        self._delegate.open()

    def close(self) -> None:
        self._delegate.close()

    @property
    def fps(self) -> float:
        return self._delegate.fps

    @property
    def frame_count(self) -> int:
        return self._delegate.frame_count

    @property
    def duration_sec(self) -> float:
        return self._delegate.duration_sec

    @property
    def hwaccel_used(self) -> bool:
        return True

    @property
    def frame_size(self) -> Tuple[int, int]:
        return self._delegate.frame_size

    def get_frame_at(self, timestamp_sec: float) -> Optional[np.ndarray]:
        rows = self.get_frames_at([timestamp_sec])
        return rows[0] if rows else None

    def get_frames_at(self, timestamps_sec: List[float]) -> List[Optional[np.ndarray]]:
        if not timestamps_sec:
            return []
        if not self._should_use_cuda_pipe():
            return self._delegate.get_frames_at(timestamps_sec)

        self._delegate.open()
        width, height = self._delegate.frame_size
        if width <= 0 or height <= 0:
            log.warn(
                "ffmpeg-cuda random access missing frame dimensions; falling back to PyAV",
                width=width,
                height=height,
            )
            return self._delegate.get_frames_at(timestamps_sec)

        results: List[Optional[np.ndarray]] = [None] * len(timestamps_sec)
        failures = 0
        batched_groups = 0
        indexed = [(idx, float(ts)) for idx, ts in enumerate(timestamps_sec)]
        for group in self._group_random_access_timestamps(indexed):
            if len(group) < 3:
                for idx, ts in group:
                    ok, frame = self._read_frame_at_pipe(ts, width=width, height=height)
                    if not ok:
                        failures += 1
                        frame = self._delegate.get_frame_at(ts)
                    results[idx] = frame
                continue

            ok, frames_by_idx = self._read_frames_window_pipe(group, width=width, height=height)
            if ok:
                batched_groups += 1
                for idx, frame in frames_by_idx.items():
                    results[idx] = frame

            missing = [(idx, ts) for idx, ts in group if results[idx] is None]
            for idx, ts in missing:
                ok_one, frame = self._read_frame_at_pipe(ts, width=width, height=height)
                if not ok_one:
                    failures += 1
                    frame = self._delegate.get_frame_at(ts)
                results[idx] = frame

        if failures:
            log.warn(
                "ffmpeg-cuda random access had fallbacks",
                failures=failures,
                requested=len(timestamps_sec),
            )
        if batched_groups:
            log.info(
                "ffmpeg-cuda batched clustered random access",
                groups=batched_groups,
                requested=len(timestamps_sec),
            )
        return results

    @staticmethod
    def _group_random_access_timestamps(
        indexed_timestamps: List[Tuple[int, float]],
        *,
        max_gap_sec: float = 2.25,
        max_span_sec: float = 3.0,
    ) -> List[List[Tuple[int, float]]]:
        if not indexed_timestamps:
            return []
        ordered = sorted(indexed_timestamps, key=lambda item: item[1])
        groups: List[List[Tuple[int, float]]] = [[ordered[0]]]
        for item in ordered[1:]:
            gap = float(item[1]) - float(groups[-1][-1][1])
            span = float(item[1]) - float(groups[-1][0][1])
            if gap <= max_gap_sec and span <= max_span_sec:
                groups[-1].append(item)
            else:
                groups.append([item])
        return groups

    def _read_frames_window_pipe(
        self,
        indexed_timestamps: List[Tuple[int, float]],
        *,
        width: int,
        height: int,
    ) -> Tuple[bool, dict]:
        duration_sec = self._duration_sec_safe()
        valid = [
            (idx, ts)
            for idx, ts in indexed_timestamps
            if ts >= 0 and (not duration_sec or ts <= duration_sec)
        ]
        if not valid:
            return True, {}

        fps = self._fps_safe(default=30.0)
        output_fps = min(12.0, max(1.0, fps))
        first_ts = min(ts for _, ts in valid)
        last_ts = max(ts for _, ts in valid)
        pad = max(0.25, 1.0 / output_fps)
        start_sec = max(0.0, first_ts - pad)
        end_sec = last_ts + pad
        if duration_sec:
            end_sec = min(duration_sec, end_sec)
        span = max(0.1, end_sec - start_sec)

        frame_bytes = int(width) * int(height) * 3
        if frame_bytes <= 0:
            return False, {}

        cmd = [
            str(self._ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-hwaccel",
            "cuda",
            "-ss",
            f"{start_sec:.6f}",
            "-t",
            f"{span:.6f}",
            "-i",
            str(self.video_path),
            "-vf",
            f"fps={output_fps:.6f}",
            "-an",
            "-sn",
            "-dn",
            "-pix_fmt",
            "bgr24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]

        best: dict[int, Tuple[float, np.ndarray]] = {}
        proc = None
        emitted = 0
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=frame_bytes * 2,
            )
            if proc.stdout is None:
                raise IOError("ffmpeg-cuda started without stdout pipe")

            while True:
                buf = proc.stdout.read(frame_bytes)
                if not buf or len(buf) < frame_bytes:
                    break
                frame_ts = start_sec + (float(emitted) / output_fps)
                frame = np.frombuffer(buf, dtype=np.uint8).reshape((height, width, 3))
                for idx, target_ts in valid:
                    distance = abs(frame_ts - target_ts)
                    prev = best.get(idx)
                    if prev is None or distance < prev[0]:
                        best[idx] = (distance, frame)
                emitted += 1

            rc = proc.wait(timeout=10.0)
            if rc != 0:
                err = b""
                if proc.stderr is not None:
                    err = proc.stderr.read()
                raise IOError(
                    f"ffmpeg-cuda batch decode failed with exit={rc}: "
                    f"{err.decode('utf-8', 'ignore')[:200]}"
                )
        except Exception as e:
            log.warn(
                "ffmpeg-cuda clustered random access failed",
                count=len(valid),
                error=str(e),
            )
            return False, {}
        finally:
            if proc is not None:
                try:
                    if proc.poll() is None:
                        proc.kill()
                except Exception:
                    pass

        # Do not let one emitted frame stand in for an entire target cluster.
        # A partial hardware-decode failure can still produce stdout before
        # exiting; each accepted frame must be close to its requested timestamp.
        max_distance = max(0.2, 0.75 / output_fps)
        accepted = {
            idx: frame
            for idx, (distance, frame) in best.items()
            if float(distance) <= max_distance
        }
        if len(accepted) < len(valid):
            log.warn(
                "ffmpeg-cuda clustered random access missing precise frames",
                requested=len(valid),
                accepted=len(accepted),
                max_distance_sec=round(max_distance, 3),
            )

        return True, accepted

    def _read_frame_at_pipe(
        self,
        timestamp_sec: float,
        *,
        width: int,
        height: int,
    ) -> Tuple[bool, Optional[np.ndarray]]:
        duration_sec = self._duration_sec_safe()
        if timestamp_sec < 0 or (duration_sec and timestamp_sec > duration_sec):
            return True, None

        frame_bytes = int(width) * int(height) * 3
        if frame_bytes <= 0:
            return False, None

        cmd = [
            str(self._ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-hwaccel",
            "cuda",
            "-ss",
            f"{float(timestamp_sec):.6f}",
            "-i",
            str(self.video_path),
            "-frames:v",
            "1",
            "-an",
            "-sn",
            "-dn",
            "-pix_fmt",
            "bgr24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]

        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            out, err = proc.communicate(timeout=30.0)
            if proc.returncode != 0 or len(out) < frame_bytes:
                msg = err.decode("utf-8", "ignore")[:200] if err else ""
                log.warn(
                    "ffmpeg-cuda random frame decode failed; using PyAV fallback",
                    t=round(float(timestamp_sec), 3),
                    exit=proc.returncode,
                    bytes=len(out),
                    error=msg,
                )
                return False, None
            frame = np.frombuffer(out[:frame_bytes], dtype=np.uint8).reshape((height, width, 3))
            return True, frame
        except subprocess.TimeoutExpired:
            if proc is not None:
                try:
                    proc.kill()
                    proc.communicate(timeout=5.0)
                except Exception:
                    pass
            log.warn(
                "ffmpeg-cuda random frame decode timed out; using PyAV fallback",
                t=round(float(timestamp_sec), 3),
            )
            return False, None
        except Exception as e:
            log.warn(
                "ffmpeg-cuda random frame decode errored; using PyAV fallback",
                t=round(float(timestamp_sec), 3),
                error=str(e),
            )
            return False, None

    def _fps_safe(self, *, default: float) -> float:
        try:
            fps = float(getattr(self._delegate, "fps", 0.0) or 0.0)
        except Exception:
            fps = 0.0
        return fps if fps > 0 else default

    def _duration_sec_safe(self) -> float:
        try:
            return float(getattr(self._delegate, "duration_sec", 0.0) or 0.0)
        except Exception:
            return 0.0

    def _should_use_cuda_pipe(self) -> bool:
        if _HWACCEL_MODE not in {"cuda", "nvdec"}:
            return False
        if platform.system() != "Linux":
            return False
        if not self._ffmpeg_path.exists():
            log.warn(
                "AMG_VIDEO_HWACCEL requests CUDA but ffmpeg-cuda missing; "
                "falling back to PyAV software sequential decode",
                ffmpeg_cuda_bin=str(self._ffmpeg_path),
            )
            return False
        return True

    def iter_frames_sequential(
        self,
        start_sec: float,
        end_sec: float,
        interval_sec: float,
    ) -> Iterator[Tuple[float, np.ndarray]]:
        yield from self._iter_frames_pipe(
            start_sec,
            end_sec,
            interval_sec,
            output_size=None,
        )

    def iter_frames_sequential_scaled(
        self,
        start_sec: float,
        end_sec: float,
        interval_sec: float,
        max_size: Tuple[int, int],
    ) -> Iterator[Tuple[float, np.ndarray]]:
        yield from self._iter_frames_pipe(
            start_sec,
            end_sec,
            interval_sec,
            output_size=max_size,
        )

    def _iter_frames_pipe(
        self,
        start_sec: float,
        end_sec: float,
        interval_sec: float,
        *,
        output_size: Optional[Tuple[int, int]],
    ) -> Iterator[Tuple[float, np.ndarray]]:
        if interval_sec <= 0:
            raise ValueError("interval_sec must be > 0")
        if not self._should_use_cuda_pipe():
            if output_size:
                yield from self._delegate.iter_frames_sequential_scaled(
                    start_sec,
                    end_sec,
                    interval_sec,
                    output_size,
                )
            else:
                yield from self._delegate.iter_frames_sequential(start_sec, end_sec, interval_sec)
            return

        self._delegate.open()
        width, height = self._delegate.frame_size
        if width <= 0 or height <= 0:
            log.warn(
                "ffmpeg-cuda path missing frame dimensions; falling back to PyAV",
                width=width,
                height=height,
            )
            if output_size:
                yield from self._delegate.iter_frames_sequential_scaled(
                    start_sec,
                    end_sec,
                    interval_sec,
                    output_size,
                )
            else:
                yield from self._delegate.iter_frames_sequential(start_sec, end_sec, interval_sec)
            return

        span = max(0.0, float(end_sec) - float(start_sec))
        if span <= 0:
            return

        out_w, out_h = width, height
        if output_size:
            out_w, out_h = _fit_within(width, height, output_size)
        frame_bytes = out_w * out_h * 3
        if output_size and (out_w, out_h) != (width, height):
            vf = f"fps=1/{float(interval_sec):.6f},scale={out_w}:{out_h}"
            hwaccel_args = ["-hwaccel", "cuda"]
        else:
            vf = f"fps=1/{float(interval_sec):.6f}"
            hwaccel_args = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
        cmd = [
            str(self._ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "error",
            *hwaccel_args,
            "-ss",
            f"{float(start_sec):.6f}",
            "-t",
            f"{span:.6f}",
            "-i",
            str(self.video_path),
            "-vf",
            vf,
            "-an",
            "-sn",
            "-dn",
            "-pix_fmt",
            "bgr24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]

        proc = None
        emitted = 0
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=frame_bytes * 2,
            )
            if proc.stdout is None:
                raise IOError("ffmpeg-cuda started without stdout pipe")
            ts = float(start_sec)
            while ts <= end_sec:
                buf = proc.stdout.read(frame_bytes)
                if not buf or len(buf) < frame_bytes:
                    break
                # Avoid an extra CPU memcpy per frame. ``buf`` is a distinct
                # bytes object per read() call, so the ndarray can safely hold
                # a view into it until the consumer is done.
                frame = np.frombuffer(buf, dtype=np.uint8).reshape((out_h, out_w, 3))
                yield (ts, frame)
                emitted += 1
                ts += float(interval_sec)
            rc = proc.wait(timeout=10.0)
            if rc != 0 and emitted == 0:
                err = b""
                if proc.stderr is not None:
                    err = proc.stderr.read()
                raise IOError(
                    f"ffmpeg-cuda failed with exit={rc}: {err.decode('utf-8', 'ignore')[:200]}"
                )
        except Exception as e:
            log.warn(
                "ffmpeg-cuda sequential decode failed; falling back to PyAV",
                error=str(e),
            )
            if output_size:
                yield from self._delegate.iter_frames_sequential_scaled(
                    start_sec,
                    end_sec,
                    interval_sec,
                    output_size,
                )
            else:
                yield from self._delegate.iter_frames_sequential(start_sec, end_sec, interval_sec)
        finally:
            if proc is not None:
                try:
                    if proc.poll() is None:
                        proc.kill()
                except Exception:
                    pass


# --- Public class -----------------------------------------------------------


class VideoReader:
    """
    Unified video reader supporting both sequential and random-access reads.

    Chooses the best backend at open time and exposes the v11.1 API:

        with VideoReader(path) as vr:
            frame = vr.get_frame_at(timestamp_sec=123.4)
            frames = vr.get_frames_at([t1, t2, t3])
            for ts, frame in vr.iter_frames_sequential(0, 60, 1.0):
                ...
    """

    def __init__(self, video_path: Path):
        self.video_path = Path(video_path)
        self._backend = self._select_backend()

    def _select_backend(self):
        # Honor the env override if set
        if _BACKEND_OVERRIDE == "opencv":
            return _OpenCVBackend(self.video_path)
        if _BACKEND_OVERRIDE == "ffmpeg_cuda":
            if not PYAV_AVAILABLE:
                log.warn("AMG_VIDEO_BACKEND=ffmpeg_cuda but PyAV not installed; using OpenCV")
                return _OpenCVBackend(self.video_path)
            return _FFmpegCudaBackend(self.video_path)
        if _BACKEND_OVERRIDE == "pyav":
            if not PYAV_AVAILABLE:
                log.warn("AMG_VIDEO_BACKEND=pyav but PyAV not installed; using OpenCV")
                return _OpenCVBackend(self.video_path)
            return _PyAVBackend(self.video_path)
        # Auto: prefer PyAV
        if (
            _BACKEND_OVERRIDE == "auto"
            and PYAV_AVAILABLE
            and _HWACCEL_MODE in {"cuda", "nvdec"}
        ):
            return _FFmpegCudaBackend(self.video_path)
        if PYAV_AVAILABLE:
            return _PyAVBackend(self.video_path)
        return _OpenCVBackend(self.video_path)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def open(self):
        try:
            self._backend.open()
        except Exception as e:
            # If PyAV/ffmpeg-cuda fails to open the file at all, fall back to OpenCV
            if isinstance(self._backend, (_PyAVBackend, _FFmpegCudaBackend)):
                log.warn("PyAV failed to open file; falling back to OpenCV", error=str(e))
                self._backend = _OpenCVBackend(self.video_path)
                self._backend.open()
            else:
                raise

    def close(self):
        if self._backend is not None:
            self._backend.close()

    @property
    def fps(self) -> float:
        return self._backend.fps

    @property
    def frame_count(self) -> int:
        return self._backend.frame_count

    @property
    def duration_sec(self) -> float:
        return self._backend.duration_sec

    @property
    def backend_name(self) -> str:
        if isinstance(self._backend, _FFmpegCudaBackend):
            return "ffmpeg-cuda+pyav"
        if isinstance(self._backend, _PyAVBackend):
            # See _PyAVBackend.open() for why we don't claim "videotoolbox" here:
            # PyAV 13.1 accepts the option without engaging HW decode, and
            # exposes no probe API to confirm. "vt-option-set" makes the
            # uncertainty visible to anyone reading logs/dashboards.
            return "pyav (vt-option-set)" if self._backend.hwaccel_used else "pyav-software"
        return "opencv"

    def get_frame_at(self, timestamp_sec: float) -> Optional[np.ndarray]:
        return self._backend.get_frame_at(timestamp_sec)

    def get_frames_at(self, timestamps_sec: List[float]) -> List[Optional[np.ndarray]]:
        return self._backend.get_frames_at(timestamps_sec)

    def iter_frames_sequential(
        self,
        start_sec: float,
        end_sec: float,
        interval_sec: float,
    ) -> Iterator[Tuple[float, np.ndarray]]:
        return self._backend.iter_frames_sequential(start_sec, end_sec, interval_sec)

    def iter_frames_sequential_scaled(
        self,
        start_sec: float,
        end_sec: float,
        interval_sec: float,
        max_size: Tuple[int, int],
    ) -> Iterator[Tuple[float, np.ndarray]]:
        if hasattr(self._backend, "iter_frames_sequential_scaled"):
            return self._backend.iter_frames_sequential_scaled(
                start_sec,
                end_sec,
                interval_sec,
                max_size,
            )
        return (
            (ts, _resize_to_fit(frame, max_size))
            for ts, frame in self._backend.iter_frames_sequential(start_sec, end_sec, interval_sec)
        )


def get_frame_at_timestamp(video_path: Path, timestamp_sec: float) -> Optional[np.ndarray]:
    """
    Convenience function: open video, get one frame, close.

    Don't use this for many frames in a row — open a VideoReader instead.
    """
    with VideoReader(video_path) as vr:
        return vr.get_frame_at(timestamp_sec)
