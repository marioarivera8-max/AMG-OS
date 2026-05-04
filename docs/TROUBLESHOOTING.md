# AMG OS v11 — Troubleshooting

Common issues and their fixes, organized by symptom.

---

## Setup / Installation Issues

### "Ollama not found" or `command not found: ollama`

```bash
# Install via Homebrew
brew install ollama
brew services start ollama
```

### "Python 3.11+ required"

```bash
brew install python@3.12
# Re-run setup
./scripts/setup.sh
```

### "Permission denied: ./scripts/setup.sh"

```bash
chmod +x scripts/*.sh
./scripts/setup.sh
```

### Setup script fails on `pip install`

Probably a system Python interfering. Force venv-only:

```bash
rm -rf venv
python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Runtime Issues

### "Ollama: not responding" in `amg verify`

```bash
# Check if it's running
brew services list | grep ollama

# If stopped, start it
brew services start ollama

# If running but not responding, restart
brew services restart ollama

# Verify port 11434 is listening
lsof -i :11434
```

### "Vision model not loaded"

```bash
ollama pull qwen2.5vl:7b
ollama list  # Should show qwen2.5vl:7b in the list
```

### Scoring is slow (>200s/scene)

Almost always missing env vars. Check:

```bash
# In a NEW terminal:
echo $OLLAMA_MLX               # Should be 1
echo $OLLAMA_FLASH_ATTENTION   # Should be 1
echo $OLLAMA_NUM_PARALLEL      # Should be 4
echo $OLLAMA_KV_CACHE_TYPE     # Should be q8_0
echo $OLLAMA_KEEP_ALIVE        # Should be 24h
echo $OLLAMA_CONTEXT_LENGTH    # Should be 2560
```

If any are wrong/empty:

```bash
# Re-run setup
./scripts/setup.sh

# Open a NEW terminal (env vars apply to new shells)
# Re-test
amg verify
```

If terminal env vars are correct but Ollama itself doesn't see them, the GUI app
may have started before launchctl was configured:

```bash
# Force restart
launchctl setenv OLLAMA_MLX 1
launchctl setenv OLLAMA_FLASH_ATTENTION 1
launchctl setenv OLLAMA_NUM_PARALLEL 4
launchctl setenv OLLAMA_KV_CACHE_TYPE q8_0
launchctl setenv OLLAMA_KEEP_ALIVE 24h
launchctl setenv OLLAMA_CONTEXT_LENGTH 2560
brew services restart ollama
```

### Pipeline aborts with `E_TIMEOUT_HARD`

A scene exceeded 50% of its duration in processing, or hit the 20-min absolute cap.

Causes:
- Very long scene (>40min) on slow Mac
- Ollama running without optimizations
- Disk I/O bottleneck (HDD source vs SSD)

Fixes:
1. `amg verify` — confirm optimizations are active
2. Check disk: source on SSD ideally
3. If genuinely too long, increase `TIME_BUDGET_ABSOLUTE_MAX` in config.yaml

### "Decord not available" warning

Optional dependency. v11 falls back to OpenCV (slower for cluster mining but works).

```bash
source venv/bin/activate
pip install decord==0.6.0
```

If install fails (Apple Silicon sometimes has issues), it's safe to ignore —
v11 still works without it.

---

## Output Issues

### Covers look over-processed / unnatural

Disable enhancement for one batch:

```bash
# Edit config.yaml:
output:
  enhance_default: false
```

Or per-scene via `.amg_config.json` next to the video:

```json
{ "output": { "enhance_default": false } }
```

### Top-pick score is consistently low (<7)

Either:
1. **Source quality** — older scenes, lower production value
2. **Genre mismatch** — AI doesn't recognize the scene type. Check filename has
   genre keywords (DP, GANGBANG, etc.) or studio profile has correct
   `default_genres`.
3. **Performer code missing** — without `27 BBGG -` style prefix, AI doesn't know
   how many performers to expect.

Check the decision log:

```bash
cat ~/AMG_OS/data/decision_logs/{scene_id}.json | jq '.input'
```

### Floor enforcement using Fallback D frequently

Fallback D = pure CV rescue (no AI). Means the AI is rejecting too many frames.

Possible causes:
1. **Source is genuinely difficult** — heavily clothed, dark, atypical content
2. **Studio profile wrong** — `default_genres` missing, prompt context insufficient
3. **Ollama returning empty/malformed responses** — check Ollama logs:
   ```bash
   tail -f /opt/homebrew/var/log/ollama.log
   ```

Run `amg analyze` and look at fallback usage. >5% suggests systemic issue.

---

## Data Issues

### Decision logs taking up space

```bash
du -sh ~/AMG_OS/data/decision_logs/

# Each log is ~5-15 KB. 1000 scenes ≈ 10MB. Don't worry about it.
# But if you really want to:
amg clean --decision-logs --older-than 90d
```

### Lost performer registry

```bash
# Auto-recreated on next scene processed.
# To manually rebuild from decision logs:
# (Phase 2 feature — coming in v11.1)
```

### Studio profile drifted

```bash
# Recompute from accumulated history:
amg calibrate YasminaBrady

# Or reset entirely (will rebuild on next scene):
rm ~/AMG_OS/data/studio_profiles/YasminaBrady.json
```

---

## Multi-Mac Issues

### Git push rejected

```bash
# Pull first
git pull --rebase
git push
```

### Different results on different Macs

Check both have:
1. Same Ollama version: `ollama --version`
2. Same model: `ollama list | grep qwen2.5vl`
3. Same env vars: `amg verify`
4. Same code: `git log -1`

Identical inputs + identical config = identical outputs (within Ollama's
~1% determinism noise from temperature=0.1).

### Decision log conflicts in Git

You shouldn't see these — `data/` is in `.gitignore`. If you do:

```bash
# Verify gitignore
cat .gitignore | grep "data/"

# If data/ is being tracked:
git rm -r --cached data/
git commit -m "stop tracking data/"
git push
```

---

## When All Else Fails

```bash
# Full reset (preserves source content, removes processing state):
cd ~/AMG_OS
rm -rf venv
rm -rf data/logs
rm -f data/.batch.lock
./scripts/setup.sh

# Then verify
amg verify
```

If still broken, check:
1. macOS version (Sonoma 14.0+ recommended for MLX)
2. Ollama version (≥0.19 for MLX support)
3. Disk space (`df -h .`)
4. Memory pressure (`vm_stat | head -5`)

The decision log of the failing scene will reveal the exact phase and error code.
