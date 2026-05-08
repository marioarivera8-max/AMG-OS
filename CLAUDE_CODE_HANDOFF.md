# AMG OS — Context Handoff for Claude Code

> **Archive note (2026-05-08):** this is historical context from the
> local-first era. Read `AGENT_CONTEXT_CURRENT.md` first for the current
> cloud-hosted production state, live image tags, validation status, and next
> steps. This file is still useful for business background and the v11.2
> regression story, but its local-only/Tauri/GPU-rental assumptions are
> superseded where they conflict with the current handoff.

**Written:** 2026-05-04 (early morning, post-v11.2 regression)
**Operator:** Mario Rivera
**Owner:** Amy (AMG founder)
**Hardware:** Mario's M4 Pro MacBook, 24GB unified memory, macOS Tahoe

This document is a complete catch-up for the next AI agent to pick up the AMG OS project. Read top to bottom. The "First Task" section at the end is the immediate next step.

---

## What AMG Is

**AMG = Amy's adult VOD distribution business.** B2B only — AMG sells/licenses scenes to platforms (AEBN, SLR, Adult Empire, Faphouse, Dutch Linear, Proximus SVOD, etc.), not directly to consumers. AMG does not run payment processing, does not create content. They aggregate, process, retitle, package, and distribute.

**Mario's role:** sole content processor, tactical operator, business partner to Amy.

**Amy's role:** founder, holds creator relationships, strategic direction. She's deliberately stepping back from daily ops. Her stated goal in the recent intake doc: "Aggressive automation, eventually. Auto-pilot for most things. Mario reviews exceptions and quality samples weekly."

**Read this for full context:** `/Users/mariorivera/Downloads/amy_intake_fillable__1_.pdf` (Amy's intake questionnaire, just received). Critical sections: 1.1 (2257 reality), 4.1 (revenue distribution — note 85% unaccounted for in named platforms), 5.4 (Amy's role), 6.3 (automation comfort), 8.1/8.2 (approvals — left unchecked).

---

## What AMG OS Is

A local-first Python pipeline that automates the "watch a 4K HEVC scene → pick the best 15 cover frames → name and save them" workflow that Mario was previously doing manually in Photoshop. End goal: a cross-platform desktop app that processes deliveries, picks covers, generates titles, manages compliance docs, and uploads to platforms — with Mario in the loop on judgment calls and Amy supervising via reports.

### Strategic decisions already locked

| Decision | Choice | Why |
|---|---|---|
| AI architecture | Local-only (Ollama + qwen2.5vl on M4 Pro) | Adult content blocked from Anthropic/OpenAI/Google vision APIs. Cloud GPU rental is a v12+ option, not now. |
| App framework | Tauri (Rust + web UI) | Cross-platform binaries (Mac/Windows/Linux) when Amy/contractors need it. Small footprint vs Electron. |
| Build order | v11.x pipeline → v11.3 web UI (FastAPI + HTMX) → v11.4 Tauri wrapper → v12 multi-user | Web UI work transfers cleanly into Tauri. Proven path. |
| Python | 3.12 (NOT 3.13/3.14) | PyAV, Pillow, and other binary wheels exist for 3.12. Mario already has venv set up. |
| Frontend stack | HTMX + FastAPI for v11.3 | Server-rendered, simpler than React, transfers into Tauri unchanged |
| GPU rental | Deferred to v12+ | Solo workflow doesn't need it. Layer in later when scaling. |
| Cross-platform priority | HIGH | Amy/contractors using Windows likely within 8 weeks |
| Single-scene processing for testing | Yes | Use `amg process <path>`, NOT `amg batch` |

### The roadmap

- **v11.x (current)** — CLI pipeline, single-user, Mario's machine
- **v11.3 (next, 2-4 weeks)** — Web UI on localhost. FastAPI + HTMX. Reuses v11.x pipeline as-is, adds browser-based review.
- **v11.4 (2-3 weeks after v11.3)** — Tauri wrapper. Same web UI, native binaries, cross-platform.
- **v12+ (later)** — Multi-user, server-hosted, optional GPU rental. Only when AMG scales beyond solo.

---

## Current State

### Installed and working: v11.1 at `~/AMG_OS`

Mario rolled back to v11.1 from a v11.2 attempt that regressed cover quality. v11.1 is the known-good baseline. Verified output:

- `amg version` → `AMG OS v11.1.0`
- `amg verify` → all green
- Python 3.12 venv at `~/AMG_OS/venv`
- Ollama running with qwen2.5vl:7b
- All env vars correctly set (OLLAMA_MLX=1, OLLAMA_NUM_PARALLEL=4, etc.)

**Mario's local config patches (preserve these in any future install):**
```python
# In ~/AMG_OS/amg/config.py
REQUIRE_2257_DOC = False     # 2257 tracked manually for now
TIME_BUDGET_SOFT_PCT = 0.75  # bumped from 0.25
TIME_BUDGET_HARD_PCT = 1.50  # bumped from 0.50
TIME_BUDGET_ABSOLUTE_MAX = 3600  # bumped from 1200
```

### Test data on disk

`~/AMG_Processing/incoming/YasminaBrady/` contains 10 source videos. Two are heavily tested:

- **Scene 1** (`10 BGG - threesome in shower with sara`): 4K HEVC, 14:44, 3.17GB. v11.1 produced 31 covers in 22:40. Quality good but DUAL hallucinated on every cover.
- **Scene 8** (`4 BG - bath teasing scene`): 1080p HEVC, 10:34, 0.86GB. v11.1 produced 19 covers in 3:46. Quality good per Mario's eye.

v10.3 outputs also exist in each scene folder under `*_amg_v10_3/` for comparison.

### v11.2 attempt (FAILED, do not use as baseline)

Built tonight. Shipped as `amg_os_v11_2.zip` (in `/Users/mariorivera/Downloads/` if not deleted). Includes:
- PyAV with VideoToolbox hardware decode replacing OpenCV (✅ works)
- Reduced DUAL bonus + tightened prompt (⚠️ partial — DUAL still hallucinated on most covers)
- Save-time perceptual-hash dedup (✅ works but too aggressive)
- AI counter aggregation fix (✅ works — `ai_calls_total: 58`, `frames_extracted: 2434`)
- Bake-off harness, upgrade script, etc.

**Why it failed:** decode got faster but exposed sequential AI scoring as the new bottleneck (24 AI calls took 2:35 sequential despite OLLAMA_NUM_PARALLEL=4 — parallel orchestrator may not actually parallelize). Wider tier_2 sweep (555 candidates vs v11.1's 50) flooded the AI with bad options, and the AI picked worse ones — middle-of-action shots with heads cut off, motion blur. Mario's read: "covers seem worse honestly. more mid movement with head cut off."

Also has bugs:
- Duplicate filename numbering (multiple `01_*`, `02_*` etc.) — output dir wasn't cleared between runs
- Marks scenes as "INCOMPLETE / Unknown error" even when they produce 9 covers
- Final cover count (9) doesn't match files in covers dir (19) — dedup deduped fresh saves but didn't clean stale ones

**The PyAV decode work and counter fix are good and should be reused.** The dedup needs to be much less aggressive. The prompt fix needs to go further. The cluster expansion needs to be capped.

---

## Real Bugs to Fix in v11.3 (from v11.2 testing)

These are well-defined patches that should be carried into v11.3 work:

1. **DUAL gaze hallucination** — qwen2.5vl rubber-stamps DUAL on most covers. v11.1 prompt: B1b bonus +3.5. v11.2 reduced to +2.5 — still hallucinating. Either reduce to +2.0 (no bonus over single, may make AI lose interest in the tier entirely), OR remove the tier as a scoring bonus and use it only as a label, OR test a different vision model. Bake-off harness in v11.2 was built for exactly this purpose — never run.

2. **Score compression** — covers cluster at exactly 8.5. AI is finding the rubric ceiling. Related to #1.

3. **Sequential AI scoring** — `score_frames_parallel()` exists but doesn't appear to actually parallelize. Verify Ollama is serving 4 concurrent requests (check `OLLAMA_NUM_PARALLEL` is honored and ThreadPoolExecutor in `amg/scoring/orchestrator.py` is firing). v11.2 saw 24 calls in 155s = ~6.5 sec/frame which suggests sequential.

4. **resource_usage counters always 0** — fixed in v11.2, code is good. Reuse the global thread-safe counter pattern.

5. **Output directory not cleared between runs** — causes duplicate filename numbers when same scene reprocessed. Pipeline should wipe `*_amg_v11/covers/` before writing.

6. **"INCOMPLETE / Unknown error" reporting** — pipeline misclassifies successful runs as failures when cover count is below floor but >0. Should report partial success with non-blocking warning.

7. **Cluster expansion too greedy** — v11.2 expanded 30 seeds to 170 frames, flooded AI with low-quality candidates. Either tighter expansion windows, or filter expansions through CV gate before scoring.

---

## v11.3 — Web UI Build (THE MAIN PROJECT)

This is what Claude Code should build. 2-4 weeks of work.

### Architecture

```
Browser at localhost:8080
    ↓ HTTP/WebSocket
FastAPI server (new)
    ↓ Python imports
v11.x pipeline (existing, unchanged)
```

### Stack

- **Backend:** FastAPI, uvicorn, WebSocket for progress, Pydantic for models
- **Frontend:** HTMX + Tailwind CSS, server-rendered Jinja2 templates. NOT React. NOT a SPA. Server-rendered HTML with HTMX swaps.
- **Why HTMX:** simpler than React, fewer moving parts, no build step, transfers cleanly into Tauri later
- **Templating:** Jinja2 (FastAPI native)
- **Static assets:** served from FastAPI

### Pages needed (v11.3 scope)

1. **Library** (`/`) — Scene grid with thumbnails, status badges (NEW / PROCESSED / REVIEWED / UPLOADED), filter by studio/creator, search by title. Shows what's in `~/AMG_Processing/incoming/` plus what's been processed.

2. **Scene detail** (`/scene/<id>`) — Per-scene view with metadata, processing status, "Process Now" button, link to review.

3. **Review** (`/scene/<id>/review`) — REPLACES THE CLI REVIEW. Shows all generated covers in a grid, click to select top picks (1-15), enter title and description, choose platforms. Submit → saves to decision log + reviewed status.

4. **Cover picker** (modal or page) — Visual grid of all generated covers with score + tags, keyboard shortcuts (1-9 to favorite, arrows to navigate), zoom on hover.

5. **Dashboard** (`/dashboard`) — Recent batches, scenes processed per day, error patterns, AI call volume, storage stats.

6. **Settings** (`/settings`) — Vision model selection, time budgets, paths, environment variables.

### Backend endpoints

- `GET /api/scenes` — list with filters
- `GET /api/scenes/<id>` — detail
- `POST /api/scenes/<id>/process` — trigger processing (returns job_id)
- `WS /api/jobs/<job_id>` — WebSocket for real-time progress
- `GET /api/scenes/<id>/covers` — list covers for review
- `POST /api/scenes/<id>/review` — save review decisions
- `GET /api/dashboard` — metrics
- `GET /api/settings` — read config
- `POST /api/settings` — update config

### Single-command launcher

```bash
amg ui  # starts server, opens browser
```

Implementation: new CLI subcommand in `amg/cli.py` that:
1. Verifies Ollama is running (call `amg verify` internals)
2. Starts uvicorn on localhost:8080
3. Opens default browser to localhost:8080
4. Stays foreground (Ctrl+C to stop)

### What v11.3 does NOT need to do

- Multi-user / authentication (single-user, localhost-only)
- Production deployment / TLS / domain (localhost only)
- Mobile-responsive (Mario uses MacBook)
- React or any SPA framework
- Kubernetes / Docker / cloud anything
- Modify the v11.x pipeline core (only call into it from FastAPI)

### Approval gates (CRITICAL — Amy intake guidance)

Amy left every blanket-authorization checkbox UNCHECKED in Section 8 of her intake. Translation: **default the UI to maximum human-in-loop.** No auto-uploads. Every action requires explicit Mario click. Even auto-status-updates (which she said are fine) should be visibly logged for review.

The web UI should make it OBVIOUS what's automated and what isn't. Status bar showing "[Auto-pilot OFF — Mario approves all actions]" or similar.

---

## Repository Layout

```
~/AMG_OS/                   # The install (v11.1)
├── amg/
│   ├── cli.py              # CLI entry points (process, batch, review, etc.)
│   ├── config.py           # All tunables. Mario's patches live here.
│   ├── pipeline.py         # process_scene() orchestrates phases
│   ├── ingest/             # inventory, performer codes, studio profiles, title parsing
│   ├── compliance/         # 2257 checks, audit log
│   ├── video/              # reader, frames, faces, dedup, metadata
│   ├── scoring/            # ai_client, parser, prompt, orchestrator, title_generator
│   ├── scanning/           # tiered, cluster, finish_hunter, buildup_hunter, fallback
│   ├── output/             # covers, contact_sheet, decision_log, enhance
│   ├── review/             # form, distribution_gate
│   ├── library/            # search, dvd_compile
│   ├── learning/           # analyzer, calibrator, recorder, dashboard
│   └── utils/              # logging, timing
├── data/
│   ├── decision_logs/      # one JSON per processed scene
│   ├── batch_summaries/    # per-batch JSON
│   ├── studio_profiles/    # per-studio config (mostly empty)
│   └── ...
├── docs/
│   ├── ARCHITECTURE.md
│   ├── RUNBOOK.md
│   ├── TROUBLESHOOTING.md
│   ├── REVIEW_WORKFLOW.md
│   └── LEARNING.md
├── scripts/
│   ├── setup.sh            # bash 3.2 compatible (macOS native bash)
│   └── ...
├── venv/                   # Python 3.12 environment
├── requirements.txt
└── pyproject.toml

~/AMG_Processing/           # OLD v10.3 working dir (kept as safety net)
└── incoming/YasminaBrady/  # SOURCE VIDEOS (10 scenes)
    └── <scene-folder>/
        ├── *.mov           # the source video
        ├── *_amg_v10_3/    # v10.3 outputs (covers, contact sheet, decision log)
        └── *_amg_v11/      # v11.1 outputs (covers, contact sheet)
```

---

## Important Quirks

1. **macOS bash is 3.2.** Don't use `declare -A` or other bash 4+ features in shell scripts. Use parallel arrays.

2. **Mario's terminal auto-converts `1.zip` to clickable links.** Filenames containing `1.zip` get mangled into `[1.zip]` when typed in commands. Tell Mario to rename or use tab-completion.

3. **Mario uses zsh, not bash.** Aliases in `~/.zshrc`. Watch for shell aliases hijacking `amg` command (he had `alias amg="cd ~/AMG_Processing && source venv/bin/activate"` earlier that intercepted `amg version`).

4. **Mario is in Miami Beach, Eastern time.** Doesn't matter for code but affects "when's a reasonable time to ping back" if doing async work.

5. **The 2257 compliance check is OFF in Mario's config.** Don't reverse this. Mario tracks 2257 manually for now. Amy's intake confirms this is genuinely complicated and not a "just check a box" problem.

6. **Performer codes (BG, BBGG, BBBBBBG) only fit ~3 of 12 creators.** Yasmina Brady uses them. Most other creators (Naughty America, Hussie Pass, BlondeHexe, Vince Karter, etc.) don't. The v11.x pipeline assumes performer codes are available, which is why it works on YasminaBrady test scenes but would need per-creator ingest profiles for the broader catalog. v11.5+ work.

7. **Adult content vision API restrictions are real.** Anthropic, OpenAI, and Google vision APIs all prohibit adult content per usage policies. Local Ollama is the ONLY path. Don't propose closed-API solutions.

8. **Mario has 12 creators in the catalog** but only YasminaBrady scenes are in `incoming/` for testing. The other creators' content is on various Mega/Dropbox/FTP/Google Drive locations per the intake doc Section 2.

---

## v10.3 → v11.1 → v11.2 History (Brief)

- **v10.3:** Original pipeline. Worked but had bugs (BBGG misclassified as SOLO, JSON serialization errors silently dropping decision logs, 1-cover output on some scenes).
- **v11.1:** Fixed scene type classification, added eye contact tier (B1a/B1b/B1c), fixed JSON serialization, added review/find/dvd-compile/dashboard CLI commands. **Quality good per Mario's eye.** DUAL labels are wrong but covers themselves are usable.
- **v11.2:** Attempted PyAV speedup + prompt tightening + dedup. Regressed cover quality. Rolled back.

---

## First Task for Claude Code

**Build v11.3 from scratch.** Do NOT carry forward v11.2 code blindly — it has the regression bugs above. Cherry-pick the specific patches that were correct (PyAV reader, counter instrumentation pattern, save-time dedup CONCEPT but with much looser threshold). Test against scene 8 (1080p, fastest iteration) before scene 1 (4K, slow).

### Concrete steps

1. **Verify v11.1 is working:**
   ```bash
   cd ~/AMG_OS && source venv/bin/activate && amg verify && amg version
   ```
   Should show v11.1.0, all green.

2. **Read the existing pipeline to understand structure:**
   - `amg/cli.py` — CLI entry points
   - `amg/pipeline.py` — process_scene orchestration
   - `amg/scoring/orchestrator.py` — where parallel AI scoring lives (verify it actually parallelizes in v11.3)
   - `amg/scoring/prompt.py` — current scoring rubric
   - `amg/output/covers.py` — cover saving

3. **Read Amy's intake** at `/Users/mariorivera/Downloads/amy_intake_fillable__1_.pdf` for full strategic context. Pay attention to Sections 1.1, 4.1, 5.4, 6.3, 8.

4. **Run scene 8 through v11.1 to establish a fresh baseline:**
   ```bash
   amg process "/Users/mariorivera/AMG_Processing/incoming/YasminaBrady/4 BG - bath teasing scene"
   ```
   Save the decision log and contact sheet for comparison.

5. **Plan v11.3 architecture in writing** before coding. Discuss with Mario. Specifically: which parts of v11.x stay untouched, which API endpoints map to which pipeline calls, what the page hierarchy is, how WebSocket progress is wired.

6. **Implement v11.3** in this rough order:
   - FastAPI scaffold + `amg ui` launcher (smallest viable thing that runs)
   - Library page (just list scenes from filesystem)
   - Scene detail page
   - Trigger processing endpoint (call `amg process` as subprocess or import `process_scene` directly)
   - WebSocket progress
   - Review page (replaces CLI review)
   - Visual cover picker
   - Dashboard
   - Settings

7. **Concurrent v11.3.x maintenance work:** fix the v11.2 bugs in the v11.x pipeline as they're encountered:
   - Verify parallel scoring actually parallelizes
   - Reduce DUAL bonus further or remove the tier
   - Clear output dir before writing
   - Fix INCOMPLETE/UNKNOWN error reporting
   - Tame cluster expansion

8. **Run the bake-off** once v11.3 has a stable scoring path. The `scripts/bake_off.py` script in v11.2 is a starting point but needs the v11.3 code as a target. Bake-off models: qwen2.5vl:7b (current), minicpm-v:8b, internvl, qwen2.5vl:32b (4-bit). Mario picks winner by eyeball, not benchmark.

### Anti-goals

- Don't rewrite v11.x pipeline. Wrap it.
- Don't add cloud anything (closed-API vision is blocked for adult content).
- Don't add user auth, multi-user, deployment infra.
- Don't auto-upload to platforms in v11.3. That's v12 territory.
- Don't use React/Vue/Svelte. HTMX only.
- Don't introduce new bugs while fixing old ones — TEST against scene 8 between every meaningful change.

---

## Communication Style With Mario

- Direct. He knows his business. Don't over-explain domain stuff.
- Honest when something fails. He's running a real business; bullshit costs him time.
- Show concrete output (file lists, JSON, log lines) over claims.
- One question at a time when asking for input.
- Run things end-to-end before claiming success.
- He works late. Has been responsive at all hours during this session.
- Single-scene processing for testing — never batch the whole 10-scene set when iterating.
- Scene 8 (`4 BG - bath teasing scene`) is the canonical test scene because it's fastest (1080p, 10 min duration, 0.86GB).

## Communication Style With Amy

You probably won't talk to Amy directly. Mario is the interface. Amy gets reports — eventually. v11.3+ should produce digestible status that Mario can forward to Amy without editing.

---

## Files Worth Reading on Disk

In rough priority order:

1. `~/AMG_OS/amg/__version__.py` — confirms which version is installed
2. `~/AMG_OS/amg/cli.py` — what commands exist
3. `~/AMG_OS/amg/pipeline.py` — main orchestration
4. `~/AMG_OS/amg/scoring/prompt.py` — current scoring rubric
5. `~/AMG_OS/amg/config.py` — every tunable
6. `~/AMG_OS/docs/ARCHITECTURE.md` — design intent
7. `~/AMG_OS/data/decision_logs/*.json` — see what scenes have been processed
8. `/Users/mariorivera/Downloads/amy_intake_fillable__1_.pdf` — strategic context

Probably-still-on-disk artifacts from this session (delete if Mario wants):
- `/Users/mariorivera/Downloads/amg_os_v11_2.zip` — failed v11.2 attempt
- `/Users/mariorivera/Downloads/v11_2_install_preflight.md`
- `~/AMG_OS_v11_2/` — unzipped v11.2 directory (also failed)
- `~/AMG_OS_v11_1_backup_*` — v11.1 backup, may have been used to roll back

---

## TL;DR

- v11.1 is the known-good baseline. v11.2 regressed; rolled back.
- Strategic direction: local AI, Tauri app, web UI first.
- Next big project: v11.3 web UI (FastAPI + HTMX) wrapping existing pipeline.
- Don't auto-upload anything. Amy hasn't approved automation scope.
- Fix the v11.2 bugs (DUAL hallucination, sequential scoring, cluster greed) along the way.
- Test on scene 8 first, every iteration. Don't batch-process.
- Mario's eye is the truth-teller, not the JSON.

Welcome to AMG. Don't break the only known-good version.
