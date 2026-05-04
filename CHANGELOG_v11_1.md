# AMG OS v11.1 — Changelog

**Released:** 2026-05-04
**Baseline:** v11.0

## Critical Fixes

### Performer code parsing (CRITICAL)
v11.0 misclassified all-female codes:
- `GG` was treated as generic COUPLE → now **LESBIAN**
- `GGG` was treated as generic THREESOME → now **LESBIAN_THREESOME**
- Single-char codes (`G`, `B`) were rejected → now accepted as solo
- All-male codes (`BB`, `BBB`) were treated as standard → now **flagged as ALL_MALE_CONTENT** (processed but reviewed)

**Impact:** Lesbian scenes from YasminaBrady and Maximo Garcia now get proper genre-specific scoring and prompt guidance. All-male scenes are routed to a review flag instead of silently mis-processing.

### Eye contact tier split
v11.0 had a single eye-contact tier (B1, +2.0). v11.1 splits this:
- **B1a** (single performer eye contact): +2.0
- **B1b** (dual eye contact, 2 performers both at camera): +3.5
- **B1c** (triple+ eye contact): +4.5

Mutually exclusive — only one applies per frame. Dual eye contact is rare and exceptionally valuable as cover material; the old +2.0 was significantly under-rewarding it.

### Floor and cluster expansion
- `COVER_FLOOR`: 10 → **15**
- Cover caps by duration: 10/12/15/18 → **20/25/28/30**
- Cluster windows: ±2/5/15/30s → **±3/7/20/40s** (larger sample radius around peaks)

**Impact:** More candidates per scene → operators get more choice, better hero picks. Per-scene runtime ~15-30% longer (90-150s vs 60-120s).

## New Commands

| Command | Description |
|---------|-------------|
| `amg review <scene>` | Open human review form (title, cover, genres, performers, platforms) |
| `amg ready <scene>` | Distribution-ready check (per-platform validation) |
| `amg find <filters>` | Search scene library (genre, performer, score, etc.) |
| `amg dvd-compile <s1>...` | Package multiple processed scenes into DVD output |
| `amg dashboard` | Performance trends, batch history, error patterns |
| `amg resume <scene>` | Re-run a failed scene |

## New Features

### Title generator
Each scene now gets 5 AI-generated title suggestions during `amg review`, each tagged with a style pattern (performer_led, narrative_hook, scene_descriptive, studio_branded, numbered_series). Per-platform character limits enforced (AEBN 60, SLR 80, ADE 100).

### Per-platform compliance
`PLATFORM_REQUIREMENTS` configuration covers AEBN, SLR, ADE — title length limits, banned terms, 2257/release requirements, resolution preferences. Distribution-ready gate checks all targeted platforms.

### Performer document registry
`data/performer_documents/` stores reusable 2257 docs and model releases per performer. Distribution gate checks for matching releases per platform automatically.

### Batch performance tracking
Each `amg batch` writes a summary to `data/batch_summaries/`. Dashboard surfaces trends — speeding up, stable, slowing down.

### Plain-text error form
Failed scenes get a readable error report with recovery commands. No more raw stack traces in the operator path.

## Architecture Hooks (for v11.2 / v12)

### v11.2 — Title research
- `data/title_corpus/` — research corpus (empty in v11.1)
- `data/title_patterns/research_patterns.json` — research-driven patterns (empty)
- `_load_research_patterns()` returns None now; reads JSON when populated
- `industry_patterns` argument in `build_title_generation_prompt()` ready for injection

### v12 — Operator preference learning
- `data/operator_history/{operator}.json` — voice/style learning per operator
- `_load_operator_history()` returns None now; reads JSON when populated
- Decision log schema includes `human_feedback` block (empty for now)

### v12 — Active learning
- Title performance feedback loop ready in review schema
- Cover selection ML hooks (track human picks vs AI rank #1)
- Cross-creator network effects (decision log captures co-performer pairings)

## Migration

Extract over existing `~/AMG_OS/`:
```bash
cd ~
unzip -o ~/Downloads/amg_os_v11_1.zip
cd AMG_OS
source venv/bin/activate
amg verify
```

Existing `data/` is preserved. New subdirectories (`reviewed/`, `distribution_status/`, `batch_summaries/`, etc.) are created on first use.

## Daily Workflow

```bash
# 1. Process incoming
amg batch ~/Incoming/

# 2. Review each completed scene
amg review "27 BBGG - couple swap"

# 3. Verify distribution-ready
amg ready "27 BBGG - couple swap"

# 4. Browse for compilations later
amg find --genre swinger --min-score 8.0

# 5. Compile DVDs from picks
amg dvd-compile <id1> <id2> <id3> <id4> --theme "Swinger Weekend"

# 6. Weekly performance review
amg dashboard
```

## Test Results

- 22/22 v11.1 critical-fix tests passing
- 14/14 new module tests passing
- Existing v11.0 tests preserved (50+ tests)
