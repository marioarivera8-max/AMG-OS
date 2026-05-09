# AMG OS

AMG OS is a scene processing pipeline for adult VOD distribution workflows.

It supports two active paths:

- Cloud production path (operator primary): web UI on Hetzner controller,
  processing jobs on Runpod GPU pods.
- Local CLI path (development and spot verification): `amg process`.

## Core Outputs Per Scene

- ranked cover JPGs
- contact sheet
- decision log JSON

## Current Production Runtime (live baseline)

- Backend: `runpod`
- Processing profile: `fast`
- Streaming scan: enabled
- Video backend: `ffmpeg_cuda`
- Hardware acceleration: `cuda`
- GPU dedup: enabled
- Parallelism: 6/6 (`OLLAMA_NUM_PARALLEL`, `AMG_AI_PARALLEL_WORKERS`)
- Scene insight and provided-thumbnail scoring: disabled

AMG_OS v1 validated on 2026-05-09:

- 24m07s HEVC scene: `130.15s` warm-pod pipeline time
- 35m11s H264 scene: `140.45s` warm-pod pipeline time
- OpenCV CUDA is configured but currently falls back to CPU; the main v1 wins
  are ffmpeg-cuda decode, streaming scan, parallel Ollama, and warm pod reuse.

## Non-Negotiable Constraints

- Vision analysis must remain local-Ollama on trusted hardware.
- No closed API vision providers for analysis.
- No automatic platform uploads.
- Keep human review in the loop.
- Do not change `REQUIRE_2257_DOC=False` in `amg/config.py`.

## Quick Start (Local CLI)

```bash
amg verify
amg version
amg process <video-or-folder>
```

## Quick Start (Cloud Operator)

Open: https://amg.exoticplug.app

Submit one scene/job, review resulting covers in UI, and check decision log timing
for performance and quality.

## Testing

```bash
python -m pytest tests/test_stream_scan.py tests/test_job_backend.py
```

## Key Docs

- `AGENT_CONTEXT_CURRENT.md` (live authoritative context)
- `docs/cloud_edition_runbook.md` (deploy/runtime ops)
- `docs/RUNBOOK.md` (daily usage)
- `docs/TROUBLESHOOTING.md` (recovery)
