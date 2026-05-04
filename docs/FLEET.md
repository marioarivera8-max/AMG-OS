# AMG OS v11 — Fleet (Multi-Mac) Coordination

How v11 works across multiple Macs at AMG.

---

## What Syncs and What Doesn't

**Syncs (via Git):**
- All code in `amg/` package
- All scripts in `scripts/`
- All docs in `docs/`
- `requirements.txt`, `pyproject.toml`, `.gitignore`
- `config.yaml.template`

**Does NOT sync (explicitly excluded):**
- `data/` — all decision logs, studio profiles, performer registry, audit logs
- `venv/` — Python virtual environment
- Source video files (anywhere)
- Generated covers (anywhere)
- 2257 documents

Each Mac runs its own scenes. Each Mac maintains its own data. Code is
the only shared state.

---

## Onboarding a New Mac

```bash
# 1. Clone the repo
git clone <amg-os-repo-url> ~/AMG_OS
cd ~/AMG_OS

# 2. Run setup
./scripts/setup.sh

# 3. Open new terminal (env vars + alias)
# 4. Verify
amg verify

# 5. Start processing
amg batch /path/to/incoming/
```

Total time: ~10-15 minutes (most of it Ollama model download).

---

## Workflow Patterns

### Pattern 1: Single Operator, Multiple Macs

Mario has a primary Mac and a backup. Both clone same repo.

- Process scenes on whichever Mac is available
- Decision logs live on the Mac that did the work
- Code changes flow: edit on primary → commit/push → pull on backup
- No coordination needed for daily operations

### Pattern 2: Multiple Operators

When AMG hires Mario #2:

```
Mac A (Mario #1)     Mac B (Mario #2)
     │                    │
     ├─ data/             ├─ data/
     │   ├─ decision_logs ├─ decision_logs (separate)
     │   └─ studio_profs  └─ studio_profs (separate)
     │                    │
     └─── git push/pull (code only) ───┘
```

Each operator's decision logs are independent. To aggregate (e.g., "show
fleet-wide performance"):

- Today (v11.0): manually copy `data/decision_logs/` to a shared location
- Future (Phase 1+): automatic Sheets sync, opt-in

---

## Code Change Workflow

### Tweaking a Default

Mario notices Tier 1 floor of 654 is too aggressive for a specific studio.

```bash
# On primary Mac:
cd ~/AMG_OS
vim amg/config.py    # Change SHARPNESS_HARD_FLOOR or per-studio
git add amg/config.py
git commit -m "tune: lower Tier 1 floor for all studios"
git push

# On secondary Mac:
cd ~/AMG_OS
git pull
# That's it. Next batch picks up the change.
```

### Adding a Feature

```bash
# Branch for safety
git checkout -b feat/new-genre-detection

# Make changes
vim amg/ingest/title_parser.py
# Test
python -m pytest tests/

# Commit + merge
git add -A
git commit -m "feat: detect TENTACLE genre tag"
git checkout main
git merge feat/new-genre-detection
git push
```

### Bumping Dependencies

```bash
# Edit requirements.txt
vim requirements.txt
# (e.g., bump opencv-python to 4.11)

# Re-install on this Mac
source venv/bin/activate
pip install -r requirements.txt --upgrade

# Test
amg verify

# If good, commit
git add requirements.txt
git commit -m "deps: bump opencv-python to 4.11"
git push

# Other Macs:
cd ~/AMG_OS
git pull
source venv/bin/activate
pip install -r requirements.txt --upgrade
```

---

## Repository Structure

```
amg-os/                          ← Git repo
├── amg/                         ← All Python source
├── scripts/                     ← setup.sh, benchmark.sh, etc.
├── docs/                        ← All markdown docs
├── tests/                       ← Test suite
├── data/                        ← (gitignored) Local data
├── venv/                        ← (gitignored) Python venv
├── README.md
├── requirements.txt
├── pyproject.toml
├── .gitignore
└── config.yaml.template
```

---

## Recommended Branch Strategy

For a small team (1-3 operators):

- `main` — Production. Always working.
- Feature branches for any non-trivial change
- Direct commits to `main` for tweaks (config tuning, doc updates)

For a larger team:
- `main` — Production
- `develop` — Integration
- `feat/*` — Features
- `fix/*` — Bug fixes
- PR review before merge to `main`

---

## When Two Macs Diverge

If both Macs commit changes simultaneously:

```bash
# Mac A pushed first; Mac B has local changes
git pull --rebase
# Resolve conflicts if any
git push
```

For complex conflicts: communicate before committing. v11 is small enough
that conflicts are rare.

---

## Disaster Recovery

If a Mac dies:

1. New Mac clones repo from Git → all CODE recovered
2. Run `./scripts/setup.sh` → environment reproduced
3. **Decision logs are LOST** (they were local-only)

To prevent this:
- Time Machine backup includes `~/AMG_OS/data/`
- OR: weekly manual sync of `~/AMG_OS/data/decision_logs/` to external drive
- OR: rclone to encrypted cloud bucket (most paranoid option)

The setup script can configure these on request.

---

## Performance Across Macs

Each Mac's hardware affects throughput:

| Mac | Memory | Approx Speed |
|-----|--------|----------------|
| M4 Pro 24GB | High | 60-120 sec/scene |
| M4 24GB | Medium | 90-180 sec/scene |
| M3 Pro 18GB | Medium-low | 120-240 sec/scene |
| M2 16GB | Low | 240-480 sec/scene (no MLX) |
| Intel Mac | Low | Run on CPU only, 8-12x slower |

If a fleet has mixed hardware, route demanding scenes to the fastest Mac.
This is operator's call — v11 doesn't auto-route today.
