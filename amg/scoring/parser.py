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
    SCORE: 7.5
    TYPE: SEX_ACT
    GAZE: DIRECT
    AESTHETIC: PROFESSIONAL
    END

Simplified (Fallback C):
    SCORE: 6.0
    TYPE: NUDE
    GAZE: DIRECT
    END
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional

from amg.config import SCORE_MAX


@dataclass
class ScoredFrame:
    """Parsed AI scoring result."""
    score: float = 0.0
    tier_a_pass: bool = False
    tier_a_fail_code: Optional[str] = None  # DB1/DB2/DB3/DB4 if failed

    tier_b_present: List[str] = field(default_factory=list)
    tier_c_present: List[str] = field(default_factory=list)

    type_: str = "UNKNOWN"          # NUDE/SEX_ACT/PENETRATION/BUILDUP/FINISH/COMPOSITION
    gaze: str = "UNKNOWN"           # SINGLE/DUAL/TRIPLE/AVERTED/CLOSED/REAR (v11.1)
    aesthetic: str = "STANDARD"     # PROFESSIONAL/STANDARD/AMATEUR

    parse_succeeded: bool = False
    raw_response: str = ""


# Regex patterns for field extraction
# Use word-boundary anchors to avoid partial matches
_RE_TIER_A_FAIL = re.compile(r'TIER_A_FAIL:\s*(DB\d)', re.IGNORECASE)
_RE_TIER_A_PASS = re.compile(r'TIER_A_PASS:\s*(yes|no|true|false)', re.IGNORECASE)
_RE_TIER_B = re.compile(r'TIER_B_PRESENT:\s*([B0-9,\s]*)', re.IGNORECASE)
_RE_TIER_C = re.compile(r'TIER_C_PRESENT:\s*([C0-9,\s]*)', re.IGNORECASE)
_RE_SCORE = re.compile(r'SCORE:\s*(-?\d+\.?\d*)', re.IGNORECASE)
_RE_TYPE = re.compile(r'TYPE:\s*([A-Z_]+)', re.IGNORECASE)
_RE_GAZE = re.compile(r'GAZE:\s*([A-Z]+)', re.IGNORECASE)
_RE_AESTHETIC = re.compile(r'AESTHETIC:\s*([A-Z]+)', re.IGNORECASE)


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

    # Score (most important — required for parse_succeeded)
    score_match = _RE_SCORE.search(raw_text)
    if score_match:
        try:
            score = float(score_match.group(1))
            result.score = max(0.0, min(SCORE_MAX, score))
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

    # Final inference: if we got score but no tier_a_pass marker,
    # assume pass (score > 0 implies passed Tier A)
    if result.parse_succeeded and result.score > 0 and not result.tier_a_fail_code:
        result.tier_a_pass = True

    return result


def _split_codes(s: str) -> List[str]:
    """Parse 'B1,B3,B6' style strings into list."""
    parts = re.split(r'[,\s]+', s.strip())
    return [p.upper() for p in parts if p.strip()]
