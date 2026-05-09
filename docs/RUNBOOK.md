# AMG OS Runbook

Operational runbook for both cloud production and local CLI workflows.

## Environment Scope

- Cloud production: controller at `https://amg.exoticplug.app`, jobs dispatched to
  Runpod.
- Local development: `amg` CLI on developer machine.

Use cloud defaults from `AGENT_CONTEXT_CURRENT.md` as authoritative.

## Cloud Daily Operations

1. Open the UI and submit one scene/job.
2. Watch job state and pod readiness in controller logs if needed.
3. Review resulting covers and contact sheet visually.
4. Confirm decision log metrics (`decode_wall_sec`, `cv_wall_sec`, `ai_wall_sec`)
   when evaluating performance work.

## Local Daily Operations

```bash
amg verify
amg process <video-or-folder>
```

Use single-scene iteration during tuning.

## Live Controller Checks

```bash
ssh -i "$HOME/.ssh/id_ed25519" root@5.161.231.249 "systemctl status amg-controller --no-pager"
ssh -i "$HOME/.ssh/id_ed25519" root@5.161.231.249 "journalctl -u amg-controller -n 120 --no-pager"
```

## Live Runtime Defaults (Cloud)

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

AMG_OS v1 measured warm-pod pipeline baseline:

- 24m07s HEVC scene: `130.15s`
- 35m11s H264 scene: `140.45s`

## Fast Rollback Switches

If a GPU migration step regresses quality or stability, disable only the
affected layer:

- Decode rollback: `AMG_RUNPOD_VIDEO_BACKEND=auto`
- GPU CV rollback: `AMG_GPU_CV_ENABLED=0`
- GPU dedup rollback: `AMG_GPU_DEDUP_ENABLED=0`

## Guardrails

- No auto-upload flows.
- No closed API vision services.
- Preserve `REQUIRE_2257_DOC=False` in `amg/config.py`.
- Validate quality visually before calling a speed change successful.
