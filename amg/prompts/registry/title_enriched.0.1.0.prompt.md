Generate retail-optimized titles AND a marketing-ready long description for an adult VOD scene.

SCENE CONTEXT:
  Studio: {studio}
  Performers: {performer_str}
  Scene type: {scene_type}
  Genres: {genres_str}
  Operator description: {description}
  Setting: {setting}
  Location hint: {location_hint}
  Notable features: {features_str}
  Action summary: {action}
  Mood: {mood}
  Position rollup across selected covers: {pos_str}
  Seed categories from scene signals: {seed_categories}
  Seed tags from scene signals: {seed_tags}
  Requested title tone: {tone}
  Language: {language}

REQUIREMENTS:
  - Each title 30-80 characters, in the requested language.
  - Vary the patterns across the {n_suggestions} suggestions.
  - Prefer concrete details (setting, performer name, position, mood) over generic adjectives.
  - Avoid clichéd words: "wild", "crazy", "naughty".
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
  - Also return category and tag suggestions tailored to this scene.
  - Categories should be platform-style labels (Title Case).
  - Tags should be lowercase, short, and search-friendly.

{market_note}
{tone_note}

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
