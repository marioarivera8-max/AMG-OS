# AMG OS Current Agent Context

Last updated: 2026-05-09

Release checkpoint: AMG_OS v1.

This is the active handoff for all agents. If another doc conflicts with this
file, trust this file.

## Project Direction

AMG OS supports two active paths:

1. Cloud production path (primary for operations):
   - UI: https://amg.exoticplug.app
   - Controller VM: Hetzner (`5.161.231.249`)
   - GPU worker: Runpod pod image
   - Vision: local Ollama on the GPU pod (no closed API vision)
   - Source ingest: cloud storage via rclone

2. Local CLI path (primary for development and spot validation):
   - `amg process <video-or-folder>`
   - same core pipeline and quality rules

## Live Runtime Baseline (authoritative)

From `/etc/amg/controller.env`, service state, and live canary validation on
2026-05-09:

- `AMG_JOB_BACKEND=runpod`
- `AMG_RUNPOD_IMAGE=ghcr.io/marioarivera8-max/amg-pod:main-8b5749d`
- `AMG_PROCESSING_PROFILE=fast`
- `AMG_STREAMING_SCAN=1`
- `AMG_RUNPOD_VIDEO_BACKEND=ffmpeg_cuda`
- `AMG_VIDEO_HWACCEL=cuda`
- `AMG_GPU_CV_ENABLED=1`
- `AMG_GPU_CV_BACKEND=opencv_cuda`
- `AMG_GPU_DEDUP_ENABLED=1`
- `AMG_RUNPOD_OLLAMA_NUM_PARALLEL=6`
- `AMG_RUNPOD_AI_PARALLEL_WORKERS=6`
- `AMG_ENABLE_SCENE_INSIGHT=0`
- `AMG_ENABLE_PROVIDED_THUMBNAIL_SCORING=0`
- `AMG_SOFT_THUMB_ENABLED=0`
- `AMG_RUNPOD_IDLE_TERMINATE_SEC=900`

Controller service:

- `amg-controller` active/running
- controller image pinned at `ghcr.io/marioarivera8-max/amg-controller:main-8b5749d`

## AMG_OS v1 Performance Baseline

Validated on one H100 Runpod pod with `fast` + streaming scan + ffmpeg-cuda
decode:

- NG010 first canary: 24m07s, 1.98GB HEVC, 15 covers, pipeline `184.78s`
- NG010 warm rerun: 24m07s, 1.98GB HEVC, 12 covers, pipeline `130.15s`
- Y_B_004 warm run: 35m11s, 3.27GB H264, 15 covers, pipeline `140.45s`

Observed runtime truth:

- `video_backend=ffmpeg-cuda+pyav`
- OpenCV CUDA still fell back to CPU (`gpu_cv_mode=cpu`)
- Biggest wins came from GPU decode, streaming scan, parallel Ollama, and warm
  pod reuse.

## Non-Negotiables

- Do not propose Anthropic/OpenAI/Google vision APIs for analysis.
- Do not change `REQUIRE_2257_DOC=False` in `amg/config.py`.
- No automatic platform upload flow.
- Keep human-in-loop review as final quality gate.
- Prefer one focused change per commit.
- While iterating, run one scene at a time (not broad `amg batch`).

## Quality and Performance Guardrails

- Known quality reference remains v11.1 behavior.
- Cover floor target is 15 (`COVER_FLOOR`).
- Fast-path currently enabled in production (`fast` + streaming + backend auto).
- Success is visual quality plus runtime, not runtime alone.

## Test/Validation Workflow

Use short scene first, then long scene only after short passes.

Minimum validation for pipeline/runtime changes:

```bash
python -m pytest tests/test_stream_scan.py tests/test_job_backend.py
```

For cloud deploy checks:

```bash
ssh -i "$HOME/.ssh/id_ed25519" root@5.161.231.249 "systemctl status amg-controller --no-pager"
ssh -i "$HOME/.ssh/id_ed25519" root@5.161.231.249 "journalctl -u amg-controller -n 120 --no-pager"
```

## What To Read Next

1. `AGENTS.md`
2. `CLAUDE.md`
3. `docs/cloud_edition_runbook.md`
4. `docs/RUNBOOK.md`
5. `docs/TROUBLESHOOTING.md`

Use older dated context docs only for history, not for active defaults.
