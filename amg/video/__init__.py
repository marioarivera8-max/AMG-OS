"""Video module: frame extraction, metadata, sharpness, faces, deduplication."""
from amg.video.metadata import get_metadata
from amg.video.reader import VideoReader, get_frame_at_timestamp
from amg.video.frames import measure_sharpness, measure_motion, calibrate_thresholds
from amg.video.faces import detect_faces_in_frame, check_eye_whites, detect_rear_shot
from amg.video.dedup import compute_perceptual_hash, deduplicate_frames

__all__ = [
    "get_metadata",
    "VideoReader",
    "get_frame_at_timestamp",
    "measure_sharpness",
    "measure_motion",
    "calibrate_thresholds",
    "detect_faces_in_frame",
    "check_eye_whites",
    "detect_rear_shot",
    "compute_perceptual_hash",
    "deduplicate_frames",
]
