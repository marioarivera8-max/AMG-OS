"""
Frame deduplication via perceptual hashing.

GPU migration notes:
- v11.x used PIL/imagehash on CPU for every candidate.
- This module now computes dHash natively with OpenCV/Numpy and can use
  cv2.cuda for preprocess (gray+resize) when enabled.
- Hamming-threshold semantics remain unchanged.
"""
import os
from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np

from amg.config import DEDUP_HASH_SIZE, DEDUP_HAMMING_THRESHOLD
from amg.utils.logging import get_logger

log = get_logger("video.dedup")


def _gpu_enabled() -> bool:
    flag = os.environ.get("AMG_GPU_DEDUP_ENABLED")
    if flag is None:
        flag = os.environ.get("AMG_GPU_CV_ENABLED", "0")
    if str(flag).strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    if not hasattr(cv2, "cuda"):
        return False
    try:
        return int(cv2.cuda.getCudaEnabledDeviceCount()) > 0
    except Exception:
        return False


@dataclass(frozen=True)
class PerceptualHash:
    bits: int
    size: int

    def __sub__(self, other) -> int:
        if not isinstance(other, PerceptualHash):
            return 10**9
        return int((self.bits ^ other.bits).bit_count())

    def __str__(self) -> str:
        width = self.size * self.size
        return f"{self.bits:0{max(1, width // 4)}x}"


def _frame_to_gray_resized(frame_bgr: np.ndarray, hash_size: int) -> Optional[np.ndarray]:
    out_w = hash_size + 1
    out_h = hash_size
    if _gpu_enabled():
        try:
            gpu = cv2.cuda_GpuMat()
            gpu.upload(frame_bgr)
            gray = cv2.cuda.cvtColor(gpu, cv2.COLOR_BGR2GRAY)
            resized = cv2.cuda.resize(gray, (out_w, out_h), interpolation=cv2.INTER_AREA)
            return resized.download()
        except Exception as exc:  # noqa: BLE001
            log.warn("GPU dedup preprocess failed; using CPU fallback", error=str(exc))
    try:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        return cv2.resize(gray, (out_w, out_h), interpolation=cv2.INTER_AREA)
    except Exception:
        return None


def compute_perceptual_hash(frame_bgr: np.ndarray) -> Optional[PerceptualHash]:
    """
    Compute dHash for a frame.

    Returns ImageHash object or None if computation fails.
    """
    if frame_bgr is None:
        return None
    try:
        hs = max(1, int(DEDUP_HASH_SIZE))
        small = _frame_to_gray_resized(frame_bgr, hs)
        if small is None:
            return None
        # dHash: horizontal adjacent comparisons.
        diff = small[:, 1:] > small[:, :-1]
        bits = 0
        for bit in diff.reshape(-1):
            bits = (bits << 1) | int(bool(bit))
        return PerceptualHash(bits=bits, size=hs)
    except Exception:
        return None


def compute_perceptual_hash_from_gray(gray: np.ndarray) -> Optional[PerceptualHash]:
    """Compute dHash from an already-grayscale analysis frame."""
    if gray is None:
        return None
    try:
        hs = max(1, int(DEDUP_HASH_SIZE))
        small = cv2.resize(gray, (hs + 1, hs), interpolation=cv2.INTER_AREA)
        diff = small[:, 1:] > small[:, :-1]
        bits = 0
        for bit in diff.reshape(-1):
            bits = (bits << 1) | int(bool(bit))
        return PerceptualHash(bits=bits, size=hs)
    except Exception:
        return None


def are_near_duplicates(hash1, hash2, threshold: int = DEDUP_HAMMING_THRESHOLD) -> bool:
    """
    Compare two perceptual hashes.

    Returns True if Hamming distance <= threshold (default 5 = near duplicate).
    """
    if hash1 is None or hash2 is None:
        return False
    try:
        return (hash1 - hash2) <= threshold
    except Exception:
        return False


def deduplicate_frames(
    frames_with_meta: List[dict],
    threshold: int = DEDUP_HAMMING_THRESHOLD,
) -> List[dict]:
    """
    Remove near-duplicate frames from a list.

    Keeps first occurrence of each unique-looking frame.

    Args:
        frames_with_meta: List of dicts with at least 'frame' (BGR ndarray)
                          and 'timestamp_sec'. Other fields preserved.
        threshold: Hamming distance threshold for "near duplicate".

    Returns:
        Filtered list with duplicates removed.
    """
    if not frames_with_meta:
        return []

    seen_hashes = []
    kept = []

    for entry in frames_with_meta:
        frame = entry.get("frame")
        if frame is None:
            continue

        h = compute_perceptual_hash(frame)
        if h is None:
            # Can't hash — keep it (better safe than sorry)
            kept.append(entry)
            continue

        # Check against all seen hashes
        is_dup = any(are_near_duplicates(h, prev, threshold) for prev in seen_hashes)
        if not is_dup:
            seen_hashes.append(h)
            entry = dict(entry)  # Don't mutate input
            entry["_phash"] = str(h)
            kept.append(entry)

    return kept


def cluster_dedup(
    frames_with_meta: List[dict],
    threshold: int = DEDUP_HAMMING_THRESHOLD,
) -> List[List[dict]]:
    """
    Group frames into clusters of near-duplicates.

    Returns list of clusters (each cluster is a list of frames).
    Useful for picking the best representative from each cluster.
    """
    if not frames_with_meta:
        return []

    clusters: List[List[dict]] = []
    cluster_hashes: List = []  # One hash per cluster (representative)

    for entry in frames_with_meta:
        frame = entry.get("frame")
        if frame is None:
            continue

        h = compute_perceptual_hash(frame)
        if h is None:
            clusters.append([entry])
            cluster_hashes.append(None)
            continue

        # Find matching cluster
        matched = False
        for i, cluster_h in enumerate(cluster_hashes):
            if cluster_h and are_near_duplicates(h, cluster_h, threshold):
                clusters[i].append(entry)
                matched = True
                break

        if not matched:
            clusters.append([entry])
            cluster_hashes.append(h)

    return clusters
