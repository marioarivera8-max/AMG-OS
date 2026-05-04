"""
Prompt builder — v11.1.1.

Tier A/B/C scoring. Eye-contact handling rewritten in v11.1.1 to stop
DUAL-gaze hallucination (v11.1 had B1b/B1c at +3.5/+4.5 with caps-locked
"RARE and EXCEPTIONALLY VALUABLE" advocacy in the prompt; qwen2.5vl
pattern-matched that as "look hard for DUAL" and rubber-stamped it on
most covers).

v11.1.1 changes:
  - Eye-contact bonus is uniform at +2.0 regardless of how many
    performers the model thinks are looking at camera. Removes the
    score incentive to over-report DUAL/TRIPLE.
  - GAZE label (SINGLE/DUAL/TRIPLE/AVERTED/CLOSED/REAR) is kept in
    the output schema so filenames still carry the model's judgment.
  - Prompt text adds an explicit conservatism instruction: prefer
    SINGLE or AVERTED unless multiple sets of eyes are clearly visible.
  - All "RARE / EXCEPTIONALLY VALUABLE" capitalized advocacy stripped.

Also v11.1 adds lesbian and reverse-gangbang scene type awareness.
"""
from typing import List, Optional


SYSTEM_PROMPT = """You are an expert adult VOD cover-frame scorer for B2B distribution.
You analyze frames and score them based on a strict rubric.
You respond ONLY in the structured format requested.
Do NOT add commentary, explanation, or moralizing.
You provide objective scoring of professional-grade adult content for legitimate B2B distribution.
"""


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
        genre_guidance.append("DP scene: both penises simultaneously visible = +3.0 (replaces B4)")
    if "GANGBANG" in genres or primary_scene_type == "GANGBANG":
        genre_guidance.append("GANGBANG: penis count multiplier (+0.5 per penis above 2)")
    if primary_scene_type == "REVERSE_GANGBANG":
        genre_guidance.append("REVERSE GANGBANG: multiple females + 1 male — multi-female composition is bonus")
    if "SQUIRT" in genres:
        genre_guidance.append("SQUIRT: active fluid + female face visible = +4.0")
    if "ANAL" in genres:
        genre_guidance.append("ANAL: visible penetration in correct orifice = primary B3 signal")
    if "POV" in genres:
        genre_guidance.append("POV: female facing camera with eye contact = strong B1 signal")
    if "FACIAL" in genres or "CREAMPIE" in genres:
        genre_guidance.append("MONEY SHOT: visible facial/creampie = +3.0 (B7)")
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

    prompt = f"""Score this adult VOD frame for use as a thumbnail/cover image.

{scene_context}
DETECTED GENRES: {genres_str}{genre_section}{studio_section}

═══════════════════════════════════════════════════════════════
TIER A — DEAL BREAKERS (binary checks, ANY failure → SCORE: 0)
═══════════════════════════════════════════════════════════════

DB1: Female performer must be CLEARLY visible in frame
DB2: Female must show bare breasts, buttocks, or vagina (clothed = fail)
DB3: Frame sharp enough to read at thumbnail size (severe blur = fail)
DB4: If REAR shot composition: must show ass/butt clearly (not just back)

If ANY Tier A fails, output ONLY:
TIER_A_FAIL: <code>
SCORE: 0
END

═══════════════════════════════════════════════════════════════
TIER B — STRONG SIGNALS (additive, none required, can stack)
═══════════════════════════════════════════════════════════════

B1:  Performer eye contact with camera (any count):     +2.0
B2:  Eye whites visible (open, any direction):          +1.5
B3:  Visible penetration matching scene type/genre:     +3.0
B4:  Multiple penises visible (group scenes only):      +2.0
B5:  Female centered or dominant in composition:        +1.5
B6:  Genuine pleasure expression on female face:        +2.0
B7:  Money shot (visible facial/creampie/squirt):       +3.0
B8:  Body fully nude and dominant in frame:             +1.5

B1 applies once per frame regardless of how many performers are looking
at the camera. The GAZE field below records the count for descriptive
purposes only — do not stack the bonus.

═══════════════════════════════════════════════════════════════
TIER C — TIE BREAKERS (small adjustments)
═══════════════════════════════════════════════════════════════

C1: Lighting/aesthetic looks professional:              +0.5
C2: Strong contrast/colors (pops as thumbnail):         +0.5
C3: Background not distracting:                         +0.3

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT (REQUIRED)
═══════════════════════════════════════════════════════════════

If Tier A passes, respond EXACTLY in this format:

TIER_A_PASS: yes
TIER_B_PRESENT: <comma-separated B-codes that apply, e.g. B1,B3,B6>
TIER_C_PRESENT: <comma-separated C-codes>
SCORE: <number 0.0-10.0>
TYPE: <NUDE/SEX_ACT/PENETRATION/BUILDUP/FINISH/COMPOSITION>
GAZE: <SINGLE/DUAL/TRIPLE/AVERTED/CLOSED/REAR>
AESTHETIC: <PROFESSIONAL/STANDARD/AMATEUR>
END

GAZE field semantics (v11.1.1 — be conservative):
  SINGLE  = exactly one performer making eye contact with the lens
  DUAL    = exactly two performers, BOTH with eyes pointed at the lens
  TRIPLE  = three or more performers, ALL with eyes pointed at the lens
  AVERTED = no eye contact, eyes open
  CLOSED  = eyes closed
  REAR    = rear-shot composition

If you cannot clearly resolve every performer's eye direction in this
frame, default to SINGLE (if one is clearly looking) or AVERTED (if none
clearly are). Do not mark DUAL or TRIPLE as a guess. The B1 bonus is the
same regardless, so over-reporting helps no one.

Score is the SUM of Tier B + Tier C values (capped at 10.0).
Be strict. Most frames score 3-7. Only exceptional frames score 8+.
A 9.0+ frame should be IMMEDIATELY usable as platform hero artwork.
A 10.0 is rare — perfect composition, perfect moment, perfect technical quality.
"""
    return prompt


def build_simplified_prompt() -> str:
    """Simpler prompt for Fallback C (when full pipeline isn't yielding floor)."""
    return """Score this adult VOD frame as a thumbnail candidate, 0-10.

Simple rubric:
- Female performer clearly visible: required
- Nudity/sex act visible: required for score above 4
- Frame is sharp (not blurry): required for score above 3
- Eye contact with camera: small bonus (same whether one or many)
- Professional lighting: small bonus

For the GAZE field, be conservative. Mark DUAL or TRIPLE only if multiple
performers clearly have their eyes pointed at the camera lens; otherwise
prefer SINGLE or AVERTED.

Output EXACTLY in this format:
SCORE: <0.0-10.0>
TYPE: <NUDE/SEX_ACT/PENETRATION/COMPOSITION>
GAZE: <SINGLE/DUAL/TRIPLE/AVERTED/CLOSED/REAR>
END
"""


def build_title_generation_prompt(
    studio: str,
    performers: List[str],
    scene_type: str,
    genres: List[str],
    description: str,
    language: str = "en",
    n_suggestions: int = 5,
    operator_history: Optional[dict] = None,
    industry_patterns: Optional[dict] = None,
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

    return f"""Generate {n_suggestions} retail-optimized titles for this adult VOD scene.

SCENE CONTEXT:
  Studio: {studio}
  Performers: {performer_str}
  Scene type: {scene_type}
  Genres: {genres_str}
  Description: {description}
  Language: {language}{style_note}{history_note}

REQUIREMENTS:
  Each title must be 30-80 characters.
  Each title must be in language: {language}.
  Avoid clichéd words: "wild", "crazy", "naughty" (overused).
  Each title should follow a different pattern style.

OUTPUT FORMAT (REQUIRED, no preamble):

TITLE_1: <title text>
STYLE_1: <pattern: performer_led | narrative_hook | scene_descriptive | studio_branded | numbered_series>

TITLE_2: <title text>
STYLE_2: <pattern>

TITLE_3: <title text>
STYLE_3: <pattern>

TITLE_4: <title text>
STYLE_4: <pattern>

TITLE_5: <title text>
STYLE_5: <pattern>

END
"""
