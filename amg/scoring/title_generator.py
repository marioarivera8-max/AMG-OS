"""
Title generator — v11.1.

Generates 3-5 retail-optimized title suggestions per scene.

v11.1: Uses general adult industry patterns (Option A — quick patterns).
v11.2 hook: Will load research-driven patterns from data/title_patterns/research_patterns.json
v12 hook: Will incorporate operator-specific preferences learned from override history.

Output format (parsed):
[
  {"text": "Yasmina's Wild Night", "style": "performer_led", "char_count": 22},
  {"text": "Two Couples, One Bed", "style": "narrative_hook", "char_count": 22},
  ...
]
"""
import json
import re
from pathlib import Path
from typing import List, Optional, Dict

from amg.config import (
    TITLE_SUGGESTIONS_PER_SCENE,
    TITLE_RESEARCH_PATTERNS_PATH,
    TITLE_QUICK_PATTERNS_PATH,
    PLATFORM_REQUIREMENTS,
)
from amg.scoring.ai_client import AIClient
from amg.scoring.prompt import build_title_generation_prompt
from amg.scoring.market_profile import (
    MARKET_CATEGORY_PRIORITIES,
    MARKET_TAG_PRIORITIES,
    MARKET_TITLE_PATTERNS,
    build_market_profile_note,
)
from amg.utils.logging import get_logger

log = get_logger("scoring.title_generator")


# Quick patterns library (v11.1 — replaced by v11.2 research patterns when available)
QUICK_PATTERNS_GUIDANCE = {
    "performer_led": {
        "description": "Lead with performer name(s)",
        "examples": [
            "Yasmina's Bedroom Adventure",
            "The Tabatha Lust Experience",
        ],
        "good_for": ["solo", "couple", "scenes featuring known talent"],
    },
    "narrative_hook": {
        "description": "Tells a story or asks a question",
        "examples": [
            "Two Couples, One Bedroom",
            "What Happens When Friends Visit",
        ],
        "good_for": ["foursome", "swinger", "narrative scenes"],
    },
    "scene_descriptive": {
        "description": "Describes the scene structure clearly",
        "examples": [
            "Gangbang with Yasmina Khan",
            "Anal Foursome in HD",
        ],
        "good_for": ["search-driven retail (clear genre)"],
    },
    "studio_branded": {
        "description": "Uses studio name as part of title",
        "examples": [
            "Yasmina Brady Productions: Volume 7",
            "The Brady Experience",
        ],
        "good_for": ["catalog-building, series releases"],
    },
    "numbered_series": {
        "description": "Series naming with sequence number",
        "examples": [
            "Couples Weekend Vol. 7",
            "Yasmina Files: Episode 12",
        ],
        "good_for": ["recurring themes, building catalog identity"],
    },
}


def generate_titles(
    studio: str,
    performers: List[str],
    scene_type: str,
    genres: List[str],
    description: str = "",
    language: str = "en",
    n_suggestions: int = TITLE_SUGGESTIONS_PER_SCENE,
    ai_client: Optional[AIClient] = None,
    operator_id: str = "default",
) -> List[dict]:
    """
    Generate title suggestions for a scene.

    Returns list of dicts:
        {
            "text": "Yasmina's Wild Night",
            "style": "performer_led",
            "char_count": 22,
            "platform_fit": {"AEBN": True, "SLR": True, "ADE": True},
            "warnings": [],
        }
    """
    if ai_client is None:
        ai_client = AIClient()

    # Load any available pattern intelligence
    industry_patterns = _load_research_patterns()
    operator_history = _load_operator_history(operator_id)

    # Build the prompt
    prompt = build_title_generation_prompt(
        studio=studio,
        performers=performers,
        scene_type=scene_type,
        genres=genres,
        description=description,
        language=language,
        n_suggestions=n_suggestions,
        operator_history=operator_history,
        industry_patterns=industry_patterns,
    )

    # Send to AI (text-only call, no image)
    response = ai_client.generate_text(prompt)
    if not response.success:
        log.warn("Title generation failed, using fallback templates",
                 error=response.error_code)
        return _fallback_titles(studio, performers, scene_type, genres, n_suggestions)

    # Parse the AI response
    titles = _parse_title_response(response.raw_text)

    # If parsing yielded fewer than expected, pad with fallbacks
    if len(titles) < n_suggestions:
        log.warn("Insufficient titles parsed, padding with fallbacks",
                 parsed=len(titles), expected=n_suggestions)
        fallbacks = _fallback_titles(studio, performers, scene_type, genres,
                                     n_suggestions - len(titles))
        titles.extend(fallbacks)

    # Add platform fit + warnings to each
    for title in titles:
        title["char_count"] = len(title["text"])
        title["platform_fit"] = _check_platform_fit(title["text"])
        title["warnings"] = _check_warnings(title["text"])

    return titles[:n_suggestions]


def _parse_title_response(raw_text: str) -> List[dict]:
    """Parse the AI response into structured title dicts."""
    titles = []
    title_pattern = re.compile(r'TITLE_(\d+):\s*(.+?)(?=\n|$)', re.IGNORECASE)
    style_pattern = re.compile(r'STYLE_(\d+):\s*(\w+)', re.IGNORECASE)

    title_matches = {int(m.group(1)): m.group(2).strip() for m in title_pattern.finditer(raw_text)}
    style_matches = {int(m.group(1)): m.group(2).strip().lower()
                     for m in style_pattern.finditer(raw_text)}

    for idx in sorted(title_matches.keys()):
        text = title_matches[idx]
        # Strip surrounding quotes if AI added them
        text = text.strip('"\'').strip()
        style = style_matches.get(idx, "unknown")
        titles.append({
            "text": text,
            "style": style,
        })

    return titles


def _check_platform_fit(title: str) -> dict:
    """Check if title fits within each platform's character limit."""
    fit = {}
    char_count = len(title)
    for platform, reqs in PLATFORM_REQUIREMENTS.items():
        max_chars = reqs.get("title_max_chars", 100)
        fit[platform] = char_count <= max_chars
    return fit


def _check_warnings(title: str) -> List[str]:
    """Check title for potential issues."""
    warnings = []
    title_lower = title.lower()

    # Check banned terms across all platforms
    for platform, reqs in PLATFORM_REQUIREMENTS.items():
        for term in reqs.get("banned_terms", []):
            if term.lower() in title_lower:
                warnings.append(f"Contains '{term}' (banned on {platform})")

    # Length checks
    if len(title) < 15:
        warnings.append("Very short — may underperform")
    if len(title) > 100:
        warnings.append("Very long — may be truncated on most platforms")

    return warnings


def _fallback_titles(
    studio: str,
    performers: List[str],
    scene_type: str,
    genres: List[str],
    n_suggestions: int,
) -> List[dict]:
    """
    Template-based fallback when AI generation fails.
    Better than nothing — produces valid titles using the data we have.
    """
    primary = performers[0] if performers else "Unknown"
    primary_first = primary.split()[0] if primary else "Unknown"
    type_label = scene_type.replace("_", " ").title()
    genres_str = " ".join(g.title() for g in genres[:2]) if genres else ""

    templates = [
        (f"{primary_first}'s {type_label}", "performer_led"),
        (f"{type_label} with {primary}", "scene_descriptive"),
        (f"The {studio} Experience: {type_label}", "studio_branded"),
        (f"{primary_first} - {genres_str}".strip(" -"), "performer_led"),
        (f"{studio} Presents: {type_label}", "studio_branded"),
    ]

    return [
        {"text": text, "style": style}
        for text, style in templates[:n_suggestions]
    ]


def _load_research_patterns() -> Optional[dict]:
    """
    v11.2 hook: load research-driven title patterns if available.
    Returns None in v11.1 (placeholder).
    """
    if not TITLE_RESEARCH_PATTERNS_PATH.exists():
        # Built-in fallback profile from operator-provided top-grossing examples.
        return {
            "source": "builtin_market_profile",
            "title_patterns": MARKET_TITLE_PATTERNS,
            "category_priorities": MARKET_CATEGORY_PRIORITIES,
            "tag_priorities": MARKET_TAG_PRIORITIES,
            "prompt_note": build_market_profile_note(),
        }
    try:
        with open(TITLE_RESEARCH_PATTERNS_PATH) as f:
            return json.load(f)
    except Exception:
        return None


def _load_operator_history(operator_id: str) -> Optional[dict]:
    """
    v12 hook: load operator-specific title preferences.
    Returns None in v11.1 (placeholder).
    """
    history_path = Path.home() / "AMG_OS" / "data" / "operator_history" / f"{operator_id}.json"
    if not history_path.exists():
        return None
    try:
        with open(history_path) as f:
            return json.load(f)
    except Exception:
        return None
