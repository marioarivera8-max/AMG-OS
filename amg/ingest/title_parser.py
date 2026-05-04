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

    Returns:
        {
            'raw_text': '<filename and folder concatenated>',
            'description': '<extracted description portion>',
            'detected_genres': [...],
            'is_custom': bool,
            'is_vr': bool,
        }
    """
    # Combine folder name + filename for max context
    folder_name = video_path.parent.name
    filename = video_path.stem
    raw_text = f"{folder_name} | {filename}"

    # Try to extract description after performer code (if present)
    # Pattern: "27 BBGG - couple swap with jimmy and tabatha"
    desc_match = re.match(r'^\s*\d+\s+[BG]{2,12}\s+-\s*(.+)$', folder_name)
    if desc_match:
        description = desc_match.group(1).strip()
    else:
        description = folder_name

    detected = detect_genres(raw_text)

    return {
        "raw_text": raw_text,
        "description": description,
        "detected_genres": detected,
        "is_custom": "CUSTOM" in detected,
        "is_vr": "VR" in detected,
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
