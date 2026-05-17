# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Adult content notice

AMG OS processes adult VOD content (B2B distribution). Anthropic / OpenAI / Google vision APIs prohibit this category, so the pipeline is **local-only by design** (Ollama + qwen2.5vl on Apple Silicon). Do not propose closed-API vision solutions — they're a non-starter.

## Critical context: read first

`CLAUDE_CODE_HANDOFF.md` at the repo root is the operator's hand-written brief for AI agents. It contains the current strategic state, locked decisions (Tauri, Python 3.12, HTMX), the v11.2 regression history, the v11.3 web-UI plan, known bugs to fix opportunistically, and Mario's working preferences. Read it before doing anything substantive — it overrides assumptions you'd otherwise make from the code alone.

## Local archive and reference library

As of 2026-05-17, nonessential local originals and reference material may be archived off the internal Mac drive:
- Primary T9 archive: `/Volumes/T9/AMGOS_Archive_2026-05-17`
- Clearable local originals backup: `/Volumes/T9/AMGOS_Archive_2026-05-17/06_Clearable_Local_Originals`
- Curated reference library on T9: `/Volumes/T9/AMGOS_Archive_2026-05-17/02_AMG_Reference_Library`
- Google Drive synced reference mirror: `/Users/mariorivera/Library/CloudStorage/GoogleDrive-triedtrue411@gmail.com/My Drive/AMGOS_Reference_Library_2026-05-17`
- Searchable manifests and transfer logs: `/Volumes/T9/AMGOS_Archive_2026-05-17/00_MANIFESTS` and `/Volumes/T9/AMGOS_Archive_2026-05-17/99_Logs`

If a historical backup, processing run, handoff, Cursor/Claude state file, or reference document is missing from its original home path, check those archive locations before treating it as lost. Do not assume archived folders are active working directories; copy back the specific file or folder needed for a restore.

Key takeaways that shape every change:
- **AMG OS is the NOW goal.** Read `docs/AMG_OS_NOW_PLAN_2026-05-17.md` before strategic or workflow work. `AMG_OS` is the internal business operating system for Amy and Mario now; `AMG_OS_NEXT` is later productization after internal workflows prove savings.
- **v11.1 is the known-good baseline.** v11.2 was built and rolled back (regressed cover quality). Do not reintroduce v11.2 as-is.
- **Test on scene 8** (`/Users/mariorivera/AMG_Processing/incoming/YasminaBrady/4 BG - bath teasing scene`) — 1080p, ~10 min, fastest iteration. Never iterate against `amg batch` on the full 10-scene set.
- **Single-scene processing only when iterating:** `amg process <path>`, not `amg batch`.
- **Mario's local config patches** in `amg/config.py` must be preserved across upgrades: `REQUIRE_2257_DOC=False`, `TIME_BUDGET_SOFT_PCT=0.75`, `TIME_BUDGET_HARD_PCT=1.50`, `TIME_BUDGET_ABSOLUTE_MAX=3600`.
- **No auto-uploads, no automation past Mario's click.** Amy left every blanket-authorization box in her intake unchecked. Default to maximum human-in-loop.

## Common commands

```bash
# Activate the venv first (Python 3.12 required — NOT 3.13/3.14, PyAV/Pillow wheel constraint)
source venv/bin/activate

# CLI (installed as `amg` console script via pyproject.toml)
amg verify                              # Health check: Ollama, env vars, Python deps, disk
amg version                             # Version + Ollama status
amg process <video-or-folder>           # Single scene end-to-end
amg process <path> --dry-run            # Run pipeline without saving outputs
amg batch <folder>                      # All scenes recursively (uses batch lock)
amg review <scene_id>                   # CLI human review form (replaced by web UI in v11.3)
amg ready <scene_id>                    # Per-platform distribution-ready check
amg find --genre swinger --min-score 8  # Search the processed-scene library
amg dashboard --days 30                 # Performance trends from batch summaries
amg analyze --days 7 --studio Yasmina   # Decision-log analysis
amg calibrate <studio>                  # Recompute studio thresholds from history
amg resume <scene_id>                   # Re-run a failed scene (currently full re-run)

# Tests (pytest configured in pyproject.toml — testpaths=["tests"])
pytest                                  # Whole suite
pytest tests/test_performer_code.py     # One file
pytest tests/test_v11_1_modules.py::TestTitleParsing::test_parse_clean_response  # One test
pytest -k "lesbian"                     # Pattern match

# Setup (idempotent — installs ffmpeg/Ollama, configures env vars in BOTH ~/.zshrc and launchctl)
./scripts/setup.sh
./scripts/setup.sh --check              # Verify state, don't change anything
```

The `amg` entry point is defined in `pyproject.toml` (`[project.scripts] amg = "amg.cli:main"`). Mario also sometimes has a shell alias on `amg` — if `amg version` does something weird, check `~/.zshrc` for an alias hijack.

## High-level architecture

The pipeline is a **10-phase scene processor** orchestrated by `amg/pipeline.py::process_scene`. Every CLI command and (eventually) the v11.3 web UI funnels into this single function. The phases must run in order; each respects a shared deadline computed from `TIME_BUDGET_HARD_PCT * video_duration_sec` (capped at `TIME_BUDGET_ABSOLUTE_MAX`). Phases are designed to be skippable when over deadline — the floor-enforcement cascade (Phase 9) backstops quality.

```
amg/
├── config.py          # Single source of truth for ALL tunables. Mario's patches live here.
├── cli.py             # Argparse dispatcher. Lazy-imports per-command modules.
├── pipeline.py        # process_scene() — the conductor. All phases live here.
├── ingest/            # Phase 1: studio detection, performer code parsing, title parsing
├── compliance/        # Phase 2: 2257 doc verification, audit log
├── video/             # Phases 3-4: ffprobe metadata, frame extraction, sharpness calibration
├── scoring/           # AI client (Ollama HTTP), prompt builder, response parser, parallel orchestrator, title generator
├── scanning/          # Phases 5-9: tiered scan, finish/buildup hunters, cluster expansion, fallback cascade
├── output/            # Phase 10: cover saving, enhancement, AITG contact sheet, decision log
├── review/            # CLI review form, distribution-readiness gate
├── library/           # Search across processed scenes, DVD compilation
├── learning/          # Decision-log analyzer, studio recalibrator, batch tracker, dashboard
└── utils/             # Logging, timing, error report formatting
```

### Important architectural invariants

- **`config.py` is the single source of truth.** No tunables hardcoded in submodules. Override hierarchy (later wins): defaults → `~/AMG_OS/config.yaml` → studio profile JSON → scene-level `.amg_config.json` → CLI flags. Studio profiles live in `data/studio_profiles/{name}.json` and are auto-created on first encounter.

- **Time budget is enforced by passing `deadline_sec` (an absolute Unix timestamp) into every scanning phase.** Phases check `time.time() < deadline` before AI calls and return early with an `aborted: True` flag. Don't add a phase that ignores the deadline.

- **The floor enforcement cascade (`scanning/fallback.py`) is the safety net.** It runs A→C→D fallbacks until `COVER_FLOOR` (15) is met. Quality flag (`GOOD` / `AI_GENERATED` / `REVIEW_NEEDED`) is propagated to the contact sheet so the operator sees which fallback fired.

- **Cluster expansion (`scanning/cluster.py`) mines neighborhoods around high-scoring frames.** Window/interval scales with score (see `CLUSTER_WINDOWS` in `config.py`). `CLAUDE_CODE_HANDOFF.md` flags this as currently too greedy in v11.2 — be cautious if widening it.

- **Parallel AI scoring (`scoring/orchestrator.py`)** is supposed to use `ThreadPoolExecutor` with `AI_PARALLEL_WORKERS=4` matching `OLLAMA_NUM_PARALLEL=4`. The handoff doc reports it may not actually parallelize in practice — verify with timing before claiming a speedup.

- **Decision log (`output/decision_log.py`)** writes one JSON per scene to `data/decision_logs/`. This is the substrate for `amg analyze`, `amg dashboard`, `amg calibrate`, and the future operator-feedback learning loop. Anything you add to the pipeline that's worth analyzing later belongs in the decision log.

- **`data/` is gitignored** (decision logs, studio profiles, performer registry, audit logs, batch summaries, source videos). Multi-Mac sync is **code only**; data stays local. Don't write code that assumes another Mac can see another Mac's `data/`.

### Required Ollama environment

The pipeline assumes these env vars are set (verified by `amg verify`, configured by `scripts/setup.sh` in BOTH `~/.zshrc` AND `launchctl` so the Ollama GUI app sees them):

```
OLLAMA_MLX=1               OLLAMA_FLASH_ATTENTION=1
OLLAMA_NUM_PARALLEL=4      OLLAMA_KV_CACHE_TYPE=q8_0
OLLAMA_KEEP_ALIVE=24h      OLLAMA_MAX_LOADED_MODELS=1
OLLAMA_CONTEXT_LENGTH=2560 OLLAMA_HOST=127.0.0.1:11434
```

Vision model: `qwen2.5vl:7b`. Don't switch the default model without running the bake-off harness against scene 8 first.

## Working with Mario

- He runs a real business; bullshit costs him time. Be direct, show concrete output (file lists, JSON, log lines) rather than claiming success.
- Run end-to-end before declaring done. "Mario's eye is the truth-teller, not the JSON" — covers that score 8.5 but look bad are a regression.
- `scripts/setup.sh` and any other shell scripts must be **bash 3.2 compatible** (macOS native bash). No `declare -A`, use parallel arrays.
- Mario uses zsh and is in Eastern time. Filenames containing `1.zip` get auto-linked by his terminal — warn him to rename or tab-complete.

## What NOT to do

- Don't reverse `REQUIRE_2257_DOC=False` in `config.py`. Mario tracks 2257s manually for now.
- Don't propose Anthropic/OpenAI/Google vision APIs (TOS-blocked for adult content).
- Don't add user auth, multi-user, deployment infra, or React/Vue/Svelte. v11.3 is HTMX + FastAPI + Jinja2, single-user, localhost-only.
- Don't auto-upload to platforms in any v11.x. That's v12 territory and Amy hasn't approved automation scope.
- Don't `amg batch` against the full incoming folder while iterating — use `amg process` on scene 8.
- Don't rewrite the v11.x pipeline core when building v11.3; wrap it.

## AMG file routing and archive policy

Before creating new non-code files, research saves, backup manifests, staging outputs, or Google Drive/T9 archive items, read:

`/Users/mariorivera/AMG_OS_NEXT/docs/runbook/claude_file_routing_and_archive_policy_2026-05-17.md`

Short version: temp/scratch files go to `/Users/mariorivera/Desktop/AMG_LOCAL_STAGING_DO_NOT_SYNC`; final synced archive files go to `/Users/mariorivera/Desktop/AMG_SYNC_TO_GOOGLE_DRIVE/MAC BACKUP FILES`; large durable backups go to `/Volumes/T9/AMGOS_Archive_2026-05-17`; repo code/docs stay in the relevant repo. Do not re-enable Desktop, Downloads, or Documents sync in Google Drive without operator approval.
