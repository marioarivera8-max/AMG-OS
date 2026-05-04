"""
Video reader.

Wraps OpenCV (sequential reads) and decord (random access) for efficiency.
Decord is 2x faster for random-access frame extraction (cluster sampling,
finish hunter, buildup hunter). OpenCV is comparable for sequential reads.

Usage:
    with VideoReader(path) as vr:
        frame = vr.get_frame_at(timestamp_sec=123.4)
        frames = vr.get_frames_at([t1, t2, t3])
"""
import cv2
from pathlib import Path
from typing import Optional, List, Iterator
import numpy as np

# decord is optional — fall back to OpenCV if not installed
try:
    import decord
    DECORD_AVAILABLE = True
except ImportError:
    DECORD_AVAILABLE = False


class VideoReader:
    """
    Unified video reader supporting both sequential and random-access reads.

    For random access (single timestamp or list of timestamps), uses decord
    when available (2x faster). Falls back to OpenCV.

    For sequential iteration (Tier 1/2/3 scans), uses OpenCV directly.
    """

    def __init__(self, video_path: Path):
        self.video_path = Path(video_path)
        self._cv_capture = None
        self._decord_reader = None
        self._fps = None
        self._frame_count = None
        self._duration = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def open(self):
        """Open underlying readers."""
        # Always open OpenCV for sequential operations
        self._cv_capture = cv2.VideoCapture(str(self.video_path))
        if not self._cv_capture.isOpened():
            raise IOError(f"Cannot open video: {self.video_path}")

        self._fps = self._cv_capture.get(cv2.CAP_PROP_FPS)
        self._frame_count = int(self._cv_capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if self._fps > 0:
            self._duration = self._frame_count / self._fps
        else:
            self._duration = 0

        # Lazy-open decord (only if random access requested)
        # See _ensure_decord()

    def close(self):
        """Release readers."""
        if self._cv_capture is not None:
            self._cv_capture.release()
            self._cv_capture = None
        self._decord_reader = None

    def _ensure_decord(self):
        """Open decord reader on first random-access request."""
        if not DECORD_AVAILABLE:
            return False
        if self._decord_reader is None:
            try:
                self._decord_reader = decord.VideoReader(
                    str(self.video_path),
                    ctx=decord.cpu(0),
                    num_threads=4,
                )
            except Exception:
                self._decord_reader = None
                return False
        return True

    @property
    def fps(self) -> float:
        if self._fps is None:
            self.open()
        return self._fps or 0

    @property
    def frame_count(self) -> int:
        if self._frame_count is None:
            self.open()
        return self._frame_count or 0

    @property
    def duration_sec(self) -> float:
        if self._duration is None:
            self.open()
        return self._duration or 0

    def get_frame_at(self, timestamp_sec: float) -> Optional[np.ndarray]:
        """
        Get a single frame at the given timestamp (in seconds).

        Returns BGR ndarray (OpenCV convention) or None if read fails.
        Uses decord for speed if available.
        """
        if timestamp_sec < 0 or timestamp_sec > self.duration_sec:
            return None

        # Try decord (2x faster for random access)
        if self._ensure_decord():
            try:
                frame_idx = int(timestamp_sec * self.fps)
                frame_idx = max(0, min(frame_idx, self.frame_count - 1))
                frame_rgb = self._decord_reader[frame_idx].asnumpy()
                # Decord returns RGB, convert to BGR for OpenCV consistency
                return cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
            except Exception:
                pass  # Fall through to OpenCV

        # Fallback: OpenCV
        if self._cv_capture is None:
            self.open()
        self._cv_capture.set(cv2.CAP_PROP_POS_MSEC, timestamp_sec * 1000)
        ret, frame = self._cv_capture.read()
        return frame if ret else None

    def get_frames_at(self, timestamps_sec: List[float]) -> List[Optional[np.ndarray]]:
        """
        Batch frame extraction. Uses decord's get_batch when possible.

        Returns list of frames in same order as timestamps_sec.
        Failed reads return None.
        """
        if not timestamps_sec:
            return []

        # Try decord batch (most efficient)
        if self._ensure_decord():
            try:
                indices = [
                    max(0, min(int(t * self.fps), self.frame_count - 1))
                    for t in timestamps_sec
                ]
                batch = self._decord_reader.get_batch(indices).asnumpy()
                # batch is (N, H, W, 3) RGB
                result = []
                for i in range(batch.shape[0]):
                    bgr = cv2.cvtColor(batch[i], cv2.COLOR_RGB2BGR)
                    result.append(bgr)
                return result
            except Exception:
                pass

        # Fallback: one-by-one
        return [self.get_frame_at(t) for t in timestamps_sec]

    def iter_frames_sequential(
        self,
        start_sec: float,
        end_sec: float,
        interval_sec: float,
    ) -> Iterator[tuple]:
        """
        Iterate frames sequentially in a time range.

        Yields (timestamp_sec, frame_bgr) tuples.
        Uses OpenCV (faster for sequential reads).
        """
        if self._cv_capture is None:
            self.open()

        timestamp = start_sec
        while timestamp <= end_sec:
            self._cv_capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ret, frame = self._cv_capture.read()
            if ret:
                yield (timestamp, frame)
            timestamp += interval_sec


def get_frame_at_timestamp(video_path: Path, timestamp_sec: float) -> Optional[np.ndarray]:
    """
    Convenience function: open video, get one frame, close.

    Don't use this for many frames in a row — open a VideoReader instead.
    """
    with VideoReader(video_path) as vr:
        return vr.get_frame_at(timestamp_sec)
