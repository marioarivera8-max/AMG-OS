"""
Contact sheet — AITG-branded summary image showing all covers.

Layout:
    Header (110px tall): scene info, performers, version, quality flag
    Grid: 3 columns of 640x360 thumbnails with filename labels
    Footer info: total covers, top-pick score, fallbacks used

Specs from v11_final_specification.md § XVIII.E:
    Total width: 2000px
    Margin: 15px
    Header colors: gold (studio), white (performers), yellow-green (scene/genres)
"""
from pathlib import Path
from typing import List, Optional
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

from amg.config import (
    CONTACT_SHEET_THUMB_SIZE,
    CONTACT_SHEET_COLS,
    CONTACT_SHEET_MARGIN,
    CONTACT_SHEET_LABEL_HEIGHT,
    CONTACT_SHEET_HEADER_HEIGHT,
    CONTACT_SHEET_BG_COLOR,
    CONTACT_SHEET_QUALITY,
)
from amg.utils.logging import get_logger

log = get_logger("output.contact_sheet")


def build_contact_sheet(
    output_path: Path,
    cover_paths: List[Path],
    scene_info: dict,
    quality_flag: str = "GOOD",
) -> bool:
    """
    Build a contact sheet image.

    Args:
        output_path: Where to save the contact sheet JPEG
        cover_paths: List of cover file paths in rank order
        scene_info: Dict with scene metadata (studio, performers, scene_type, etc.)
        quality_flag: GOOD/AI_GENERATED/REVIEW_NEEDED — affects header color

    Returns:
        True on success, False on failure.
    """
    if not cover_paths:
        log.warn("No covers to compose into contact sheet")
        return False

    try:
        # Calculate dimensions
        thumb_w, thumb_h = CONTACT_SHEET_THUMB_SIZE
        cols = CONTACT_SHEET_COLS
        margin = CONTACT_SHEET_MARGIN
        label_h = CONTACT_SHEET_LABEL_HEIGHT
        header_h = CONTACT_SHEET_HEADER_HEIGHT

        n = len(cover_paths)
        rows = (n + cols - 1) // cols  # Ceiling division

        cell_w = thumb_w + margin
        cell_h = thumb_h + label_h + margin

        total_w = cols * cell_w + margin
        total_h = header_h + rows * cell_h + margin

        # Create canvas
        canvas = Image.new("RGB", (total_w, total_h), CONTACT_SHEET_BG_COLOR)
        draw = ImageDraw.Draw(canvas)

        # Load fonts (with fallbacks)
        font_header = _load_font(28)
        font_subheader = _load_font(20)
        font_label = _load_font(16)
        font_small = _load_font(13)

        # === HEADER ===
        _draw_header(draw, total_w, header_h, scene_info, quality_flag,
                     font_header, font_subheader, font_small)

        # === COVER GRID ===
        for i, cover_path in enumerate(cover_paths):
            try:
                row = i // cols
                col = i % cols
                x = margin + col * cell_w
                y = header_h + row * cell_h

                # Load and resize cover
                cover_img = Image.open(cover_path)
                cover_img.thumbnail((thumb_w, thumb_h), Image.LANCZOS)

                # Center within cell if smaller after thumbnail
                paste_x = x + (thumb_w - cover_img.width) // 2
                paste_y = y + (thumb_h - cover_img.height) // 2
                canvas.paste(cover_img, (paste_x, paste_y))

                # Filename label below thumbnail
                label_text = cover_path.name
                # Truncate if too long
                if len(label_text) > 65:
                    label_text = label_text[:62] + "..."
                draw.text(
                    (x + 5, y + thumb_h + 4),
                    label_text,
                    fill=(220, 220, 220),
                    font=font_label,
                )
            except Exception as e:
                log.warn("Failed to add cover to sheet", index=i, error=str(e))

        # Save
        canvas.save(output_path, "JPEG", quality=CONTACT_SHEET_QUALITY, optimize=True)
        log.info("Contact sheet saved", path=str(output_path), covers=n)
        return True

    except Exception as e:
        log.error("Contact sheet generation failed", error=str(e))
        return False


def _draw_header(draw, width, height, info, quality_flag,
                 font_h, font_sub, font_small):
    """Draw the header section of the contact sheet."""
    studio = info.get("studio", "Unknown Studio")
    performers = info.get("performers", "")
    scene_type = info.get("scene_type", "")
    genres = info.get("genres", [])
    cover_count = info.get("cover_count", 0)
    tier_used = info.get("tier_used", "")
    version = info.get("version", "v11.0.0")

    # Line 1: Studio + AITG
    line1 = f"{studio} — AITG"
    draw.text((CONTACT_SHEET_MARGIN, 8), line1, fill=(255, 215, 0), font=font_h)

    # Line 2: Performers
    if performers:
        draw.text((CONTACT_SHEET_MARGIN, 42),
                  f"Performers: {performers}",
                  fill=(255, 255, 255), font=font_sub)

    # Line 3: Scene type and genres
    line3_parts = []
    if scene_type:
        line3_parts.append(f"Scene: {scene_type}")
    if genres:
        if isinstance(genres, list):
            genres_str = ", ".join(genres[:5])
        else:
            genres_str = str(genres)
        line3_parts.append(f"Genres: {genres_str}")
    line3 = " | ".join(line3_parts)
    draw.text((CONTACT_SHEET_MARGIN, 68),
              line3, fill=(180, 180, 100), font=font_small)

    # Line 4: Version, tier, quality, count
    quality_color = {
        "GOOD": (100, 220, 100),
        "AI_GENERATED": (220, 180, 100),
        "REVIEW_NEEDED": (220, 100, 100),
    }.get(quality_flag, (180, 180, 180))

    line4 = f"{version} | Tier: {tier_used} | Quality: {quality_flag} | {cover_count} covers"
    draw.text((CONTACT_SHEET_MARGIN, 88),
              line4, fill=quality_color, font=font_small)


def _load_font(size: int) -> ImageFont.ImageFont:
    """Load a font with fallback chain."""
    # Try common system fonts in order
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",            # macOS
        "/System/Library/Fonts/Supplemental/Arial.ttf",   # macOS alt
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", # Linux
        "C:/Windows/Fonts/arial.ttf",                      # Windows
    ]
    for path in candidates:
        try:
            if Path(path).exists():
                return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    # Final fallback: PIL default (low quality but always works)
    try:
        return ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()
