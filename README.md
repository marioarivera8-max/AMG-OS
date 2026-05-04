# AMG OS v11.1

**Adult VOD Scene Processor — Apple Silicon optimized**

AMG OS is a research-validated scene processing system for B2B adult VOD distribution.
v11 takes a video file and produces a curated set of cover candidate JPEGs plus a
contact sheet plus a decision log — all locally, no cloud calls, fast.

---

## What v11 Does

For each scene:

1. **Inventory** — detects studio, parses performer code (BBGG/BG/etc.), reads filename for genre signals
2. **Compliance** — verifies 2257 documentation present
3. **Calibration** — samples 100 frames, derives per-source sharpness thresholds
4. **Tiered scan** — escalating 3-tier search: strict → loose → lenient
5. **Finish hunter** — focused search of last 20% (money shots)
6. **Buildup hunter** — focused search of 25-50% (anticipation moments)
7. **Cluster expansion** — score-triggered mining around top hits (±2s to ±30s windows)
8. **Floor enforcement** — fallback cascade (A→C→D) ensures ≥15 covers per scene (v11.1)
9. **Output** — saves covers with descriptive filenames + AITG contact sheet
10. **Decision log** — captures everything for the learning system

Target performance: **90-150 seconds per scene** on M4 Pro 24GB.



---

## v11.1 Highlights

**See [CHANGELOG_v11_1.md](CHANGELOG_v11_1.md) for full details.**

### Critical fixes
- Lesbian code parsing: `GG`/`GGG` now correctly classified as LESBIAN (was COUPLE/THREESOME)
- All-male codes (`BB`, `BBB`) flagged for review
- Eye contact tier split: dual eye contact (B1b) now scored +3.5 (was undifferentiated)
- Cover floor raised to 15, caps expanded to 20-30 by duration

### New commands
- `amg review <scene>` — Human review form with AI title suggestions, cover pick, genre/performer/platform confirmation
- `amg ready <scene>` — Distribution-ready gate with per-platform validation
- `amg find` — Scene library search by genre/performer/score/etc.
- `amg dvd-compile` — Package multiple scenes into DVD output with quad case art
- `amg dashboard` — Performance trends + recent batch history
- `amg resume <scene>` — Re-run a failed scene

### Daily workflow
```bash
amg batch ~/Incoming/                              # Process scenes
amg review "27 BBGG - couple swap"                 # Pick title, cover, etc.
amg ready "27 BBGG - couple swap"                  # Verify distribution-ready
amg find --genre swinger --min-score 80            # Search library (0–100 scores)
amg dvd-compile <id1> <id2> <id3> <id4> --theme "Swinger Weekend"
amg dashboard                                       # Weekly review
```

See [docs/REVIEW_WORKFLOW.md](docs/REVIEW_WORKFLOW.md) and [docs/DVD_COMPILATION.md](docs/DVD_COMPILATION.md).

---

## Installation

```bash
# Clone or extract
cd ~/AMG_OS

# Run setup (idempotent — safe to re-run)
./scripts/setup.sh

# Open a NEW terminal (loads env vars + 'amg' alias)
# Then verify
amg verify
```

The setup script:
- Installs Ollama + ffmpeg via Homebrew
- Configures Apple Silicon optimizations (MLX, flash attention, parallel, q8 KV cache, 24h keep-alive)
- Sets env vars in BOTH `~/.zshrc` AND `launchctl` (so Ollama GUI app sees them too)
- Pulls `qwen2.5vl:7b` vision model
- Creates Python venv with pinned deps
- Installs `amg` shortcut alias

---

## Daily Usage

```bash
# Single scene
amg process /path/to/scene/

# Batch
amg batch /path/to/incoming/

# Performance review (after several scenes)
amg analyze
amg analyze --days 7

# Health check anytime
amg verify

# What's running right now
amg status
```

---

## What Makes v11 Fast

| Optimization | Speedup |
|---|---|
| MLX backend (Apple Silicon native ML) | 1.93x |
| Flash Attention | 1.15x |
| Parallel scoring (4 workers) | 1.6x |
| Q8 KV cache quantization | 1.1x |
| 24h keep-alive (model stays in memory) | Avoid 30s reload per batch |
| Decord random-access frame extraction | 2x for cluster mining |
| Perceptual hash dedup before AI scoring | Saves 30-50% AI calls |
| Adaptive thresholds (per-source calibration) | Skip useless work |

**Combined: ~8-12x faster than v10.3** (13 min/scene → 60-120 sec/scene).

---

## Architecture

```
amg/
├── __init__.py
├── __version__.py
├── config.py             # Single source of truth — all defaults & constants
├── cli.py                # `amg` command dispatcher
├── pipeline.py           # End-to-end orchestrator (the conductor)
│
├── ingest/               # Read scenes, detect studios, parse codes
├── video/                # Frame extraction, sharpness, faces, dedup
├── scoring/              # AI client, prompt, parser, parallel orchestrator
├── scanning/             # Tiered scan, finish/buildup hunters, cluster, fallback
├── output/               # Cover saving, enhancement, contact sheet, decision log
├── compliance/           # 2257 verification, audit log
├── learning/             # Analyze logs, recalibrate, record outcomes
└── utils/                # Timing, logging
```

See `docs/ARCHITECTURE.md` for deep dive.

---

## Multi-Mac Setup

```bash
# On primary Mac:
git init && git add -A && git commit -m "v11.0 baseline"
git remote add origin <your-private-repo>
git push origin main

# On secondary Mac:
git clone <your-private-repo> ~/AMG_OS
cd ~/AMG_OS
./scripts/setup.sh
amg verify
```

Code syncs via Git. Data (decision logs, performer registry, source videos)
stays local on each Mac.

---

## Privacy & Security

- **100% local processing** — no cloud calls, no telemetry, no data uploads
- **Ollama bound to localhost** (127.0.0.1:11434)
- All decision logs stored at `~/AMG_OS/data/decision_logs/`
- Source files never leave the Mac
- Multi-Mac sync via Git carries CODE only (data is in `.gitignore`)

---

## Documentation

- `docs/ARCHITECTURE.md` — System design deep dive
- `docs/RUNBOOK.md` — Common operations
- `docs/TROUBLESHOOTING.md` — Common issues + fixes
- `docs/LEARNING.md` — How the learning system works
- `docs/FLEET.md` — Multi-Mac coordination

---

## Version History

- **v11.0** (May 2026) — Modular package, qwen2.5-vl, Apple Silicon optimizations
- **v10.3** (April 2026) — Tier A/B/C scoring, finish/buildup hunters
- **v10.1** (March 2026) — First production deployment
- **v9.x** (2025-26) — Initial baseline

---

## Scope Boundary

v11.0 IS:
- Scene processor (covers + contact sheet + decision log)
- Tier A/B/C scoring with floor enforcement
- Studio profiles + performer registry
- Multi-Mac coordination via Git

v11.0 IS NOT (deferred to AMG OS Phases 1+):
- Title generation
- PSD auto-fill
- Platform packaging / upload automation
- Google Sheets integration
- Operator feedback loop (Phase 4 of learning roadmap)

These build ON TOP of v11. v11 ships independently.
