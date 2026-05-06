# AGENTS.md

Cross-tool entry point for AI assistants working in this repo (Cursor,
Claude Code, Aider, Continue, etc.). Read this first.

## What this project is

AMG OS — Python pipeline that processes adult VOD scenes for Amy's B2B
distribution business. Replaces a manual Photoshop "pick ~15 cover frames
per scene" workflow.

As of **2026-05-06** the system runs as a **cloud-hosted edition** —
controller VM on Hetzner + on-demand RTX 4090 pods on Runpod, accessible
to the operator at https://amg.exoticplug.app . The local-first v11.x
pipeline still works (`amg process <video>` from the Mac venv) but is
no longer the production path.

The pipeline is local-Ollama-on-the-pod by design (qwen2.5vl on the
GPU). Anthropic / OpenAI / Google vision APIs prohibit adult content,
so cloud GPU + local Ollama on that GPU is the only viable path.

## Read these before doing substantive work

In order of importance for getting current:

1. **`TOMORROW.md`** — current state, what's open, where the operator
   wants to go next. Updated at the end of each session. Read this
   first to know what's already done and what's contemplated.
2. **`AGENT_CONTEXT_2026-05-06_CLOUD_EDITION.md`** — comprehensive
   handoff for the cloud edition (Hetzner + Runpod + GHA). Architecture,
   bug graveyard from the cutover session, recovery procedures.
3. **`SESSION_NOTES_2026-05-06.md`** (or the latest dated equivalent)
   — conversational context that the git log + commit messages don't
   capture (rejected proposals, misdiagnoses, design discussions).
4. **`docs/cloud_edition_runbook.md`** — the deployment runbook
   (Hetzner setup, Caddy + Let's Encrypt, GHCR, systemd, secrets).
5. **`docs/WINDOWS_G14_SETUP.md`** — only relevant if you're on the
   Windows G14 machine and need to recreate dev environment context.
6. **`CLAUDE.md`** — standing project guide written for AI agents.
   Architecture, conventions, what NOT to do. Originally written
   for Claude Code but the content is tool-agnostic.
7. **`CLAUDE_CODE_HANDOFF.md`** — the operator's strategic brief.
   Business context, locked decisions, v11.2 regression history, what's
   deferred and why. Pre-dates the cloud pivot but the working
   agreements still apply.

The 2026-05-04-era docs (`AGENT_CONTEXT_2026-05-04_LATEST.md`,
`SESSION_NOTES_2026-05-04.md`) describe local-only training/scoring
work. That work is paused but valid — read those if Mario wants to
resume that branch.
5. **`README.md`** — user-facing setup + usage. Read this if you need
   to know how `amg verify` works or which env vars Ollama needs.

## Hard rules (apply tool-agnostically)

- **Never propose closed-API vision** (Anthropic / OpenAI / Google
  vision). Adult content prohibited per their policies. Local Ollama
  is the only path.
- **Never touch `REQUIRE_2257_DOC` in `amg/config.py`.** The operator
  tracks 2257 docs manually for now.
- **Never auto-upload to platforms.** Amy hasn't approved automation
  scope. Default UI posture is maximum human-in-loop.
- **Test on scene 4 first** for short iteration (1080p, 10:34, 0.9GB
  at `~/AMG_Processing/incoming/YasminaBrady/4 BG - bath teasing scene`).
  Scene 7 (BBBBBBG, 46:47, 4.4GB) is the long-form stress test. Avoid
  `amg batch` while iterating — it processes all 10 scenes (~3 hours).
- **One change per commit.** No bundling. Recent example of why: v11.2
  bundled PyAV decode + prompt changes + dedup changes + bake-off
  harness, regressed cover quality, the whole bundle got rolled back
  even though the decode work was sound.
- **The operator's eye is the only quality metric that ships.** Numbers
  (cover counts, scores, cluster cap firings) inform; they don't decide.

## Common commands

```bash
source venv/bin/activate

amg verify                     # health check: Ollama, env vars, deps
amg version                    # current version + Ollama status
amg process <video-or-folder>  # single scene end-to-end
amg batch <folder>             # all scenes recursively (~3h on full set)

pytest                         # full suite (67 tests as of v11.1.4)

# Bake-off harness — multi-model comparison (~2.5h with default 2 scenes)
python scripts/bake_off.py
python scripts/bake_off.py --models qwen2.5vl_3b minicpm-v_8b
python scripts/bake_off.py --skip-pull   # if models already cached
```

## Backend selection (v11.1.3+)

```bash
AMG_VIDEO_BACKEND=pyav    amg process ...   # default — fast on most files
AMG_VIDEO_BACKEND=opencv  amg process ...   # fallback — use if PyAV hangs

# Swap vision model without editing config:
AMG_VISION_MODEL_OVERRIDE=qwen2.5vl:3b  amg process ...
```

## High-level architecture

10-phase pipeline orchestrated by `amg/pipeline.py::process_scene`:

1. Inventory (studio, performer code, title)
2. Compliance (2257 check — currently skipped per operator config)
3. Metadata (ffprobe)
4. Calibration (sharpness floors per source)
5. Tiered scan (Tier 1 strict → 2 loose → 3 lenient)
6. Finish hunter (last 20% of video)
7. Buildup hunter (25-50% zone)
8. Cluster expansion (mine neighborhoods of high-scoring frames)
9. Floor enforcement (fallback cascade A→C→D)
10. Output (covers, contact sheet, decision log)

`amg/config.py` is the single source of truth for tunables. AI client
honors `AMG_VISION_MODEL_OVERRIDE` env var (added for the bake-off
harness in commit cd38015).

## Working agreement

- Match the operator's commit-message style: explain WHY in the body,
  reference issues observed empirically, link decisions to data.
- Direct + honest. Operator runs a real business; vague output costs
  him time.
- Show concrete output (file lists, JSON, log lines) over claims.
- One question at a time when asking for input.
- Run things end-to-end before claiming success.
