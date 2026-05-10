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

_PERFORMER_NAME_STOP_WORDS = {
    "scene", "video", "clip", "full", "preview", "trailer", "episode", "ep",
    "part", "vol", "volume", "hardcore", "squirting", "squirt", "orgy",
    "gangbang", "gang", "bang", "group", "sex", "pov", "anal", "blowjob",
    "deepthroat", "cream", "creampie", "facial", "cumshot", "threesome",
    "foursome", "solo", "amateur", "verified", "models", "hd", "uhd", "4k",
    "vr", "compilation", "collection", "take", "takes",
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
        "inferred_performers": infer_performers_from_title_text(raw_text),
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


def infer_performers_from_title_text(text: str, *, max_names: int = 5) -> List[str]:
    """Infer performer names from common scene-title filename shapes.

    This is intentionally conservative. It only trusts comma-separated cast
    lists and double-underscore cast prefixes, both of which appear in AMG
    source names. Explicit metadata still wins elsewhere in the pipeline.
    """
    raw = str(text or "")
    if not raw.strip():
        return []

    names: List[str] = []
    for chunk in _split_candidate_chunks(raw):
        for name in _extract_comma_list_names(chunk):
            _append_unique_name(names, name, max_names=max_names)
        for name in _extract_double_underscore_names(chunk):
            _append_unique_name(names, name, max_names=max_names)
        if len(names) >= max_names:
            break
    return names[:max_names]


def _split_candidate_chunks(raw: str) -> List[str]:
    chunks = [raw]
    chunks.extend(re.split(r"\s*\|\s*", raw))
    chunks.extend(re.split(r"(?<!\d)[-/]+(?!\d)", raw))
    return [c.strip() for c in chunks if c and c.strip()]


def _extract_comma_list_names(chunk: str) -> List[str]:
    if "," not in chunk:
        return []
    # Prefer text after a scene number when present, then stop at filename
    # separators before the descriptive title continues.
    candidates = re.split(r"(?:^|[_\s-])\d{1,5}[_\s-]+", chunk)
    out: List[str] = []
    for candidate in candidates:
        if "," not in candidate:
            continue
        head = re.split(r"[|]", candidate, maxsplit=1)[0]
        parts = [p for p in re.split(r"\s*,\s*", head) if p.strip()]
        if len(parts) < 2:
            continue
        cleaned = [_clean_name_token(p) for p in parts]
        cleaned = [p for p in cleaned if p]
        if len(cleaned) >= 2:
            out.extend(cleaned)
    return out


def _extract_double_underscore_names(chunk: str) -> List[str]:
    if "__" not in chunk:
        return []
    text = re.sub(r"^\s*\d{1,5}_+", "", chunk.strip())
    parts = [p for p in re.split(r"__+", text) if p.strip()]
    out: List[str] = []
    for part in parts:
        name = _clean_name_token(part)
        if not name:
            break
        out.append(name)
    return out if len(out) >= 2 else []


def _clean_name_token(token: str) -> str:
    text = str(token or "").strip().strip("_-.,;:()[]{}")
    if not text:
        return ""
    text = re.split(r"[|/\\]", text, maxsplit=1)[0]
    text = text.replace("__", " ").replace("_", " ")
    words: List[str] = []
    for raw_word in text.split():
        word = raw_word.strip().strip("_-.,;:()[]{}")
        if not word or word.isdigit():
            continue
        key = re.sub(r"[^a-z0-9]+", "", word.lower())
        if key in _PERFORMER_NAME_STOP_WORDS:
            break
        if not re.search(r"[A-Za-z]", word):
            continue
        words.append(word)
        if len(words) >= 3:
            break
    if not words:
        return ""
    name = " ".join(words)
    if _looks_like_title_phrase(name):
        return ""
    return name


def _looks_like_title_phrase(name: str) -> bool:
    tokens = [re.sub(r"[^a-z0-9]+", "", w.lower()) for w in str(name or "").split()]
    if not tokens:
        return True
    if any(t in _PERFORMER_NAME_STOP_WORDS for t in tokens):
        return True
    # Reject all-lowercase descriptive phrases, but allow short mononyms.
    if len(tokens) > 1 and name == name.lower():
        return True
    return False


def _append_unique_name(names: List[str], name: str, *, max_names: int) -> None:
    clean = " ".join(str(name or "").split())
    if not clean or len(names) >= max_names:
        return
    if clean.lower() in {x.lower() for x in names}:
        return
    names.append(clean)
