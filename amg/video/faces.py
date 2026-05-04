"""
Face detection.

Used for:
- Face count (signals scene type, gates scoring)
- Eye whites detection (Tier B signal: B2 +1.5)
- Rear-shot detection (Tier A: DB4 deal breaker if rear without ass visible)

Uses OpenCV Haar Cascades. Not as accurate as DNN-based detection but:
- Fast (no GPU needed)
- No model download required (built into OpenCV)
- Sufficient for our scoring purposes (we use AI for the hard cases)
"""
import cv2
import numpy as np
from typing import List, Tuple, Optional


# Lazy-loaded classifiers (loading is ~50ms, do once)
_face_cascade = None
_eye_cascade = None


def _get_face_cascade():
    global _face_cascade
    if _face_cascade is None:
        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _face_cascade = cv2.CascadeClassifier(path)
    return _face_cascade


def _get_eye_cascade():
    global _eye_cascade
    if _eye_cascade is None:
        path = cv2.data.haarcascades + "haarcascade_eye.xml"
        _eye_cascade = cv2.CascadeClassifier(path)
    return _eye_cascade


def detect_faces_in_frame(frame_bgr: np.ndarray) -> List[Tuple[int, int, int, int]]:
    """
    Detect faces in a frame.

    Returns list of (x, y, w, h) bounding boxes.
    Empty list if no faces detected (or detection failed).
    """
    if frame_bgr is None:
        return []

    try:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        cascade = _get_face_cascade()
        faces = cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(40, 40),
        )
        # detectMultiScale returns ndarray or empty tuple
        if isinstance(faces, np.ndarray):
            return [tuple(f) for f in faces]
        return []
    except cv2.error:
        return []


def check_eye_whites(frame_bgr: np.ndarray, face_box: Optional[Tuple] = None) -> dict:
    """
    Check if eyes are visible (eye whites detection).

    Returns:
        {
            'eyes_detected': int,    # Count of eye regions found
            'has_eye_whites': bool,  # True if at least one eye region has visible whites
            'confidence': float,     # 0-1
        }
    """
    result = {
        "eyes_detected": 0,
        "has_eye_whites": False,
        "confidence": 0.0,
    }

    if frame_bgr is None:
        return result

    try:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

        # If face_box provided, restrict eye search to upper half of face
        if face_box is not None:
            x, y, w, h = face_box
            # Upper 60% of face (where eyes are)
            roi_y_end = y + int(h * 0.6)
            roi = gray[y:roi_y_end, x:x + w]
        else:
            roi = gray

        cascade = _get_eye_cascade()
        eyes = cascade.detectMultiScale(
            roi,
            scaleFactor=1.1,
            minNeighbors=4,
            minSize=(15, 15),
        )

        if not isinstance(eyes, np.ndarray) or len(eyes) == 0:
            return result

        result["eyes_detected"] = len(eyes)

        # Check if any eye region has bright pixels (whites)
        for ex, ey, ew, eh in eyes:
            eye_region = roi[ey:ey + eh, ex:ex + ew]
            if eye_region.size == 0:
                continue
            # Eye whites: pixels brighter than 180 in 0-255 grayscale
            bright_pixel_ratio = np.sum(eye_region > 180) / eye_region.size
            if bright_pixel_ratio > 0.05:  # 5%+ bright pixels = whites visible
                result["has_eye_whites"] = True
                result["confidence"] = min(1.0, bright_pixel_ratio * 5)
                break

        return result
    except cv2.error:
        return result


def detect_rear_shot(frame_bgr: np.ndarray) -> dict:
    """
    Heuristic detection for rear-shot composition.

    Rear shot characteristics:
    - No frontal face detected
    - Significant skin coverage in lower-center
    - Symmetric horizontal composition (suggesting butt/back view)

    This is HEURISTIC — AI vision model has final say.

    Returns:
        {
            'is_rear': bool,
            'confidence': float,
            'reason': str,
        }
    """
    if frame_bgr is None:
        return {"is_rear": False, "confidence": 0.0, "reason": "no_frame"}

    h, w = frame_bgr.shape[:2]

    # 1. Check for absence of frontal face
    faces = detect_faces_in_frame(frame_bgr)
    has_face = len(faces) > 0

    # 2. Check skin coverage in lower-center region
    skin_mask = _compute_skin_mask(frame_bgr)
    lower_center = skin_mask[h // 2:, w // 4:3 * w // 4]
    skin_ratio_lower = np.sum(lower_center > 0) / lower_center.size if lower_center.size else 0

    # Decision logic
    if has_face:
        # Frontal face = not a rear shot
        return {
            "is_rear": False,
            "confidence": 0.9,
            "reason": "frontal_face_detected",
        }

    if skin_ratio_lower > 0.4:
        return {
            "is_rear": True,
            "confidence": min(1.0, skin_ratio_lower * 1.5),
            "reason": "no_face_with_lower_skin_dominance",
        }

    return {
        "is_rear": False,
        "confidence": 0.3,
        "reason": "no_face_no_skin_dominance",
    }


def _compute_skin_mask(frame_bgr: np.ndarray) -> np.ndarray:
    """
    Simple HSV-based skin detection.

    Returns binary mask (255 where skin, 0 elsewhere).
    """
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    # Multi-tone skin range (handles various skin tones)
    lower1 = np.array([0, 30, 60], dtype=np.uint8)
    upper1 = np.array([20, 150, 255], dtype=np.uint8)
    mask1 = cv2.inRange(hsv, lower1, upper1)

    lower2 = np.array([170, 30, 60], dtype=np.uint8)
    upper2 = np.array([180, 150, 255], dtype=np.uint8)
    mask2 = cv2.inRange(hsv, lower2, upper2)

    return cv2.bitwise_or(mask1, mask2)


def compute_skin_dominance(frame_bgr: np.ndarray) -> float:
    """
    Compute fraction of frame that's skin-colored.

    Returns 0-1 ratio. Used as quick gate for "is this a sex scene frame?"
    """
    if frame_bgr is None:
        return 0.0
    mask = _compute_skin_mask(frame_bgr)
    return float(np.sum(mask > 0)) / mask.size if mask.size else 0.0
