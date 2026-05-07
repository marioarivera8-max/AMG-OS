"""
Market-profile guidance for title/description/tag generation.

This module encodes recurring patterns from operator-provided top-grossing
examples so prompts can stay consistent and reusable.
"""
from typing import Dict, List


MARKET_TITLE_PATTERNS: List[str] = [
    "Performer-led + action verb + clear scenario",
    "Setting-led hook (hotel room, shower, penthouse, classroom)",
    "POV/angle-led framing when central to scene identity",
    "Climax/finish-led phrase when release cues are explicit",
    "Compilation label only when source is actually compilation",
]

MARKET_CATEGORY_PRIORITIES: List[str] = [
    "Amateur",
    "Verified Models",
    "Verified Amateurs",
    "HD Porn",
    "POV",
    "Blowjob",
    "Deepthroat",
    "Anal",
    "Big Ass",
    "Big Tits",
    "Big Dick",
    "Cumshot",
    "Squirt",
    "Solo Female",
    "Toys",
    "MILF",
    "Compilation",
    "Step Fantasy",
]

MARKET_TAG_PRIORITIES: List[str] = [
    "pov",
    "eye contact",
    "blowjob",
    "deepthroat",
    "big ass",
    "big tits",
    "big cock",
    "doggy style",
    "missionary",
    "cum in mouth",
    "creampie",
    "squirting",
    "spit",
    "rough sex",
    "hard sex",
    "solo dildo",
    "natural tits",
    "shaved pussy",
]

MARKET_TERMS_TO_AVOID: List[str] = [
    "wild",
    "crazy",
    "naughty",
]

# Soft global ranges used by the enriched metadata normalizer.
CATEGORY_COUNT_MIN = 8
CATEGORY_COUNT_MAX = 15
TAG_COUNT_MIN = 15
TAG_COUNT_MAX = 30

# Common synonym cleanup so generated outputs collapse to one canonical token.
CATEGORY_ALIASES: Dict[str, str] = {
    "pov porn": "POV",
    "cumshot": "Cumshot",
    "cream pie": "Cumshot",
    "deep throat": "Deepthroat",
    "solo": "Solo Female",
    "toys": "Toys",
}

TAG_ALIASES: Dict[str, str] = {
    "bj": "blowjob",
    "deep throat": "deepthroat",
    "doggystyle": "doggy style",
    "doggy": "doggy style",
    "cumshot": "cum in mouth",
    "creampie": "creampie",
    "eye-contact": "eye contact",
}


def build_market_profile_note() -> str:
    """Render compact prompt text for generation guidance."""
    patterns = "\n".join(f"- {p}" for p in MARKET_TITLE_PATTERNS)
    categories = ", ".join(MARKET_CATEGORY_PRIORITIES)
    tags = ", ".join(MARKET_TAG_PRIORITIES)
    avoid = ", ".join(MARKET_TERMS_TO_AVOID)
    return (
        "CURRENT TOP-GROSSING STYLE SIGNALS:\n"
        f"{patterns}\n\n"
        f"CATEGORY VOCAB PRIORITY (choose relevant subset): {categories}\n"
        f"TAG VOCAB PRIORITY (lowercase, comma-separated): {tags}\n"
        f"AVOID OVERUSED FILLER TERMS: {avoid}"
    )


def build_seed_taxonomy(genres: List[str], position_summary: Dict[str, int]) -> Dict[str, List[str]]:
    """
    Lightweight deterministic seed taxonomy from known scene metadata.

    Used to anchor AI suggestions toward useful, scene-realistic categories/tags.
    """
    genres_upper = {g.upper() for g in (genres or [])}
    pos_upper = {k.upper() for k in (position_summary or {}).keys()}

    categories: List[str] = ["HD Porn"]
    tags: List[str] = []

    if "POV" in genres_upper:
        categories.append("POV")
        tags.extend(["pov", "eye contact"])
    if "ANAL" in genres_upper:
        categories.append("Anal")
        tags.append("anal")
    if "SQUIRT" in genres_upper:
        categories.append("Squirt")
        tags.append("squirting")
    if "CREAMPIE" in genres_upper or "FACIAL" in genres_upper:
        categories.append("Cumshot")
        tags.extend(["creampie", "cum in mouth"])
    if "SOLO_FEMALE" in genres_upper:
        categories.append("Solo Female")
        tags.append("solo")
    if "TOY" in genres_upper or "TOYS" in genres_upper:
        categories.append("Toys")
        tags.append("solo dildo")
    if "ORAL_BJ" in pos_upper or "BLOWJOB" in genres_upper:
        categories.append("Blowjob")
        tags.extend(["blowjob", "deepthroat"])
    if "DOGGY" in pos_upper:
        tags.append("doggy style")
    if "MISSIONARY" in pos_upper:
        tags.append("missionary")

    # Preserve order while deduplicating.
    categories = list(dict.fromkeys(categories))
    tags = list(dict.fromkeys(tags))
    return {"categories": categories[:10], "tags": tags[:20]}
