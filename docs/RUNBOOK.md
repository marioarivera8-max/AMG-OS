# AMG OS v11 — Operations Runbook

Quick reference for daily operations.

---

## Daily Workflow

```bash
# 1. Drop new scenes into incoming
mv ~/Downloads/27\ BBGG*  ~/AMG_Processing/incoming/

# 2. Process the batch
amg batch ~/AMG_Processing/incoming/

# 3. Review covers + contact sheets
open ~/AMG_Processing/incoming/27\ BBGG*/27_BBGG_amg_v11/
```

---

## Common Commands

```bash
amg verify              # Health check (run if anything seems off)
amg status              # What's been happening recently
amg process <path>      # Single scene
amg batch <path>        # Multiple scenes in folder
amg analyze             # Last 30 days of performance
amg analyze --days 7    # Last week only
amg calibrate Yasmina   # Recompute Yasmina's thresholds from history
amg performers          # List performer registry
amg version             # Version + Ollama status
amg clean               # Cleanup old work directories
```

---

## Adding a New Studio

You don't need to. v11 auto-creates studio profiles when it encounters a new one.

If you want to customize:

```bash
# Profile auto-created at:
# ~/AMG_OS/data/studio_profiles/{StudioName}.json

# Edit it directly to set:
# - default_genres
# - common_scene_types
# - language preferences
# - banned terms
```

Then next batch picks up the changes.

---

## What "Floor Met" Means

v11 ALWAYS delivers ≥10 covers per scene. The contact sheet shows a quality flag:

- **GOOD** — Normal pipeline succeeded, no fallbacks
- **AI_GENERATED** — Fallback C used (simplified prompt). Review covers carefully.
- **REVIEW_NEEDED** — Fallback D used (pure CV rescue). Some covers may be subpar.

If you see REVIEW_NEEDED, the scene was unusual — maybe a compilation, a long
talking-head segment, or a corrupt source. Check the decision log:

```bash
cat ~/AMG_OS/data/decision_logs/{scene_id}.json | jq .
```

---

## When Ollama Misbehaves

```bash
# Symptom: "Ollama: not responding" in `amg verify`
brew services restart ollama

# Symptom: Model not loaded
ollama pull qwen2.5vl:7b

# Symptom: Slow scoring (>200s/scene)
amg verify  # Look for missing env vars
# If env vars wrong, re-run setup
./scripts/setup.sh

# Nuclear option: restart from scratch
brew services stop ollama
sleep 2
brew services start ollama
amg verify
```

---

## When v11 Hangs

Ctrl+C is safe — pipeline cleans up its lock file. Your batch resumes wherever
you re-run it (skipping already-processed scenes by default).

If something is REALLY stuck:

```bash
# Find the running process
ps aux | grep "amg.cli"

# Kill it (note the PID)
kill <PID>

# Remove stale lock if needed
rm ~/AMG_OS/data/.batch.lock
```

---

## Multi-Mac Workflow

```bash
# After making code changes on Mac A:
cd ~/AMG_OS
git add -A && git commit -m "tweak: adjust Tier 1 floor for BlondeHexe"
git push

# On Mac B:
cd ~/AMG_OS
git pull
# That's it — code synced. Data stays local on each Mac.
```

If you change `requirements.txt`:

```bash
# On each Mac:
source venv/bin/activate
pip install -r requirements.txt
```

---

## Reviewing Covers

After processing, the work directory contains:

```
27 BBGG - couple swap_amg_v11/
├── 00_YasminaBrady_contact_sheet.jpg   ← Open this first
└── covers/
    ├── 01_Yasmina_BBGG_FINISH_Direct_9.5_14m24s.jpg   ← Top pick
    ├── 02_Yasmina_BBGG_PENETRATION_Direct_9.0_8m12s.jpg
    ├── 03_Yasmina_BBGG_NUDE_Direct_8.5_3m24s.jpg
    ├── ...
    └── 12_Yasmina_BBGG_BUILDUP_Averted_7.0_5m48s.jpg
```

Filenames encode everything you need:
- `01` = rank (highest score first)
- `Yasmina_BBGG` = performer + code
- `FINISH` = scene type / origin tier
- `Direct` = gaze direction
- `9.5` = AI score
- `14m24s` = position in source video

---

## Performance Targets

After a few batches, run `amg analyze` and check:

| Metric | Target | What if missing |
|---|---|---|
| Avg per scene | 60-120 sec | Check `amg verify`, env vars |
| Avg covers per scene | 11-13 | Normal — extras above floor |
| Avg top-pick score | 8.0+ | Quality of source content |
| Floor compliance | 100% | Always 100% — non-negotiable |
| Fallback usage | <10% | Higher = unusual content batch |

---

## When to Recalibrate

After **50+ scenes** for a single studio, recalibration may improve speed:

```bash
amg calibrate YasminaBrady
```

This computes the studio's actual Tier 1 threshold from real history and
updates the profile. Future scenes from that studio will use the refined value.

Don't bother for new studios with <20 scenes — defaults work fine.
