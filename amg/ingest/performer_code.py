"""
Performer code parser — v11.1.

Scenes use codes like:
  "27 BBGG - couple swap"           → mixed foursome
  "12 GG - lesbian scene"           ← v11.1 fix
  "8 GGG - lesbian threesome"       ← v11.1 fix
  "1 G - solo female"               ← v11.1 fix (v11.0 didn't accept single chars)
  "3 BB - gay scene"                ← v11.1 fix (flagged)

B = boy (male), G = girl (female).

v11.0 BUG FIXED:
  - GG was treated as generic COUPLE → now LESBIAN
  - GGG was treated as generic THREESOME → now LESBIAN_THREESOME
  - All-male codes (BB, BBB) are now flagged for review
  - Single-char codes (G, B) now accepted
"""
import re
from pathlib import Path
from typing import Optional, Tuple

# v11.1: Pattern accepts 1-12 chars (was 2-12)
PERFORMER_CODE_PATTERN = re.compile(r'^\s*\d+\s+([BG]{1,12})\s+-\s')


def parse_performer_code(video_path: Path) -> Optional[dict]:
    """
    Extract performer code from filename or parent folder name.

    Returns dict with full classification or None if no code found.
    """
    candidate_strings = [video_path.parent.name, video_path.stem]

    for s in candidate_strings:
        match = PERFORMER_CODE_PATTERN.match(s)
        if match:
            code = match.group(1)
            return _classify_code(code)

    return None


def _classify_code(code: str) -> dict:
    """
    Classify a performer code (e.g., 'BBGG', 'GG', 'BBBBG').

    v11.1 fix: properly distinguishes all-G (lesbian), all-B (gay, flagged),
    mixed (standard), and solo cases.
    """
    male_count = code.count('B')
    female_count = code.count('G')
    total = male_count + female_count

    is_all_female = male_count == 0 and female_count >= 1
    is_all_male = female_count == 0 and male_count >= 1
    is_mixed = male_count >= 1 and female_count >= 1

    is_solo_female = is_all_female and total == 1
    is_solo_male = is_all_male and total == 1
    is_lesbian = is_all_female and total >= 2
    is_gay = is_all_male and total >= 2

    is_couple = is_mixed and total == 2
    is_threesome = is_mixed and total == 3
    is_foursome = is_mixed and total == 4
    is_gangbang = (total >= 5 and is_mixed) or (male_count >= 4 and is_mixed)

    flag_for_review = is_all_male and total >= 1

    return {
        'code': code,
        'male_count': male_count,
        'female_count': female_count,
        'total': total,
        'is_solo_female': is_solo_female,
        'is_solo_male': is_solo_male,
        'is_lesbian': is_lesbian,
        'is_gay': is_gay,
        'is_couple': is_couple,
        'is_threesome': is_threesome,
        'is_foursome': is_foursome,
        'is_gangbang': is_gangbang,
        'is_mixed': is_mixed,
        'flag_for_review': flag_for_review,
        'flag_reason': 'ALL_MALE_CONTENT' if flag_for_review else None,
    }


def get_authoritative_performer_count(
    video_path: Path,
    fallback_count: int = 0
) -> Tuple[int, str]:
    """Get most authoritative performer count. Code wins over face detection."""
    code_info = parse_performer_code(video_path)
    if code_info:
        return code_info['total'], 'filename_code'
    return fallback_count, 'face_detection'


def detect_scene_type_from_code(code_info: dict) -> str:
    """
    Map performer code to scene type.

    v11.1 returns:
        SOLO_FEMALE, SOLO_MALE
        LESBIAN, LESBIAN_THREESOME, LESBIAN_GROUP
        GAY, GAY_GROUP
        COUPLE
        THREESOME, THREESOME_FFM, THREESOME_MMF
        FOURSOME
        GANGBANG, REVERSE_GANGBANG
        STANDARD
    """
    if code_info is None:
        return "STANDARD"

    if code_info['is_solo_female']:
        return "SOLO_FEMALE"
    if code_info['is_solo_male']:
        return "SOLO_MALE"

    if code_info['is_lesbian']:
        if code_info['total'] == 2:
            return "LESBIAN"
        if code_info['total'] == 3:
            return "LESBIAN_THREESOME"
        return "LESBIAN_GROUP"

    if code_info['is_gay']:
        if code_info['total'] == 2:
            return "GAY"
        return "GAY_GROUP"

    if code_info['is_couple']:
        return "COUPLE"

    if code_info['is_threesome']:
        if code_info['female_count'] == 2:
            return "THREESOME_FFM"
        if code_info['male_count'] == 2:
            return "THREESOME_MMF"
        return "THREESOME"

    if code_info['is_foursome']:
        if code_info['female_count'] == 3:
            return "REVERSE_GANGBANG"
        if code_info['male_count'] == 3:
            return "GANGBANG"
        return "FOURSOME"

    if code_info['is_gangbang']:
        if code_info['female_count'] > code_info['male_count']:
            return "REVERSE_GANGBANG"
        return "GANGBANG"

    return "STANDARD"
