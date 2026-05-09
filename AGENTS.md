# AGENTS.md

Cross-tool entry point for AI assistants in this repo.

## Read Order

1. `AGENT_CONTEXT_CURRENT.md` (live defaults and current direction)
2. `CLAUDE.md` (working rules and architecture invariants)
3. `docs/cloud_edition_runbook.md` (deployment and runtime ops)
4. `docs/RUNBOOK.md` (day-to-day cloud + local usage)
5. `docs/TROUBLESHOOTING.md` (recovery and debugging)

## Project Direction

AMG OS has two active operating modes:

- Cloud production (operator primary path): web UI on Hetzner controller, jobs on
  Runpod GPU pods.
- Local CLI development path: run scenes locally with `amg process`.

Treat both as valid, but use live runtime values from `AGENT_CONTEXT_CURRENT.md`
as the source of truth for operational defaults.

## Hard Rules

- Never propose closed API vision services for content analysis.
- Never change `REQUIRE_2257_DOC=False` in `amg/config.py`.
- Never auto-upload to downstream platforms.
- Keep operator-in-the-loop review as final quality decision.
- Avoid `amg batch` during iteration; test one scene/job at a time.

## Current Production Runtime Defaults

Mirror live controller env unless the operator explicitly changes it:

- `AMG_JOB_BACKEND=runpod`
- `AMG_PROCESSING_PROFILE=fast`
- `AMG_STREAMING_SCAN=1`
- `AMG_RUNPOD_VIDEO_BACKEND=ffmpeg_cuda`
- `AMG_VIDEO_HWACCEL=cuda`
- `AMG_GPU_CV_ENABLED=1`
- `AMG_GPU_CV_BACKEND=opencv_cuda`
- `AMG_GPU_DEDUP_ENABLED=1`
- `AMG_RUNPOD_OLLAMA_NUM_PARALLEL=6`
- `AMG_RUNPOD_AI_PARALLEL_WORKERS=6`
- `AMG_RUNPOD_IDLE_TERMINATE_SEC=900`
- `AMG_ENABLE_SCENE_INSIGHT=0`
- `AMG_ENABLE_PROVIDED_THUMBNAIL_SCORING=0`
- `AMG_SOFT_THUMB_ENABLED=0`

AMG_OS v1 baseline: H100 Runpod + `main-8b5749d` + ffmpeg-cuda decode
processed a 35m11s H264 scene in `140.45s` pipeline time and a 24m07s HEVC
scene in `130.15s` on a warm pod.

## Common Commands

```bash
# Local CLI
amg verify
amg version
amg process <video-or-folder>

# Tests
python -m pytest tests/test_stream_scan.py tests/test_job_backend.py

# Live controller
ssh -i "$HOME/.ssh/id_ed25519" root@5.161.231.249 "systemctl status amg-controller --no-pager"
ssh -i "$HOME/.ssh/id_ed25519" root@5.161.231.249 "journalctl -u amg-controller -n 120 --no-pager"
```

## Commit Discipline

- Prefer one clear intent per commit.
- Explain why in commit body, not only what changed.
- Validate before claiming complete.
