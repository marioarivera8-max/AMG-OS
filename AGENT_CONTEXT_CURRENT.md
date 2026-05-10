# AMG OS Current Agent Context

Last updated: 2026-05-10

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

From `/etc/amg/controller.env`, service state, and live deploy validation on
2026-05-10:

- `AMG_JOB_BACKEND=runpod`
- `AMG_RUNPOD_IMAGE=ghcr.io/marioarivera8-max/amg-pod:main-595ea4a`
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
- controller image pinned at `ghcr.io/marioarivera8-max/amg-controller:main-595ea4a`

## Latest Deployed Change

Commit `595ea4a` warms the pod vision model before accepting jobs:

- Controller and Runpod pod images are pinned at `main-595ea4a`.
- Pod `/readyz` now waits for a tiny real vision inference warmup, not just
  Ollama `/api/tags`, before the controller submits scene jobs.
- Controller readiness logs now surface the `vision-warmup` phase.
- This was added after a cold H100 pod accepted the first job with the model
  present but not inference-warm, causing `E_AI_TIMEOUT` on all stream AI calls
  and fallback D.

Current disk maintenance state:

- `amg clean` can dry-run or apply conservative AMG data cleanup.
- `/root/amg_deploy/scripts/controller_disk_maintenance.py` is installed on
  the Hetzner host for host-side Docker image tag pruning. It protects the
  running controller image and configured `AMG_RUNPOD_IMAGE`.
- One-time cleanup on 2026-05-10 reduced `/` from roughly 65% used to 50% used.
- The daily systemd cleanup timer is not installed yet; it needs explicit
  operator approval because it creates ongoing automated deletion.

2026-05-10 cold/warm pod observations:

- Cold H100 pod `q307t9htdqsi6n` took roughly 19 minutes before `/healthz`
  served; during that window Runpod already reported desiredStatus `RUNNING`.
- First cold scene `JPOV_0389...` completed with fallback D: pipeline `455.97s`
  for `1768.97s` source (`257.8 ms/video-sec`) because stream AI timed out.
- Second warm scene `JPOV_0375...` completed without fallbacks: pipeline
  `219.27s` for `1382.10s` source (`158.6 ms/video-sec`), with zero AI errors.

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
