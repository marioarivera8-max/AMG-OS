"""
Parse AI scoring responses into structured data.

Expected formats:

Tier A fail:
    TIER_A_FAIL: DB2
    SCORE: 0
    END

Tier A pass:
    TIER_A_PASS: yes
    TIER_B_PRESENT: B1,B3,B6
    TIER_C_PRESENT: C1
    TIER_D_PRESENT: D2
    SCORE: 75.0
    TYPE: SEX_ACT
    GAZE: DIRECT
    AESTHETIC: PROFESSIONAL
    END

Simplified (Fallback C):
    SCORE: 62.0
    TYPE: NUDE
    GAZE: DIRECT
    END
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from amg.config import (
    GENRE_LABELS,
    SENSITIVE_CONTENT_FLAG_LABELS,
    SUBGENRE_LABELS,
    SCORE_MAX,
    normalize_genre_label,
    normalize_position_label,
    normalize_sensitive_content_flag,
    normalize_subgenre_label,
)


@dataclass
class ScoredFrame:
    """Parsed AI scoring result."""
    score: float = 0.0
    tier_a_pass: bool = False
    tier_a_fail_code: Optional[str] = None  # DB1/DB2/DB3/DB4 if failed

    tier_b_present: List[str] = field(default_factory=list)
    tier_c_present: List[str] = field(default_factory=list)
    tier_d_present: List[str] = field(default_factory=list)

    type_: str = "UNKNOWN"          # NUDE/SEX_ACT/PENETRATION/BUILDUP/FINISH/COMPOSITION
    gaze: str = "UNKNOWN"           # SINGLE/DUAL/TRIPLE/AVERTED/CLOSED/REAR (v11.1)
    aesthetic: str = "STANDARD"     # PROFESSIONAL/STANDARD/AMATEUR
    penetration_visible: bool = False
    penetration_confidence: float = 0.0
    action_evidence: str = "NONE"
    position_label: str = "OTHER"
    position_confidence: float = 0.0
    genre_tags: List[str] = field(default_factory=list)
    subgenre_tags: List[str] = field(default_factory=list)
    sensitive_content_flags: List[str] = field(default_factory=list)
    sensitive_content_confidence: float = 0.0

    parse_succeeded: bool = False
    model_score_raw: Optional[float] = None
    raw_response: str = ""


# Regex patterns for field extraction
# Use word-boundary anchors to avoid partial matches
_RE_TIER_A_FAIL = re.compile(r'TIER_A_FAIL:\s*(DB\d)', re.IGNORECASE)
_RE_TIER_A_PASS = re.compile(r'TIER_A_PASS:\s*(yes|no|true|false)', re.IGNORECASE)
_RE_TIER_B = re.compile(r'TIER_B_PRESENT:\s*([B0-9,\s]*)', re.IGNORECASE)
_RE_TIER_C = re.compile(r'TIER_C_PRESENT:\s*([C0-9,\s]*)', re.IGNORECASE)
_RE_TIER_D = re.compile(r'TIER_D_PRESENT:\s*([D0-9,\s]*)', re.IGNORECASE)
_RE_SCORE = re.compile(r'SCORE:\s*(-?\d+\.?\d*)', re.IGNORECASE)
_RE_TYPE = re.compile(r'TYPE:\s*([A-Z_]+)', re.IGNORECASE)
_RE_GAZE = re.compile(r'GAZE:\s*([A-Z]+)', re.IGNORECASE)
_RE_AESTHETIC = re.compile(r'AESTHETIC:\s*([A-Z]+)', re.IGNORECASE)
_RE_PEN_VISIBLE = re.compile(r'PENETRATION_VISIBLE:\s*(yes|no|true|false)', re.IGNORECASE)
_RE_PEN_CONF = re.compile(r'PENETRATION_CONFIDENCE:\s*(-?\d+\.?\d*)', re.IGNORECASE)
_RE_ACTION_EVIDENCE = re.compile(r'ACTION_EVIDENCE:\s*([A-Z0-9_,\- ]+)', re.IGNORECASE)
_RE_POSITION = re.compile(r'POSITION:\s*([A-Z0-9_\- ]+)', re.IGNORECASE)
_RE_POS_CONF = re.compile(r'POSITION_CONFIDENCE:\s*(-?\d+\.?\d*)', re.IGNORECASE)
_RE_GENRES = re.compile(r'^\s*GENRES:\s*([^\r\n]+)', re.IGNORECASE | re.MULTILINE)
_RE_SUBGENRES = re.compile(r'^\s*SUBGENRES:\s*([^\r\n]+)', re.IGNORECASE | re.MULTILINE)
_RE_CONTENT_FLAGS = re.compile(r'^\s*CONTENT_FLAGS:\s*([^\r\n]+)', re.IGNORECASE | re.MULTILINE)
_RE_CONTENT_FLAG_CONF = re.compile(r'CONTENT_FLAG_CONFIDENCE:\s*(-?\d+\.?\d*)', re.IGNORECASE)

_RETAIL_BASE = 34.0
_TIER_B_WEIGHTS: Dict[str, float] = {
    "B1": 10.0,
    "B2": 6.0,
    "B3": 16.0,
    "B4": 10.0,
    "B5": 6.0,
    "B6": 10.0,
    "B7": 16.0,
    "B8": 7.0,
    "B9": 12.0,   # Oral close-up (mouth contact + face clarity)
    "B10": 10.0,  # Dual lens-aware composition
    "B11": 8.0,   # Aggressive / intense action beat
    "B12": 12.0,  # Climax anticipation/release cue
    "B13": 14.0,  # Bodily fluid prominently visible
}
_TIER_C_WEIGHTS: Dict[str, float] = {
    "C1": 5.0,
    "C2": 5.0,
    "C3": 3.0,
    "C4": 4.0,   # Strong close-up framing quality
    "C5": 4.0,   # Retail readability at thumbnail size
}
_TIER_D_PENALTIES: Dict[str, float] = {
    "D1": 8.0,   # Mild blur / motion softness
    "D2": 6.0,   # Awkward crop / cut-off subject
    "D3": 7.0,   # Face occlusion harms cover value
    "D4": 5.0,   # Distracting clutter / background noise
    "D5": 6.0,   # Ambiguous action read despite nudity
}


def parse_ai_response(raw_text: str) -> ScoredFrame:
    """
    Parse raw AI response text into structured ScoredFrame.

    Handles graceful degradation: if AI returns malformed output,
    extract what we can. Mark parse_succeeded=False if score missing.
    """
    result = ScoredFrame(raw_response=raw_text)

    if not raw_text or not raw_text.strip():
        return result

    # Tier A fail check (short-circuits)
    fail_match = _RE_TIER_A_FAIL.search(raw_text)
    if fail_match:
        result.tier_a_pass = False
        result.tier_a_fail_code = fail_match.group(1).upper()
        result.score = 0.0
        result.parse_succeeded = True
        return result

    # Tier A pass check
    pass_match = _RE_TIER_A_PASS.search(raw_text)
    if pass_match:
        val = pass_match.group(1).lower()
        result.tier_a_pass = val in ("yes", "true")

    # Model-declared score (kept for diagnostics; may be overridden by deterministic recompute)
    score_match = _RE_SCORE.search(raw_text)
    if score_match:
        try:
            score = float(score_match.group(1))
            result.model_score_raw = max(0.0, min(SCORE_MAX, score))
            result.score = result.model_score_raw
            result.parse_succeeded = True
        except ValueError:
            pass

    # Tier B present
    b_match = _RE_TIER_B.search(raw_text)
    if b_match:
        codes = b_match.group(1).strip()
        if codes:
            result.tier_b_present = _split_codes(codes)

    # Tier C present
    c_match = _RE_TIER_C.search(raw_text)
    if c_match:
        codes = c_match.group(1).strip()
        if codes:
            result.tier_c_present = _split_codes(codes)

    d_match = _RE_TIER_D.search(raw_text)
    if d_match:
        codes = d_match.group(1).strip()
        if codes:
            result.tier_d_present = _split_codes(codes)

    # Type
    type_match = _RE_TYPE.search(raw_text)
    if type_match:
        result.type_ = type_match.group(1).upper()

    # Gaze
    gaze_match = _RE_GAZE.search(raw_text)
    if gaze_match:
        result.gaze = gaze_match.group(1).upper()

    # Aesthetic
    aesthetic_match = _RE_AESTHETIC.search(raw_text)
    if aesthetic_match:
        result.aesthetic = aesthetic_match.group(1).upper()

    # Penetration visibility
    pen_visible_match = _RE_PEN_VISIBLE.search(raw_text)
    if pen_visible_match:
        v = pen_visible_match.group(1).lower()
        result.penetration_visible = v in ("yes", "true")

    pen_conf_match = _RE_PEN_CONF.search(raw_text)
    if pen_conf_match:
        try:
            conf = float(pen_conf_match.group(1))
            result.penetration_confidence = max(0.0, min(1.0, conf))
        except ValueError:
            pass

    evidence_match = _RE_ACTION_EVIDENCE.search(raw_text)
    if evidence_match:
        result.action_evidence = evidence_match.group(1).strip().upper()

    position_match = _RE_POSITION.search(raw_text)
    if position_match:
        result.position_label = normalize_position_label(position_match.group(1))

    pos_conf_match = _RE_POS_CONF.search(raw_text)
    if pos_conf_match:
        try:
            conf = float(pos_conf_match.group(1))
            result.position_confidence = max(0.0, min(1.0, conf))
        except ValueError:
            pass

    genre_match = _RE_GENRES.search(raw_text)
    if genre_match:
        result.genre_tags = _parse_taxonomy_tags(
            genre_match.group(1),
            normalize_genre_label,
            set(GENRE_LABELS),
        )

    subgenre_match = _RE_SUBGENRES.search(raw_text)
    if subgenre_match:
        result.subgenre_tags = _parse_taxonomy_tags(
            subgenre_match.group(1),
            normalize_subgenre_label,
            set(SUBGENRE_LABELS),
        )

    content_flags_match = _RE_CONTENT_FLAGS.search(raw_text)
    if content_flags_match:
        result.sensitive_content_flags = _parse_taxonomy_tags(
            content_flags_match.group(1),
            normalize_sensitive_content_flag,
            set(SENSITIVE_CONTENT_FLAG_LABELS),
        )

    content_conf_match = _RE_CONTENT_FLAG_CONF.search(raw_text)
    if content_conf_match:
        try:
            conf = float(content_conf_match.group(1))
            result.sensitive_content_confidence = max(0.0, min(1.0, conf))
        except ValueError:
            pass

    # Backward compatibility: older prompts may emit TYPE=PENETRATION without
    # the explicit penetration fields. In that case, infer visible=true with
    # low confidence so downstream gates still have a signal.
    if result.type_ == "PENETRATION" and pen_visible_match is None:
        result.penetration_visible = True
        if result.penetration_confidence == 0.0:
            result.penetration_confidence = 0.51
        if result.action_evidence == "NONE":
            result.action_evidence = "EXPLICIT_PENETRATION"

    # Deterministic recompute from explicit criteria (preferred path).
    deterministic = _compute_deterministic_score(result)
    if deterministic is not None:
        result.score = deterministic
        result.parse_succeeded = True

    # Final inference: if we got score but no tier_a_pass marker,
    # assume pass (score > 0 implies passed Tier A)
    if result.parse_succeeded and result.score > 0 and not result.tier_a_fail_code:
        result.tier_a_pass = True

    return result


def _split_codes(s: str) -> List[str]:
    """Parse 'B1,B3,B6' style strings into list."""
    parts = re.split(r'[,\s]+', s.strip())
    return [p.upper() for p in parts if p.strip()]


def _parse_taxonomy_tags(raw: str, normalizer, allowed: set[str]) -> List[str]:
    if not raw:
        return []
    if raw.strip().upper() in {"NONE", "NA", "N/A", "UNKNOWN"}:
        return []
    out: List[str] = []
    for part in re.split(r'[,;/]+', raw):
        label = normalizer(part)
        if label and label in allowed and label not in out:
            out.append(label)
    return out


def _compute_deterministic_score(result: ScoredFrame) -> Optional[float]:
    """
    Compute final score from criteria codes.

    This avoids score compression by making ranking numeric from explicit
    evidence codes rather than trusting a single free-form SCORE value.
    """
    if result.tier_a_fail_code:
        return 0.0
    if not result.tier_a_pass:
        return None

    has_criteria = bool(result.tier_b_present or result.tier_c_present or result.tier_d_present)
    if not has_criteria:
        return None

    b_total = sum(_TIER_B_WEIGHTS.get(code, 0.0) for code in set(result.tier_b_present))
    c_total = sum(_TIER_C_WEIGHTS.get(code, 0.0) for code in set(result.tier_c_present))
    d_total = sum(_TIER_D_PENALTIES.get(code, 0.0) for code in set(result.tier_d_present))
    raw = _RETAIL_BASE + b_total + c_total - d_total
    capped = cap_score_for_excellence(result, raw)
    return max(0.0, min(SCORE_MAX, capped))


def cap_score_for_excellence(result: ScoredFrame, proposed_score: float) -> float:
    """
    Keep 100 as a rare art-tier outcome.

    We intentionally cap most frames below elite ranges even when stacked
    criteria are present. This stops "good" from crowding into 99-100.
    """
    score = float(proposed_score or 0.0)
    b_codes = set(result.tier_b_present or [])
    c_codes = set(result.tier_c_present or [])
    d_codes = set(result.tier_d_present or [])
    evidence = (result.action_evidence or "NONE").upper()
    model_raw = float(result.model_score_raw or 0.0)

    no_penalties = len(d_codes) == 0
    strong_action = evidence in {"EXPLICIT_PENETRATION", "ORAL_CONTACT"}
    key_moment = bool({"B7", "B9", "B12", "B13"} & b_codes)
    core_presence = "B1" in b_codes and "B6" in b_codes
    rich_b = len(b_codes) >= 6
    rich_c = len(c_codes) >= 4 and {"C1", "C2", "C4", "C5"}.issubset(c_codes)
    high_pen_conf = (not result.penetration_visible) or (float(result.penetration_confidence or 0.0) >= 0.90)

    art_tier = all([no_penalties, strong_action, key_moment, core_presence, rich_b, rich_c, high_pen_conf])
    elite_tier = all([no_penalties, strong_action, key_moment, core_presence, len(b_codes) >= 5, len(c_codes) >= 3, high_pen_conf])

    if art_tier and model_raw >= 98.0 and score >= 99.0:
        return min(100.0, score)
    if elite_tier:
        return min(98.8, score)
    return min(96.0, score)
