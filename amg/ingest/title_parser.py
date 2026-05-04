"""
Title parser — extract genres and scene type from filename/folder name.

Maps user-friendly description text to structured genre tags used by the AI
prompt and scoring system.
"""
import re
from pathlib import Path
from typing import List, Optional

# Genre detection patterns (case-insensitive)
# Order matters — more specific patterns first
GENRE_PATTERNS = {
    "DP": [r"\bdp\b", r"double penetration", r"double pen"],
    "GANGBANG": [r"gangbang", r"gang bang", r"gang-bang"],
    "THREESOME": [r"threesome", r"three some", r"three-some", r"\b3some\b"],
    "FOURSOME": [r"foursome", r"four some", r"\b4some\b"],
    "ORGY": [r"orgy", r"orgies"],
    "ANAL": [r"\banal\b", r"\bass fuck", r"butt sex", r"\bass to mouth\b", r"\batm\b"],
    "CREAMPIE": [r"creampie", r"cream pie", r"cum inside", r"internal cum"],
    "FACIAL": [r"\bfacial\b", r"cum on face", r"cum in face"],
    "SQUIRT": [r"squirt", r"gushing", r"squirting"],
    "POV": [r"\bpov\b", r"point of view", r"first person"],
    "MILF": [r"\bmilf\b", r"\bmom\b", r"mature mom"],
    "MATURE": [r"\bmature\b", r"\bgranny\b", r"older woman"],
    "LESBIAN": [r"lesbian", r"girl on girl", r"\bgirls only\b", r"\bg/g\b"],
    "INTERRACIAL": [r"interracial", r"\bbbc\b", r"\bblack guy\b", r"\bwhite girl black\b"],
    "BIG_TITS": [r"big tits", r"huge tits", r"big breasts", r"busty"],
    "BIG_ASS": [r"big ass", r"huge ass", r"big butt", r"thick"],
    "PETITE": [r"petite", r"\btiny\b", r"\bsmall\b"],
    "ASIAN": [r"\basian\b", r"japanese", r"chinese", r"korean", r"thai"],
    "LATINA": [r"latina", r"spanish girl", r"latin"],
    "EBONY": [r"ebony", r"black girl"],
    "GLAMOUR": [r"glamour", r"glamor", r"glamorous"],
    "GROUP": [r"\bgroup sex\b", r"\bgroup\b"],
    "COUPLE": [r"\bcouple\b", r"\bduo\b"],
    "SOLO": [r"\bsolo\b", r"masturbat"],
    "MASSAGE": [r"massage"],
    "BATH": [r"\bbath\b", r"shower", r"bathtub", r"hot tub"],
    "OUTDOOR": [r"outdoor", r"outside", r"public", r"\bbeach\b", r"forest", r"park"],
    "SWINGER": [r"swinger", r"\bswap\b", r"wife swap"],
    "OFFICE": [r"\boffice\b", r"\bboss\b", r"\bsecretary\b", r"\bemployee\b"],
    "TEACHER": [r"teacher", r"professor", r"\bclassroom\b"],
    "NURSE": [r"\bnurse\b", r"\bdoctor\b", r"hospital", r"medical"],
    "BIRTHDAY": [r"birthday", r"\bbday\b"],
    "VR": [r"\bvr\b", r"virtual reality", r"\b180\b", r"\b360\b"],
    "CUSTOM": [r"custom video", r"custom request"],
}


def parse_title(video_path: Path) -> dict:
    """
    Parse video path/filename to extract title metadata.

    Backwards-compatible wrapper. Internally now resolves a FolderContext
    (walking up ancestors when the immediate filename is generic) and delegates
    to ``parse_title_with_context``.
    """
    from amg.ingest.folder_context import resolve_folder_context

    ctx = resolve_folder_context(video_path)
    return parse_title_with_context(video_path, ctx)


def parse_title_with_context(video_path: Path, ctx) -> dict:
    """
    Parse title metadata using a pre-resolved FolderContext.

    The context lets us recover useful information when the scene file itself
    has a generic recorder filename (e.g. "Screen Recording 2025-10-24..."),
    by reading metadata from ancestor folders, JSON manifests, and walked-up
    folder names.

    Returns:
        {
            'raw_text': '<concatenated text scanned for genres>',
            'description': '<best-effort scene description>',
            'detected_genres': [...],
            'is_custom': bool,
            'is_vr': bool,
            'is_generic_filename': bool,
            'context_source': '<absolute path of source folder>' or None,
            'metadata_title': <title from metadata JSON, if any>,
        }
    """
    from amg.ingest.folder_context import best_text_for_parsing

    # Order: deepest ancestor first → bubble up. Folder names that contain a
    # performer code carry the description after the code.
    description = None
    folder_with_desc = None
    code_re = re.compile(r'^\s*\d+\s+[BG]{1,12}\s+-\s*(.+)$')
    for name in ctx.ancestor_names:
        m = code_re.match(name)
        if m:
            description = m.group(1).strip()
            folder_with_desc = name
            break

    # If no folder had an embedded description, prefer metadata.json description,
    # then folder name, then filename.
    if not description:
        description = (
            ctx.description
            or ctx.title
            or (ctx.ancestor_names[0] if ctx.ancestor_names else "")
            or (video_path.stem if not ctx.is_generic_filename else "")
        )

    raw_text = best_text_for_parsing(ctx)
    detected = detect_genres(raw_text)
    # Add user-supplied tags from the metadata JSON, mapped through the same
    # genre detector so we keep the canonical tag vocabulary.
    if ctx.tags:
        for extra in detect_genres(" ".join(ctx.tags)):
            if extra not in detected:
                detected.append(extra)

    return {
        "raw_text": raw_text,
        "description": description or "",
        "detected_genres": detected,
        "is_custom": "CUSTOM" in detected,
        "is_vr": "VR" in detected,
        "is_generic_filename": ctx.is_generic_filename,
        "context_source": str(ctx.source_folder) if ctx.source_folder else None,
        "metadata_title": ctx.title,
    }


def detect_genres(text: str) -> List[str]:
    """
    Detect genres mentioned in text.

    Returns deduplicated list of genre tags.
    """
    text_lower = text.lower()
    detected = []
    for genre, patterns in GENRE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                detected.append(genre)
                break  # One match per genre is enough
    return detected


def derive_primary_scene_type(genres: List[str], performer_count: Optional[int] = None) -> str:
    """
    Derive the primary scene type for AI prompting.

    Priority:
        1. Multi-performer scene types (GANGBANG > FOURSOME > THREESOME)
        2. Genre-based (DP, POV, SOLO, etc.)
        3. Performer-count-based (3 = THREESOME, 2 = COUPLE, 1 = SOLO)
        4. Default STANDARD
    """
    if "GANGBANG" in genres:
        return "GANGBANG"
    if "FOURSOME" in genres:
        return "FOURSOME"
    if "THREESOME" in genres:
        return "THREESOME"
    if "DP" in genres:
        return "DP"
    if "ORGY" in genres:
        return "GANGBANG"  # Treat as gangbang for scoring
    if "POV" in genres:
        return "POV"
    if "SOLO" in genres:
        return "SOLO"
    if "LESBIAN" in genres:
        return "LESBIAN"

    # Fall back to performer count
    if performer_count:
        if performer_count >= 5:
            return "GANGBANG"
        if performer_count == 4:
            return "FOURSOME"
        if performer_count == 3:
            return "THREESOME"
        if performer_count == 2:
            return "COUPLE"
        if performer_count == 1:
            return "SOLO"

    return "STANDARD"
