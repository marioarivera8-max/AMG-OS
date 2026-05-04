# Review Workflow — v11.1

After `amg batch` completes, scenes are processed but not yet distribution-ready. The review workflow is where you (the human) make the final calls: title, cover pick, genres, performers, platforms.

## Quick Path

```bash
# Process incoming
amg batch ~/Incoming/

# Review each scene (form opens in terminal)
amg review "27 BBGG - couple swap"

# Verify everything's good
amg ready "27 BBGG - couple swap"
```

## What `amg review` Does

It opens a 7-section terminal form for the scene:

### 1. Title
- AI generates 5 suggestions tagged by style pattern
- You pick one (1-5) or type your own (6)
- Each suggestion shows char count + style + warnings
- Styles: `performer_led`, `narrative_hook`, `scene_descriptive`, `studio_branded`, `numbered_series`

Example:
```
Suggestions:
  1. Yasmina's Couple Swap Adventure
     [33 chars · performer_led]
  2. Two Couples, One Wild Night
     [27 chars · narrative_hook]
  3. Couple Swap Foursome with Yasmina
     [34 chars · scene_descriptive]
  4. The YasminaBrady Experience: Vol 27
     [37 chars · studio_branded]
  5. Couples Files: Episode 27
     [25 chars · numbered_series]
  6. Type my own
```

### 2. Cover (pick the hero)
- Shows top 10 covers ranked by score
- Reminds you where the contact sheet is
- Pick a number, or "skip" to defer

### 3. Genres
- AI-detected genres shown
- Press ENTER to accept, or type comma-separated overrides

### 4. Performers
- Suggested from studio's regular performers list
- Press ENTER to accept, or type names

### 5. Target platforms
- Shows which platforms accept your title length
- Press ENTER for "all that fit", or specify

### 6. Release date
- Defaults to today
- Type `YYYY-MM-DD` for a specific date

### 7. Notes
- Optional free text

## Output Files

`data/reviewed/{scene_id}.json`:
```json
{
  "scene_id": "27 BBGG - couple swap",
  "reviewed_at": "2026-05-04T19:42:00Z",
  "title": {
    "text": "Two Couples, One Wild Night",
    "style": "narrative_hook",
    "source": "ai_suggestion"
  },
  "cover_pick": {
    "filename": "rank_03_score_8.4_t0123.jpg",
    "rank": 3
  },
  "genres_confirmed": ["FOURSOME", "SWINGER"],
  "performers_confirmed": ["Yasmina Brady", "Tabatha", "Jimmy", "Marco"],
  "target_platforms": ["AEBN", "SLR", "ADE"],
  "release_date": "2026-05-15",
  "notes": ""
}
```

## What `amg ready` Does

After review, this verifies the scene is distribution-ready:

```
═══════════════════════════════════════════════════════════════
  DISTRIBUTION-READY CHECK: 27 BBGG - couple swap
═══════════════════════════════════════════════════════════════

  Overall: ✓ READY

  CHECKS:
    ✓ Decision log present
    ✓ Human review completed
    ✓ Title set: Two Couples, One Wild Night
    ✓ Hero cover designated: rank_03_score_8.4_t0123.jpg
    ✓ Cover floor (15): 23 covers delivered

  PER-PLATFORM:
    ✓ AEBN
    ✓ SLR
    ✓ ADE
═══════════════════════════════════════════════════════════════
```

If something's wrong (missing 2257, title too long, etc.), it lists per-platform blockers. Output saved to `data/distribution_status/{scene_id}.json`.

## Performer Document Registry

For per-performer model releases, place files in `data/performer_documents/`:

```
data/performer_documents/
├── yasmina_brady_release.pdf
├── yasmina_brady_2257.pdf
├── tabatha_release.pdf
└── ...
```

The naming convention is `{performer_name_lowercase_underscored}_release.pdf` or `_2257.pdf`. Distribution gate matches by name automatically.

Platforms that require individual releases (AEBN, ADE) will block if any performer is missing one. SLR currently treats studio-level release as sufficient.

## Re-reviewing a Scene

If you change your mind about a title or cover:

```bash
amg review "27 BBGG - couple swap"
```

It detects the existing review and asks if you want to re-open. Saying yes overwrites; saying no shows the existing review.

## Search After Review

Once reviewed, scenes are searchable by their confirmed metadata:

```bash
amg find --performer "Yasmina"          # All Yasmina scenes
amg find --reviewed --min-score 8.0     # Reviewed + high quality
amg find --ready-for AEBN                # Ready to upload to AEBN
amg find --genre LESBIAN --studio YasminaBrady
```

## v11.2 Preview

Title suggestions in v11.1 use general industry patterns. v11.2 will replace these with research-driven patterns derived from AVN/XBIZ award winners and top-rated catalogs. No code change needed — drop the new patterns file at `data/title_patterns/research_patterns.json` and the title generator picks them up automatically.

## v12 Preview

After ~100 reviewed scenes, v12 will start learning **your** preferences:
- Which AI suggestions you pick most
- Which words you consistently swap out
- Per-operator voice mimicry

This is gated behind the `data/operator_history/` directory which is empty in v11.1.
