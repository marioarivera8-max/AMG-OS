"""
Frame analysis: sharpness, motion measurement, adaptive calibration.

GPU migration notes:
- This module now exposes an optional CUDA-backed analysis path for hot CV ops
  (resize + gray conversion + Laplacian sharpness + frame-diff motion proxy).
- CPU remains the source-of-truth fallback on every host.
- Callers should treat GPU as opportunistic acceleration, not a hard dependency.
"""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
try:  # Optional phase-5 kernel backend.
    import cupy as cp  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    cp = None

from amg.config import (
    ANALYSIS_FRAME_SIZE,
    CALIBRATION_SAMPLE_COUNT,
    CALIBRATION_MAX_DURATION_SEC,
    TIER_1_PERCENTILE,
    TIER_2_PERCENTILE,
    TIER_3_PERCENTILE,
    SHARPNESS_HARD_FLOOR,
)
from amg.video.reader import VideoReader
from amg.utils.logging import get_logger

log = get_logger("video.frames")


def _cuda_device_available() -> bool:
    if str(os.environ.get("AMG_GPU_CV_ENABLED", "0")).strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    if not hasattr(cv2, "cuda"):
        return False
    try:
        return int(cv2.cuda.getCudaEnabledDeviceCount()) > 0
    except Exception:
        return False


@dataclass(frozen=True)
class CvRuntime:
    mode: str  # "gpu" | "cpu"
    backend: str  # "opencv-cuda" | "cupy" | "cpu"
    reason: str


_RUNTIME: Optional[CvRuntime] = None


def runtime() -> CvRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        backend_pref = str(os.environ.get("AMG_GPU_CV_BACKEND", "opencv_cuda")).strip().lower()
        if _cuda_device_available() and backend_pref == "cupy" and cp is not None:
            _RUNTIME = CvRuntime(
                mode="gpu",
                backend="cupy",
                reason="AMG_GPU_CV_ENABLED + CuPy backend selected",
            )
        elif _cuda_device_available():
            _RUNTIME = CvRuntime(
                mode="gpu",
                backend="opencv-cuda",
                reason="AMG_GPU_CV_ENABLED + CUDA device",
            )
        else:
            _RUNTIME = CvRuntime(mode="cpu", backend="cpu", reason="cuda unavailable or disabled")
    return _RUNTIME


def runtime_info() -> dict:
    r = runtime()
    return {"mode": r.mode, "backend": r.backend, "reason": r.reason}


def _gpu_resize_gray(frame_bgr: np.ndarray) -> Optional[np.ndarray]:
    """Return ANALYSIS_FRAME_SIZE gray frame via cv2.cuda when available."""
    if runtime().mode != "gpu":
        return None
    try:
        gpu = cv2.cuda_GpuMat()
        gpu.upload(frame_bgr)
        resized = cv2.cuda.resize(gpu, ANALYSIS_FRAME_SIZE, interpolation=cv2.INTER_AREA)
        gray = cv2.cuda.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        return gray.download()
    except Exception as exc:  # noqa: BLE001 - GPU path must be best-effort
        log.warn("GPU resize/gray failed; using CPU fallback", error=str(exc))
        return None


def analysis_gray(frame_bgr: np.ndarray) -> np.ndarray:
    """Canonical analysis-sized grayscale frame for downstream CV metrics."""
    if frame_bgr is None:
        return np.zeros((ANALYSIS_FRAME_SIZE[1], ANALYSIS_FRAME_SIZE[0]), dtype=np.uint8)
    gray = _gpu_resize_gray(frame_bgr)
    if gray is not None:
        return gray
    small = cv2.resize(frame_bgr, ANALYSIS_FRAME_SIZE)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def measure_sharpness(frame_bgr: np.ndarray) -> float:
    """
    Measure frame sharpness via Laplacian variance.

    Higher = sharper. Typical ranges:
        < 100:    very blurry
        100-300:  acceptable
        300-700:  sharp
        > 700:    very sharp (4K masters)

    Frame can be any size — we'll work on grayscale at analysis size.
    """
    if frame_bgr is None:
        return 0.0

    gray = analysis_gray(frame_bgr)
    if runtime().mode == "gpu":
        try:
            gpu_gray = cv2.cuda_GpuMat()
            gpu_gray.upload(gray)
            lap = cv2.cuda.createLaplacianFilter(cv2.CV_8U, cv2.CV_32F, ksize=3).apply(gpu_gray)
            lap_cpu = lap.download()
            return float(lap_cpu.var())
        except Exception as exc:  # noqa: BLE001
            log.warn("GPU sharpness failed; using CPU fallback", error=str(exc))
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def measure_motion(prev_gray: np.ndarray, curr_gray: np.ndarray) -> float:
    """
    Measure motion between two consecutive grayscale frames.

    Uses Farneback optical flow. Returns mean magnitude.
    Higher = more motion.

    Frames should be at ANALYSIS_FRAME_SIZE for consistent scoring.
    """
    if prev_gray is None or curr_gray is None:
        return 0.0
    if prev_gray.shape != curr_gray.shape:
        return 0.0

    # Fast GPU-friendly proxy first: mean absolute frame delta.
    if runtime().mode == "gpu":
        if runtime().backend == "cupy" and cp is not None:
            try:
                prev_gpu = cp.asarray(prev_gray)
                curr_gpu = cp.asarray(curr_gray)
                diff = cp.abs(curr_gpu.astype(cp.float32) - prev_gpu.astype(cp.float32))
                return float(cp.mean(diff).get()) / 32.0
            except Exception as exc:  # noqa: BLE001
                log.warn("CuPy motion kernel failed; using fallback", error=str(exc))
        try:
            g0 = cv2.cuda_GpuMat()
            g1 = cv2.cuda_GpuMat()
            g0.upload(prev_gray)
            g1.upload(curr_gray)
            diff = cv2.cuda.absdiff(g0, g1).download()
            return float(diff.mean()) / 32.0
        except Exception as exc:  # noqa: BLE001
            log.warn("GPU motion proxy failed; using Farneback CPU", error=str(exc))
    try:
        flow = cv2.calcOpticalFlowFarneback(
            prev_gray, curr_gray,
            None,
            0.5,
            3,
            15,
            3,
            5,
            1.2,
            0,
        )
        magnitude = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
        return float(magnitude.mean())
    except cv2.error:
        return 0.0


def calibrate_thresholds(video_path: Path, duration_sec: float) -> dict:
    """
    Sample frames evenly across video duration, derive adaptive thresholds.

    Returns:
        {
            'tier_1_floor': float,    # 75th percentile of sharpness
            'tier_2_floor': float,    # 50th percentile
            'tier_3_floor': float,    # 25th percentile
            'sharpness_samples': [...],
            'samples_collected': int,
            'no_variation': bool,     # True if all samples nearly identical
            'all_blurry': bool,       # True if 75th pct still below hard floor
        }
    """
    sharpnesses = []

    effective_duration = float(duration_sec)
    if CALIBRATION_MAX_DURATION_SEC > 0:
        effective_duration = min(effective_duration, float(CALIBRATION_MAX_DURATION_SEC))
    sample_count = max(1, int(CALIBRATION_SAMPLE_COUNT))

    with VideoReader(video_path) as vr:
        sample_interval = effective_duration / sample_count if sample_count > 0 else effective_duration
        timestamps = [i * sample_interval for i in range(sample_count)]
        frames = vr.get_frames_at(timestamps)

        for frame in frames:
            if frame is not None:
                sharp = measure_sharpness(frame)
                sharpnesses.append(sharp)

    if not sharpnesses:
        # Couldn't read any frames
        return {
            "tier_1_floor": SHARPNESS_HARD_FLOOR,
            "tier_2_floor": SHARPNESS_HARD_FLOOR,
            "tier_3_floor": SHARPNESS_HARD_FLOOR,
            "sharpness_samples": [],
            "samples_collected": 0,
            "no_variation": True,
            "all_blurry": True,
        }

    sorted_samples = sorted(sharpnesses)
    n = len(sorted_samples)

    # Percentile calculation (simple)
    def pct(p: float) -> float:
        idx = int(n * p / 100)
        idx = max(0, min(idx, n - 1))
        return sorted_samples[idx]

    tier_1 = max(pct(TIER_1_PERCENTILE), SHARPNESS_HARD_FLOOR)
    tier_2 = max(pct(TIER_2_PERCENTILE), SHARPNESS_HARD_FLOOR / 2)
    tier_3 = max(pct(TIER_3_PERCENTILE), SHARPNESS_HARD_FLOOR / 4)

    # Detect degenerate cases
    sample_range = sorted_samples[-1] - sorted_samples[0]
    no_variation = sample_range < 50  # Very little variation = solid color/test pattern
    all_blurry = pct(75) < SHARPNESS_HARD_FLOOR

    return {
        "tier_1_floor": tier_1,
        "tier_2_floor": tier_2,
        "tier_3_floor": tier_3,
        "sharpness_samples": sharpnesses,
        "samples_collected": n,
        "no_variation": no_variation,
        "all_blurry": all_blurry,
    }


def is_frame_too_dark(frame_bgr: np.ndarray, threshold: float = 0.1) -> bool:
    """
    Quick check if frame is mostly black (transition or fade-out).

    Returns True if average brightness < threshold (0-1 scale).
    """
    if frame_bgr is None:
        return True
    gray = analysis_gray(frame_bgr)
    avg = gray.mean() / 255.0
    return avg < threshold


def average_brightness(frame_bgr: np.ndarray) -> float:
    """Return average brightness 0-1."""
    if frame_bgr is None:
        return 0.0
    gray = analysis_gray(frame_bgr)
    return float(gray.mean()) / 255.0
