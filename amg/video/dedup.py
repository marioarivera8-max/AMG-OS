"""
Frame deduplication via perceptual hashing.

Many candidate frames look near-identical (consecutive frames in a slow scene).
Pre-filter with dHash + Hamming distance ≤ 5 (industry standard for "near duplicate").

Saves AI calls and ensures variety in final cover set.
"""
import imagehash
import cv2
import numpy as np
from PIL import Image
from typing import List, Optional

from amg.config import DEDUP_HASH_SIZE, DEDUP_HAMMING_THRESHOLD


def compute_perceptual_hash(frame_bgr: np.ndarray) -> Optional[imagehash.ImageHash]:
    """
    Compute dHash for a frame.

    Returns ImageHash object or None if computation fails.
    """
    if frame_bgr is None:
        return None
    try:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        return imagehash.dhash(pil_img, hash_size=DEDUP_HASH_SIZE)
    except Exception:
        return None


def are_near_duplicates(hash1, hash2, threshold: int = DEDUP_HAMMING_THRESHOLD) -> bool:
    """
    Compare two perceptual hashes.

    Returns True if Hamming distance <= threshold (default 5 = near duplicate).
    """
    if hash1 is None or hash2 is None:
        return False
    return (hash1 - hash2) <= threshold


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
