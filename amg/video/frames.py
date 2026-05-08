"""
Frame analysis: sharpness, motion measurement, adaptive calibration.

Sharpness: Laplacian variance — standard cheap-and-effective measure.
Motion: optical flow magnitude between consecutive frames (quick gate).
Calibration: sample 100 frames evenly, derive per-source thresholds.
"""
import cv2
import numpy as np
from pathlib import Path
from typing import Tuple

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

    # Resize for consistent measurement
    small = cv2.resize(frame_bgr, ANALYSIS_FRAME_SIZE)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
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

    try:
        flow = cv2.calcOpticalFlowFarneback(
            prev_gray, curr_gray,
            None,           # flow output
            0.5,            # pyr_scale
            3,              # levels
            15,             # winsize
            3,              # iterations
            5,              # poly_n
            1.2,            # poly_sigma
            0,              # flags
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
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    avg = gray.mean() / 255.0
    return avg < threshold


def average_brightness(frame_bgr: np.ndarray) -> float:
    """Return average brightness 0-1."""
    if frame_bgr is None:
        return 0.0
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    return float(gray.mean()) / 255.0
