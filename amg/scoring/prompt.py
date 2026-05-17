"""
Prompt builder — v11.1.1 eye semantics + v11.1.5 score scale.

Tier A/B/C scoring on a 0–100 scale (v11.1.5): base + additive signals,
capped at 100, with calibration text to reduce score compression in the
80s. Eye-contact handling remains v11.1.1 (uniform B1 weight; conservative
DUAL/TRIPLE; GAZE label for filenames only).
"""
from typing import Any, List, Optional
from amg.prompts.loader import load_prompt_registry
from amg.scoring.market_profile import build_market_profile_note


SYSTEM_PROMPT = load_prompt_registry("scoring_system")
_SCORING_FRAME_TEMPLATE = load_prompt_registry("scoring_frame")
_SCORING_FALLBACK_SIMPLE_TEMPLATE = load_prompt_registry("scoring_fallback_simple")
_SCORING_SOFT_TEMPLATE = load_prompt_registry("scoring_soft_thumbnail")
_SCENE_INSIGHT_TEMPLATE = load_prompt_registry("scene_insight")
_TITLE_ENRICHED_TEMPLATE = load_prompt_registry("title_enriched")
_TITLE_GENERATION_TEMPLATE = load_prompt_registry("title_generation")


def build_scoring_prompt(
    primary_scene_type: str = "STANDARD",
    detected_genres: Optional[List[str]] = None,
    performer_count: Optional[int] = None,
    studio_language: str = "en",
    studio_hint: Optional[str] = None,
) -> str:
    """
    Build the v11.1 scoring prompt with eye contact tiering.
    """
    genres = detected_genres or []
    genres_str = ", ".join(genres) if genres else "none detected"

    # Genre-specific guidance
    genre_guidance = []
    if "DP" in genres:
        genre_guidance.append(
            "DP scene: both penises simultaneously visible — award B4 points "
            "(do not stack a generic multi-penis B4 twice; one B4 application only)."
        )
    if "GANGBANG" in genres or primary_scene_type == "GANGBANG":
        genre_guidance.append("GANGBANG: add +5 to SCORE per penis clearly visible above 2 (cap total SCORE at 100)")
    if primary_scene_type == "REVERSE_GANGBANG":
        genre_guidance.append("REVERSE GANGBANG: multiple females + 1 male — multi-female composition is bonus")
    if "SQUIRT" in genres:
        genre_guidance.append(
            "SQUIRT: active fluid + female face visible — award B7 when clearly present"
        )
    if "ANAL" in genres:
        genre_guidance.append("ANAL: visible penetration in correct orifice = primary B3 signal")
    if "POV" in genres:
        genre_guidance.append("POV: female facing camera with eye contact = strong B1 signal")
    if "FACIAL" in genres or "CREAMPIE" in genres:
        genre_guidance.append("MONEY SHOT genre: visible facial/creampie must earn B7 when applicable")
    if primary_scene_type in ("LESBIAN", "LESBIAN_THREESOME", "LESBIAN_GROUP"):
        genre_guidance.append(
            "LESBIAN: multiple females, no male performers expected. "
            "Female-female interaction is the focus (kissing, oral, body contact)."
        )
    if primary_scene_type == "SOLO_FEMALE":
        genre_guidance.append(
            "SOLO: single female. Focus on body composition, expression, lighting."
        )

    genre_section = ""
    if genre_guidance:
        genre_section = "\n\nGENRE-SPECIFIC SCORING:\n" + "\n".join(f"- {g}" for g in genre_guidance)

    studio_section = ""
    if studio_hint:
        studio_section = f"\n\nSTUDIO CONTEXT: {studio_hint}"
    if studio_language != "en":
        studio_section += f"\nNote: Studio operates in {studio_language} market."

    scene_context = ""
    if primary_scene_type != "STANDARD":
        scene_context = f"\nSCENE TYPE: {primary_scene_type}"
        if performer_count:
            scene_context += f" ({performer_count} performers expected)"

    prompt = _SCORING_FRAME_TEMPLATE.format(
        scene_context=scene_context,
        genres_str=genres_str,
        genre_section=genre_section,
        studio_section=studio_section,
    )
    return prompt


def build_simplified_prompt() -> str:
    """Simpler prompt for Fallback C (when full pipeline isn't yielding floor)."""
    return _SCORING_FALLBACK_SIMPLE_TEMPLATE


def build_soft_thumbnail_prompt() -> str:
    """Prompt for non-nude / soft thumbnail selection."""
    return _SCORING_SOFT_TEMPLATE


def build_scene_insight_prompt() -> str:
    """Vision prompt — describe a single contact-sheet image factually.

    Asked once per scene against the contact sheet (or top cover) so we get
    coarse setting/mood/feature data without re-scoring every frame.
    """
    return _SCENE_INSIGHT_TEMPLATE


def build_enriched_title_prompt(
    studio: str,
    performers: List[str],
    scene_type: str,
    genres: List[str],
    description: str,
    insight: dict[str, Any],
    position_summary: dict[str, Any],
    seed_taxonomy: Optional[dict[str, Any]] = None,
    title_tone: str = "edgy",
    language: str = "en",
    n_suggestions: int = 5,
) -> str:
    """Title-generation prompt enriched with vision insight + position rollup.

    Produces both N retail title suggestions AND a long-form scene description
    suitable for a VOD store listing. Text-only call (no image).
    """
    performer_str = ", ".join(performers) if performers else "(unknown)"
    genres_str = ", ".join(genres) if genres else "(none)"
    pos_str = ", ".join(f"{k}={v}" for k, v in (position_summary or {}).items()) or "(none)"
    setting = (insight or {}).get("setting") or "(unknown)"
    location_hint = (insight or {}).get("location_hint") or "(none)"
    features = (insight or {}).get("notable_features") or []
    features_str = ", ".join(features) if features else "(none)"
    action = (insight or {}).get("action_summary") or "(none)"
    mood = (insight or {}).get("mood") or "(none)"
    market_note = build_market_profile_note()
    seed_categories = ", ".join((seed_taxonomy or {}).get("categories") or []) or "(none)"
    seed_tags = ", ".join((seed_taxonomy or {}).get("tags") or []) or "(none)"
    tone = (title_tone or "edgy").strip().lower()
    if tone == "retail_safe":
        tone_note = (
            "TONE PROFILE: Retail Safe — punchy but conservative. Avoid taboo/incest phrasing, "
            "avoid extreme aggression language, keep wording commercially clean."
        )
    elif tone == "premium_story":
        tone_note = (
            "TONE PROFILE: Premium Story — cinematic, sensual, upscale phrasing with clear action terms."
        )
    elif tone == "creative":
        tone_note = (
            "TONE PROFILE: Creative — vivid, fresh, imaginative phrasing with high specificity. "
            "Use unusual but still searchable hooks grounded in real scene details. "
            "Avoid generic title templates and avoid fabricated facts."
        )
    else:
        tone = "edgy"
        tone_note = (
            "TONE PROFILE: Edgy — energetic, explicit, high-conversion retail voice while still factual "
            "to what is visible in-scene."
        )

    return _TITLE_ENRICHED_TEMPLATE.format(
        studio=studio,
        performer_str=performer_str,
        scene_type=scene_type,
        genres_str=genres_str,
        description=description or "(none provided)",
        setting=setting,
        location_hint=location_hint,
        features_str=features_str,
        action=action,
        mood=mood,
        pos_str=pos_str,
        seed_categories=seed_categories,
        seed_tags=seed_tags,
        tone=tone,
        language=language,
        n_suggestions=n_suggestions,
        market_note=market_note,
        tone_note=tone_note,
    )


def build_title_generation_prompt(
    studio: str,
    performers: List[str],
    scene_type: str,
    genres: List[str],
    description: str,
    language: str = "en",
    n_suggestions: int = 5,
    operator_history: Optional[dict[str, Any]] = None,
    industry_patterns: Optional[dict[str, Any]] = None,
) -> str:
    """
    Build prompt to generate retail-optimized title suggestions.

    v11.1 uses general industry patterns; v11.2 will inject research-driven
    patterns via the industry_patterns argument.
    """
    performer_str = ", ".join(performers) if performers else "performers"
    genres_str = ", ".join(genres) if genres else "none specified"

    style_note = ""
    if industry_patterns:
        # v11.2 hook: when research-driven patterns exist, use them
        style_note = f"\n\nINDUSTRY PATTERNS FROM RESEARCH:\n{industry_patterns}"

    history_note = ""
    if operator_history and operator_history.get("preferred_styles"):
        # v12 hook: when operator preferences are learned
        history_note = f"\n\nOPERATOR PREFERS: {operator_history['preferred_styles']}"

    return _TITLE_GENERATION_TEMPLATE.format(
        n_suggestions=n_suggestions,
        studio=studio,
        performer_str=performer_str,
        scene_type=scene_type,
        genres_str=genres_str,
        description=description,
        language=language,
        style_note=style_note,
        history_note=history_note,
    )
