# AMG OS v11 — Architecture

This document explains the WHY behind v11's design.
For HOW to use it, see `RUNBOOK.md`.
For WHAT failed and how to recover, see `TROUBLESHOOTING.md`.

---

## Design Philosophy

1. **Predictable over clever** — Time budgets, floor enforcement, deterministic scoring
2. **Fail loud, then fall back** — Errors surfaced immediately, but cascade prevents work loss
3. **Capture everything** — Every scene's full decision log persists for learning
4. **Local-first** — Zero cloud, zero telemetry, all data on operator's Mac
5. **Replaceable parts** — Every module is one file you can rewrite if needed

---

## The Pipeline (10 Phases)

### Phase 1: Inventory
- Detect studio from path components (KNOWN_STUDIOS in `studio_profiles.py`)
- Parse performer code from filename (`27 BBGG - couple swap`) → 2M/2F
- Parse title for genre tags (DP, ANAL, GANGBANG, etc.)
- Derive primary scene type (FOURSOME from BBGG, GANGBANG from BBBBG, etc.)
- Auto-create studio profile if studio is new

### Phase 2: Compliance
- Look for 2257 documentation (2257.pdf, id.pdf, files matching `*2257*`)
- Block scene processing if missing AND `REQUIRE_2257_DOC=true`
- Audit log records the verification

### Phase 3: Metadata
- ffprobe for duration, fps, resolution, codec, file size, audio presence
- Reject scenes < 60 sec (likely truncated/test files)

### Phase 4: Calibration (NEW in v11)
- Sample 100 frames evenly across video duration
- Measure sharpness via Laplacian variance
- Set Tier 1 floor at 75th percentile, Tier 2 at 50th, Tier 3 at 25th
- This adapts to source quality automatically (4K HEVC vs 1080p MP4)
- Updates studio profile's calibration history

### Phase 5: Tiered Scan (cascade)
- **Tier 1**: 1fps sampling, strict thresholds. Fast. Often sufficient.
- **Tier 2**: 0.5fps sampling, looser thresholds. Triggered if Tier 1 < 3 candidates.
- **Tier 3**: 0.25fps, lenient. Final escalation.
- Each tier: dedupe candidates → AI score in parallel → check minimum

### Phase 6: Finish Hunter
- Scans last 20% of video at 1fps
- Top N (8) by sharpness sent to AI
- Targets money shots / climactic moments

### Phase 7: Buildup Hunter
- Scans 25%-50% zone at 0.5fps
- Top N (6) by sharpness sent to AI
- Targets tease/anticipation moments (often great covers)

### Phase 8: Cluster Expansion (score-triggered)
- For each high-scoring frame, sample neighbors:

| Score | Window | Interval | Samples |
|-------|--------|----------|---------|
| 5-7.9 | ±2s | 1s | 4 |
| 8-8.9 | ±5s | 1s | 10 |
| 9-9.9 | ±15s | 3s | 10 |
| 10.0 | ±30s | 5s | 12 |

- Excellence clusters in time. This mines neighborhoods around hits.

### Phase 9: Floor Enforcement (fallback cascade)
If accumulated candidates < 10:
- **Fallback A**: Take frames that scored 3.0-5.0 from already-scored pile
- **Fallback B**: (deferred to v11.1)
- **Fallback C**: 30 random frames + simplified prompt
- **Fallback D**: Pure CV (sharpness + skin dominance + face count) — emergency rescue

Floor of 10 is **never** violated for delivery; quality flag indicates which fallback fired.

### Phase 10: Output
- Re-extract frames at full resolution (analysis used 640x360 downsamples)
- Apply auto-enhancement (+10% sat, +5% contrast, +15% sharp, brightness if dark)
- Save with descriptive filenames: `01_Yasmina_BBGG_FINISH_Direct_9.0_14m24s.jpg`
- Build AITG contact sheet (2000px wide, 3-column grid, color-coded quality flag)
- Write decision log JSON to `data/decision_logs/`
- Audit log entry

---

## Time Budget Enforcement

Three concentric guardrails:

```
Soft warning  ──┐
                ├── 25% of video duration (logged, not blocking)
                ↓
Hard abort    ──┐
                ├── 50% of video duration (graceful exit, partial output)
                ↓
Absolute cap  ──┐
                └── 20 minutes (hard kill regardless of duration)
```

Per-phase timeouts also prevent any one phase from monopolizing budget:
- Calibration: 60s max
- Each tier scan: 240-300s max
- Finish/buildup hunters: 180s max
- Cluster: 180s max
- Fallback cascade: 300s max

---

## Why Modular?

v10.3 was a single 1613-line file. v11 splits into 35+ modules:

**Benefits:**
- Each file < 400 lines — readable, testable, replaceable
- Test individual modules without spinning up full pipeline
- Easy to swap implementations (e.g., new AI client without touching scoring logic)
- Multi-developer-friendly (when AMG grows beyond Mario)
- Future v12+ extensions slot in cleanly

**Trade-off:** more files to navigate. Mitigated by `__init__.py` exports
and clear module boundaries.

---

## The Decision Log

Every scene's processing produces one JSON file at
`data/decision_logs/{scene_id}.json` capturing:

- **Input**: video metadata, studio, performer code, scene type, genres
- **Execution**: per-phase timings, calibration values, error codes
- **Outcomes**: covers delivered, score distribution, top pick details, fallbacks used
- **Resource usage**: AI call count, retries, frame extraction count
- **Human feedback** (placeholder): operator's eventual selections, platform performance

These logs are NEVER auto-deleted. They form the corpus for:

- `amg analyze` — performance trends
- `amg calibrate <studio>` — recompute thresholds from history
- v11.1+ learning features (operator feedback, per-studio prompt adaptation)
- Forensic analysis (why did this scene fail?)
- Fleet-wide aggregation (multi-Mac coordination, future)

---

## Multi-Mac Coordination (Git-based)

```
[Mac A] ────┐
            │ git push (code only)
            ▼
        [Git Repo]
            ▲
            │ git pull (code only)
[Mac B] ────┘

Each Mac maintains its own:
  - data/decision_logs/ (local processing history)
  - data/studio_profiles/ (local calibration state)
  - Source videos
  - Generated covers
```

If operators want to share data (Phase 1+ of AMG OS),
that goes through explicit Sheets/Drive sync, not Git.

---

## Apple Silicon Optimizations (Why So Fast)

| Optimization | Mechanism | Speedup |
|---|---|---|
| OLLAMA_MLX=1 | Ollama uses Apple's MLX framework instead of llama.cpp | **1.93x** |
| OLLAMA_FLASH_ATTENTION=1 | Memory-efficient attention | 1.15x |
| OLLAMA_NUM_PARALLEL=4 | 4 concurrent inference slots in Ollama | 1.6x |
| OLLAMA_KV_CACHE_TYPE=q8_0 | 8-bit KV cache (halves memory, ~no quality loss) | 1.1x |
| OLLAMA_KEEP_ALIVE=24h | Model stays in memory across batches | Saves 30s/batch reload |
| OLLAMA_CONTEXT_LENGTH=2560 | Right-sized for our prompts (smaller = faster) | 1.1x |

**Critical detail**: Ollama GUI app does NOT read `~/.zshrc`. Setup script uses
`launchctl setenv` to ensure GUI-launched Ollama also gets these vars.

---

## Memory Budget (M4 Pro 24GB)

```
Total system memory:           24 GB
macOS reserves:                ~7 GB
Other apps (Photoshop etc.):   ~2 GB
Available:                     ~15 GB

qwen2.5-vl:7b model:           ~6 GB
KV cache (4 parallel × 2560):  ~0.05 GB × 4 = 0.2 GB
Headroom:                      ~8.8 GB
```

Plenty of room. v11 will not paginate or swap.

---

## Failure Modes

The pipeline is designed to degrade gracefully:

| Failure | Recovery |
|---------|----------|
| Ollama crashes mid-batch | Per-call retry (3x with backoff). Then graceful batch pause. |
| Single AI call malformed | Mark frame as parse fail, continue. |
| Source video corrupt | Reject early (E_SOURCE_UNREADABLE), continue batch. |
| Tier 1/2/3 finds 0 candidates | Cascade to fallback automatically. |
| All phases yield <10 covers | Fallback C (random + simplified) → Fallback D (pure CV). |
| Time budget exceeded | Hard abort, decision log records partial state. |
| Disk space low | Pause batch with clear instructions. |
| Concurrent batch attempt | File lock prevents — second invocation refuses. |

Errors are codes, not crashes. Every code maps to recovery action in
`config.py` `ERROR_CODES`.
