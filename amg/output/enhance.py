"""
Auto-enhance pipeline for covers.

Conservative philosophy: subtle enhancement, never aggressive.
Buyers want covers that look professional, not over-processed.

Operations (in order):
1. +10% saturation
2. +5% contrast
3. +15% sharpness
4. Brightness boost only if scene is dark (avg < 0.35)
"""
from PIL import Image, ImageEnhance

from amg.config import (
    ENHANCE_SATURATION,
    ENHANCE_CONTRAST,
    ENHANCE_SHARPNESS,
    ENHANCE_BRIGHTNESS_THRESHOLD,
    ENHANCE_BRIGHTNESS_BOOST,
)


def auto_enhance(pil_img: Image.Image) -> Image.Image:
    """
    Apply auto-enhancement pipeline to a PIL image.

    Returns enhanced PIL image (input not mutated).
    """
    if pil_img is None:
        return pil_img

    # Operate on a copy
    img = pil_img.copy()

    # Convert to RGB if needed
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")

    # 1. Color/saturation
    img = ImageEnhance.Color(img).enhance(ENHANCE_SATURATION)

    # 2. Contrast
    img = ImageEnhance.Contrast(img).enhance(ENHANCE_CONTRAST)

    # 3. Sharpness
    img = ImageEnhance.Sharpness(img).enhance(ENHANCE_SHARPNESS)

    # 4. Conditional brightness boost
    avg_brightness = _compute_avg_brightness(img)
    if avg_brightness < ENHANCE_BRIGHTNESS_THRESHOLD:
        img = ImageEnhance.Brightness(img).enhance(ENHANCE_BRIGHTNESS_BOOST)

    return img


def _compute_avg_brightness(img: Image.Image) -> float:
    """Compute average brightness 0-1."""
    gray = img.convert("L")
    # Get histogram and compute mean
    hist = gray.histogram()
    total_pixels = gray.width * gray.height
    if total_pixels == 0:
        return 0.5
    weighted_sum = sum(i * count for i, count in enumerate(hist))
    avg = weighted_sum / total_pixels
    return avg / 255.0
