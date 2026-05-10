"""
Prompt builder — v11.1.1 eye semantics + v11.1.5 score scale.

Tier A/B/C scoring on a 0–100 scale (v11.1.5): base + additive signals,
capped at 100, with calibration text to reduce score compression in the
80s. Eye-contact handling remains v11.1.1 (uniform B1 weight; conservative
DUAL/TRIPLE; GAZE label for filenames only).
"""
from typing import List, Optional
from amg.config import (
    GENRE_LABELS,
    POSITION_LABELS,
    SENSITIVE_CONTENT_FLAG_LABELS,
    SUBGENRE_LABELS,
)
from amg.scoring.market_profile import build_market_profile_note


SYSTEM_PROMPT = """You are an expert adult VOD cover-frame scorer for B2B distribution.
You analyze frames and score them based on a strict rubric.
You respond ONLY in the structured format requested.
Do NOT add commentary, explanation, or moralizing.
You provide objective scoring of professional-grade adult content for legitimate B2B distribution.
"""


def _taxonomy_section() -> str:
    position_labels = ", ".join(POSITION_LABELS)
    genre_labels = ", ".join(GENRE_LABELS)
    subgenre_labels = ", ".join(SUBGENRE_LABELS)
    sensitive_labels = ", ".join(SENSITIVE_CONTENT_FLAG_LABELS)
    return f"""
POSITION / GENRE TAXONOMY

Return one POSITION label from the allowed list below. Choose the most specific
visible position or sex act. Use OTHER only when the frame is a transition,
not a sex-act/nude retail frame, or genuinely unclear after inspection.

Do not overuse OTHER. If the visible act is clear, map common descriptions to
the closest allowed label: doggy/from-behind -> DOGGY_STYLE, side/spooning ->
SPOON, blowjob/oral on male -> ORAL_BJ, cunnilingus/oral on female ->
ORAL_CUNN, toy/dildo/vibrator -> TOY, multi-performer action -> GROUP.

Allowed POSITION labels:
{position_labels}

Return GENRES as top-level scene/category tags only when visible or strongly
supported by scene context. Return SUBGENRES for specific niches/acts/roleplay
only when clearly supported. Use NONE rather than guessing.

Allowed GENRES:
{genre_labels}

Allowed SUBGENRES:
{subgenre_labels}

CONTENT WARNING FLAGS

Flag visible blood, urine, or feces/scat for operator review. These are review
warnings, not automatic rejection. Do not penalize score solely because a flag
is present; still score thumbnail quality normally.

Allowed CONTENT_FLAGS:
{sensitive_labels}
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
    taxonomy_section = _taxonomy_section()

    prompt = f"""Score this adult VOD frame for use as a thumbnail/cover image.

{scene_context}
DETECTED GENRES: {genres_str}{genre_section}{studio_section}
{taxonomy_section}

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
TIER B — STRONG SIGNALS (integer points, only if unambiguous)
═══════════════════════════════════════════════════════════════

B1:  Performer eye contact with camera (any count):     +10
B2:  Eye whites visible (open, any direction):          +6
B3:  Visible penetration matching scene type/genre:     +16
B4:  Multiple penises visible (group scenes only):      +10
B5:  Female centered or dominant in composition:         +6
B6:  Genuine pleasure expression on female face:        +10
B7:  Money shot (visible facial/creampie/squirt):        +16
B8:  Body fully nude and dominant in frame:              +7
B9:  Oral close-up (mouth contact + readable face):     +12
B10: Dual gaze at lens (exactly 2 performers):          +10
B11: Aggressive/intense action beat is clearly visible: +8
B12: Climax cue (release, anticipation, creampie setup):+12
B13: Bodily fluid prominently visible (spit/cum/etc):   +14

B1 applies once per frame regardless of how many performers are looking
at the camera. The GAZE field below records the count for descriptive
purposes only — do not stack the B1 points more than once.

═══════════════════════════════════════════════════════════════
TIER C — AESTHETIC SIGNALS (separate the cinematic from the competent)
═══════════════════════════════════════════════════════════════

C1: Lighting/aesthetic looks professional (not flat/flash): +5
C2: Strong contrast/colors (pops as thumbnail):             +5
C3: Background not distracting (composition reads cleanly): +3
C4: Face-forward close-up framing reads clean at thumb size:+4
C5: Subject/action readability survives heavy downscale:    +4

Tier C is what separates a competent action frame (which most
candidates are) from a cinematic cover (which is what we want at the
top of the rank). Be strict — apply C1 only when lighting actively
flatters the subject, not just because the frame is exposed correctly.

═══════════════════════════════════════════════════════════════
TIER D — PENALTIES (subtract for degradations; include only when present)
═══════════════════════════════════════════════════════════════

D1: Mild blur or motion softness hurts readability:         -8
D2: Awkward crop (cut faces/body key points):               -6
D3: Face occlusion weakens cover utility:                   -7
D4: Distracting clutter/noise in frame:                     -5
D5: Ambiguous action read despite nudity:                   -6

═══════════════════════════════════════════════════════════════
SCORE FORMULA (0–100, TIER_A_PASS only)
═══════════════════════════════════════════════════════════════

RETAIL_BASE = 34 (every Tier-A-passing frame starts here — "meets minimum
B2B thumbnail hygiene" before bonuses).

SCORE = min(100, RETAIL_BASE + sum(Tier B + Tier C) - sum(Tier D penalties)).
The backend recomputes score from your listed codes, so code lists must match
what is truly visible. If nothing beyond base is clearly earned, SCORE should
sit near 34–45. Do not inflate.

Calibration (use the full span — avoid parking unrelated frames in the same band):
  34–48: weak / cluttered / flat — usable only as filler
  49–62: competent but ordinary
  63–76: clearly good retail thumbnail
  77–88: strong — would compete for hero placement
  89–96: exceptional — immediate hero artwork
  97–100: flawless — reserve for rare perfect composition + moment + light

If two frames differ only slightly in quality, their SCORE values must differ
by a few points, not sit on the same tenth. Penalize muddy focus, awkward crop,
and busy backgrounds with lower SCORE even when nudity is present.

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT (REQUIRED)
═══════════════════════════════════════════════════════════════

If Tier A passes, respond EXACTLY in this format:

TIER_A_PASS: yes
TIER_B_PRESENT: <comma-separated B-codes that apply, e.g. B1,B3,B6>
TIER_C_PRESENT: <comma-separated C-codes>
TIER_D_PRESENT: <comma-separated D-codes, or NONE>
SCORE: <number 0.0-100.0, one decimal allowed>
TYPE: <NUDE/SEX_ACT/PENETRATION/BUILDUP/FINISH/COMPOSITION>
GAZE: <SINGLE/DUAL/TRIPLE/AVERTED/CLOSED/REAR>
AESTHETIC: <PROFESSIONAL/STANDARD/AMATEUR>
PENETRATION_VISIBLE: <yes/no>
PENETRATION_CONFIDENCE: <0.00-1.00>
ACTION_EVIDENCE: <EXPLICIT_PENETRATION|ORAL_CONTACT|POSE_NO_CONTACT|OCCLUDED|WATER_OCCLUSION|NONE>
POSITION: <one allowed POSITION label>
POSITION_CONFIDENCE: <0.00-1.00>
GENRES: <comma-separated allowed GENRES, or NONE>
SUBGENRES: <comma-separated allowed SUBGENRES, or NONE>
CONTENT_FLAGS: <comma-separated allowed CONTENT_FLAGS, or NONE>
CONTENT_FLAG_CONFIDENCE: <0.00-1.00>
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

Dual-gaze scoring policy:
  - B10 requires exactly two performers both looking at lens.
  - Do not award B10 for SINGLE or TRIPLE gaze.
  - POV with camera-as-male still qualifies if two performers are lens-aware.

Penetration truth rules (critical):
  - PENETRATION_VISIBLE=yes ONLY when explicit insertion/contact is clearly
    visible in-frame (not implied by pose).
  - If limbs/water/angle occlude the key area, use PENETRATION_VISIBLE=no,
    confidence <= 0.49, and ACTION_EVIDENCE=OCCLUDED or WATER_OCCLUSION.
  - TYPE must be PENETRATION only when PENETRATION_VISIBLE=yes.
  - In uncertain cases, prefer conservative outputs:
      TYPE=SEX_ACT or NUDE, PENETRATION_VISIBLE=no.

If lighting is flat, do not award C1. If composition is cluttered, do not award C3.
The score must differentiate frames, not normalize them toward the mid 80s.
"""
    return prompt


def build_streaming_scoring_prompt(
    primary_scene_type: str = "STANDARD",
    detected_genres: Optional[List[str]] = None,
    performer_count: Optional[int] = None,
    studio_language: str = "en",
    studio_hint: Optional[str] = None,
) -> str:
    """Compact scoring prompt for high-volume streaming scans.

    It preserves the parser contract and deterministic B/C/D code scoring, but
    removes long explanatory prose. The full prompt remains available for
    classic scans and slower audit modes.
    """
    genres = detected_genres or []
    genres_str = ", ".join(genres) if genres else "none detected"
    scene_bits = [f"SCENE TYPE: {primary_scene_type or 'STANDARD'}"]
    if performer_count:
        scene_bits.append(f"EXPECTED PERFORMERS: {performer_count}")
    if studio_hint:
        scene_bits.append(f"STUDIO CONTEXT: {studio_hint}")
    if studio_language and studio_language != "en":
        scene_bits.append(f"MARKET LANGUAGE: {studio_language}")
    taxonomy_section = _taxonomy_section()

    return f"""Score this adult VOD frame as a storefront cover candidate.

{chr(10).join(scene_bits)}
DETECTED GENRES: {genres_str}
{taxonomy_section}

TIER A hard fails. If any applies, output only:
TIER_A_FAIL: <DB1|DB2|DB3|DB4>
SCORE: 0
END

DB1 no clear female lead visible
DB2 no visible female nudity or clear sex-act read
DB3 severe blur or unreadable thumbnail
DB4 rear composition without clear butt/ass read

If Tier A passes, list only visible evidence codes:
B1 eye contact, B2 open eyes, B3 explicit visible penetration, B4 multi-penis/group signal,
B5 female centered/dominant, B6 pleasure expression, B7 money shot/fluid release,
B8 nude body dominant, B9 oral contact close-up, B10 dual lens-aware gaze,
B11 intense action beat, B12 climax cue/setup, B13 visible fluid.
C1 flattering light, C2 strong color/contrast, C3 clean background, C4 readable close-up,
C5 strong thumbnail readability.
D1 mild blur, D2 awkward crop, D3 face occlusion, D4 clutter/noise, D5 ambiguous action.

Score uses: 34 base + B/C bonuses - D penalties. Use the full 0-100 range.
Do not inflate. Strong but ordinary retail frames usually land 63-88.
Reserve 89+ for exceptional composition, clarity, and moment.

Penetration truth rules:
- TYPE=PENETRATION only when PENETRATION_VISIBLE=yes.
- Use PENETRATION_VISIBLE=no and ACTION_EVIDENCE=OCCLUDED when the key area is hidden.
- POSE_NO_CONTACT is not penetration.
- POSITION=OTHER only for transitions, non-sex-act frames, or genuinely unclear frames.

Respond EXACTLY:
TIER_A_PASS: yes
TIER_B_PRESENT: <comma B-codes or NONE>
TIER_C_PRESENT: <comma C-codes or NONE>
TIER_D_PRESENT: <comma D-codes or NONE>
SCORE: <0.0-100.0>
TYPE: <NUDE/SEX_ACT/PENETRATION/BUILDUP/FINISH/COMPOSITION>
GAZE: <SINGLE/DUAL/TRIPLE/AVERTED/CLOSED/REAR>
AESTHETIC: <PROFESSIONAL/STANDARD/AMATEUR>
PENETRATION_VISIBLE: <yes/no>
PENETRATION_CONFIDENCE: <0.00-1.00>
ACTION_EVIDENCE: <EXPLICIT_PENETRATION|ORAL_CONTACT|POSE_NO_CONTACT|OCCLUDED|WATER_OCCLUSION|NONE>
POSITION: <one allowed POSITION label>
POSITION_CONFIDENCE: <0.00-1.00>
GENRES: <comma allowed GENRES or NONE>
SUBGENRES: <comma allowed SUBGENRES or NONE>
CONTENT_FLAGS: <comma allowed CONTENT_FLAGS or NONE>
CONTENT_FLAG_CONFIDENCE: <0.00-1.00>
END
"""


def build_simplified_prompt() -> str:
    """Simpler prompt for Fallback C (when full pipeline isn't yielding floor)."""
    taxonomy_section = _taxonomy_section()
    return f"""Score this adult VOD frame as a thumbnail candidate on a 0–100 scale.
{taxonomy_section}

Holistic rubric (one SCORE number — use the full range, do not cluster in the 80s):
- 0: unusable (no clear female lead, severe blur, or fails basic retail hygiene)
- 35–52: weak filler
- 53–68: acceptable
- 69–82: good cover material
- 83–92: strong
- 93–100: exceptional (rare)

Female performer clearly visible; nudity or clear sex-act read for non-zero scores;
frame sharp enough for a storefront thumbnail; eye contact and lighting lift the score.

For the GAZE field, be conservative. Mark DUAL or TRIPLE only if multiple
performers clearly have their eyes pointed at the camera lens; otherwise
prefer SINGLE or AVERTED.

Output EXACTLY in this format:
SCORE: <0.0-100.0>
TYPE: <NUDE/SEX_ACT/PENETRATION/COMPOSITION>
GAZE: <SINGLE/DUAL/TRIPLE/AVERTED/CLOSED/REAR>
PENETRATION_VISIBLE: <yes/no>
PENETRATION_CONFIDENCE: <0.00-1.00>
ACTION_EVIDENCE: <EXPLICIT_PENETRATION|ORAL_CONTACT|POSE_NO_CONTACT|OCCLUDED|WATER_OCCLUSION|NONE>
POSITION: <one allowed POSITION label>
POSITION_CONFIDENCE: <0.00-1.00>
GENRES: <comma-separated allowed GENRES, or NONE>
SUBGENRES: <comma-separated allowed SUBGENRES, or NONE>
CONTENT_FLAGS: <comma-separated allowed CONTENT_FLAGS, or NONE>
CONTENT_FLAG_CONFIDENCE: <0.00-1.00>
END
"""


def build_soft_thumbnail_prompt() -> str:
    """Prompt for non-nude / soft thumbnail selection."""
    return """Score this frame for NON-NUDE soft thumbnail suitability on a 0-100 scale.

Hard requirements:
- No explicit visible nipples, vagina, anus, or explicit penetration.
- No visible semen/creampie/facial/spit-string focus.
- Frame must still be commercially useful (clear subject, sharp, readable).

Scoring guidance:
- 0-30: explicit content present or unusable frame
- 31-55: safe but weak/boring/blurry
- 56-75: safe and usable
- 76-90: strong soft thumbnail candidate
- 91-100: excellent soft cover (clean, clear, compelling)

Output EXACTLY in this format:
TIER_A_PASS: yes
SCORE: <0.0-100.0>
TYPE: <SOFT_CLOTHED/COMPOSITION/SEX_ACT/NUDE>
GAZE: <SINGLE/DUAL/TRIPLE/AVERTED/CLOSED/REAR>
AESTHETIC: <PROFESSIONAL/STANDARD/AMATEUR>
PENETRATION_VISIBLE: <yes/no>
PENETRATION_CONFIDENCE: <0.00-1.00>
ACTION_EVIDENCE: <EXPLICIT_PENETRATION|ORAL_CONTACT|POSE_NO_CONTACT|OCCLUDED|WATER_OCCLUSION|NONE>
END
"""


def build_scene_insight_prompt() -> str:
    """Vision prompt — describe a single contact-sheet image factually.

    Asked once per scene against the contact sheet (or top cover) so we get
    coarse setting/mood/feature data without re-scoring every frame.
    """
    return """You are looking at a contact sheet of representative thumbnails from a single adult VOD scene.

Describe the scene factually. Avoid editorial language. Avoid words like "wild", "naughty", "crazy".

OUTPUT FORMAT (REQUIRED, exact keys, no preamble):

SETTING: <short phrase, e.g. "bathtub", "hotel suite bedroom", "kitchen counter", "outdoor patio">
LOCATION_HINT: <one phrase if a distinctive location is visible (e.g. "rooftop pool", "white-tile bathroom", "neon-lit dressing room"), else "none">
NOTABLE_FEATURES: <comma-separated factual tags: lingerie color, distinctive props, lighting, framing, etc. (e.g. "wet hair, bath bubbles, candlelight"). Avoid evaluating attractiveness.>
ACTION_SUMMARY: <2 sentences, factual: what's happening across the frames. Use neutral terms.>
MOOD: <one or two words: "playful", "intimate", "intense", "teasing", "casual">

END
"""


def build_enriched_title_prompt(
    studio: str,
    performers: List[str],
    scene_type: str,
    genres: List[str],
    description: str,
    insight: dict,
    position_summary: dict,
    seed_taxonomy: Optional[dict] = None,
    title_tone: str = "edgy",
    language: str = "en",
    n_suggestions: int = 5,
    top_examples: Optional[List[dict]] = None,
    retrieval_scope: str = "titles",
    analysis_context: str = "",
    metadata_fact_sheet_context: str = "",
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
    retrieval_scope_note = _retrieval_scope_note(retrieval_scope)
    examples_block = _format_top_examples(top_examples or [], retrieval_scope=retrieval_scope)

    return f"""Generate retail-optimized titles AND a marketing-ready long description for an adult VOD scene.

SCENE CONTEXT:
  Studio: {studio}
  Performers: {performer_str}
  Scene type: {scene_type}
  Genres: {genres_str}
  Operator description: {description or "(none provided)"}
  Setting: {setting}
  Location hint: {location_hint}
  Notable features: {features_str}
  Action summary: {action}
  Mood: {mood}
  Position rollup across selected covers: {pos_str}
  AMG scene analysis: {analysis_context or "(none)"}
  Metadata fact sheet: {metadata_fact_sheet_context or "(none)"}
  Seed categories from scene signals: {seed_categories}
  Seed tags from scene signals: {seed_tags}
  Retrieval scope: {retrieval_scope}
  Requested title tone: {tone}
  Language: {language}
  Top-K approved examples:
{examples_block}

REQUIREMENTS:
  - Each title 30-80 characters, in the requested language.
  - Vary the patterns across the {n_suggestions} suggestions.
  - Prefer concrete details (setting, performer name, position, mood) over generic adjectives.
  - Treat the metadata fact sheet as the highest-signal source for categories, tags, action beats, and warnings.
  - Avoid clichéd words: "wild", "crazy", "naughty".
  - Do not repeat near-identical title phrasing across TITLE_1..TITLE_5.
  - Keep punctuation clean: no emoji, no all-caps shouting, no repeated exclamation marks.
  - Example block is for style guidance only; never copy exact title or sentence fragments.
  - Never reuse distinctive proper-noun combinations from examples unless present in this scene context.
  - Rewrite in fresh phrasing and keep it scene-specific.
  - If a lead performer is provided, include that performer name in EVERY title.
  - The long description must mention the lead performer by name at least once.
  - Write with commercial energy (confident, explicit, sellable), not bland catalog prose.
  - Use concrete action terms that match the scene (e.g. POV, blowjob, anal, creampie, squirting, toys).
  - Make tone differences obvious:
      * retail_safe: cleaner wording, lower intensity
      * edgy: explicit and conversion-oriented
      * premium_story: polished/cinematic narrative
      * creative: novel phrasing and less repetitive structure
  - Long description: 2-4 sentences, factual, suitable for a store listing. Mention performers,
    setting, and one or two notable details. Do NOT use the words listed above.
  - Long description must be 140-500 characters.
  - Also return category and tag suggestions tailored to this scene.
  - Categories should be platform-style labels (Title Case).
  - Tags should be lowercase, short, and search-friendly.
  - CATEGORY_SUGGESTIONS should be 8-15 unique entries.
  - TAG_SUGGESTIONS should be 15-30 unique entries.

{market_note}
{tone_note}
{retrieval_scope_note}

OUTPUT FORMAT (REQUIRED, no preamble):

TITLE_1: <text>
STYLE_1: <performer_led | narrative_hook | scene_descriptive | studio_branded | numbered_series>

TITLE_2: <text>
STYLE_2: <pattern>

TITLE_3: <text>
STYLE_3: <pattern>

TITLE_4: <text>
STYLE_4: <pattern>

TITLE_5: <text>
STYLE_5: <pattern>

LONG_DESCRIPTION: <2-4 sentence factual description>
CATEGORY_SUGGESTIONS: <comma-separated categories, 8-15 items>
TAG_SUGGESTIONS: <comma-separated tags, 15-30 items>

END
"""


def _format_top_examples(rows: List[dict], *, retrieval_scope: str = "titles") -> str:
    if not rows:
        return "  (none)"
    scope = str(retrieval_scope or "titles").strip().lower()
    lines: List[str] = []
    for idx, row in enumerate(rows[:5], start=1):
        title = str((row or {}).get("title") or "").strip() or "(no title)"
        studio = str((row or {}).get("studio") or "").strip() or "unknown"
        scene_type = str((row or {}).get("scene_type") or "").strip() or "STANDARD"
        genres = ", ".join((row or {}).get("genres") or []) or "none"
        line = f"  EXAMPLE_{idx}: studio={studio}; type={scene_type}; genres={genres}; title={title}"
        if scope in {"titles_description", "full"}:
            desc = str((row or {}).get("long_description") or "").strip()
            if len(desc) > 180:
                desc = desc[:180].rstrip() + "..."
            if _example_description_ready(row):
                line += f"; description={desc}"
            else:
                line += "; description=omitted_low_quality"
        if scope == "full" and _example_full_metadata_ready(row):
            tags = ", ".join(str(x) for x in ((row or {}).get("tags") or [])[:20])
            cats = ", ".join(str(x) for x in ((row or {}).get("categories") or [])[:12])
            line += f"; categories={cats}; tags={tags}"
        elif scope == "full":
            line += "; categories_tags=omitted_low_quality"
        lines.append(line)
    return "\n".join(lines)


def _example_description_ready(row: dict) -> bool:
    desc = str((row or {}).get("long_description") or "").strip()
    return 120 <= len(desc) <= 600


def _example_full_metadata_ready(row: dict) -> bool:
    tags = (row or {}).get("tags") or []
    cats = (row or {}).get("categories") or []
    return _example_description_ready(row) and len(tags) >= 12 and len(cats) >= 6


def _retrieval_scope_note(scope: str) -> str:
    clean = str(scope or "titles").strip().lower()
    if clean == "titles":
        return "RETRIEVAL POLICY: Use retrieved examples only for title style and lexical specificity."
    if clean == "titles_description":
        return "RETRIEVAL POLICY: Use retrieved examples for titles and long-description structure."
    if clean == "full":
        return "RETRIEVAL POLICY: Use retrieved examples for titles, long description, tags, and categories."
    return "RETRIEVAL POLICY: Use retrieval conservatively and prioritize the current scene context."


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
