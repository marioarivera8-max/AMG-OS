# AMG Cloud Edition Runbook

This runbook is for the live cloud production path.

## Production Architecture

```text
Browser (https://amg.exoticplug.app)
  -> Hetzner controller VM (FastAPI UI + dispatcher)
  -> Runpod GPU pod (Ollama + pipeline worker)
  -> cloud source storage via rclone
```

## Live Runtime Baseline (2026-05-10)

- controller host: `5.161.231.249`
- controller service: `amg-controller` active/running
- controller image: `ghcr.io/marioarivera8-max/amg-controller:main-9619578`
- pod image: `ghcr.io/marioarivera8-max/amg-pod:main-4f564ce`

Controller env defaults in production:

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

AMG_OS v1 live canary results:

- NG010 first canary: 24m07s, 1.98GB HEVC, pipeline `184.78s`
- NG010 warm rerun: 24m07s, 1.98GB HEVC, pipeline `130.15s`
- Y_B_004 warm run: 35m11s, 3.27GB H264, pipeline `140.45s`
- Runtime truth: `video_backend=ffmpeg-cuda+pyav`, `gpu_cv_mode=cpu`

## Standard Deploy Procedure

1. Build and publish images through GitHub Actions.
2. Update `/etc/amg/controller.env` with the new pod image tag if needed.
3. Ensure systemd unit references the matching controller image.
4. Restart controller service.
5. Run one short cloud scene and validate:
   - pod readiness
   - pipeline completion
   - visual cover quality

## Core Verification Commands

```bash
ssh -i "$HOME/.ssh/id_ed25519" root@5.161.231.249 "systemctl status amg-controller --no-pager"
ssh -i "$HOME/.ssh/id_ed25519" root@5.161.231.249 "journalctl -u amg-controller -n 200 --no-pager"
ssh -i "$HOME/.ssh/id_ed25519" root@5.161.231.249 "grep -E '^(AMG_JOB_BACKEND|AMG_RUNPOD_IMAGE|AMG_PROCESSING_PROFILE|AMG_STREAMING_SCAN|AMG_RUNPOD_VIDEO_BACKEND|AMG_VIDEO_HWACCEL|AMG_GPU_CV_ENABLED|AMG_GPU_CV_BACKEND|AMG_GPU_DEDUP_ENABLED|AMG_RUNPOD_OLLAMA_NUM_PARALLEL|AMG_RUNPOD_AI_PARALLEL_WORKERS|AMG_ENABLE_SCENE_INSIGHT|AMG_ENABLE_PROVIDED_THUMBNAIL_SCORING|AMG_SOFT_THUMB_ENABLED|AMG_RUNPOD_IDLE_TERMINATE_SEC)=' /etc/amg/controller.env"
```

## Disk Maintenance

The controller disk usually fills from old Docker image tags, especially pod
images. AMG data artifacts are normally much smaller, but stale
`cloud_submit_fallback/` folders can temporarily hold full source videos.

Inspect usage:

```bash
ssh -i "$HOME/.ssh/id_ed25519" root@5.161.231.249 "df -h && docker system df && du -xh --max-depth=1 /var/lib/amg/data | sort -h"
```

Dry-run AMG data cleanup from inside the controller image:

```bash
amg clean --dry-run
```

Apply safe transient cleanup:

```bash
amg clean --auto
```

Host-level cleanup, including old AMG Docker image tags, must run on the
Hetzner host:

```bash
cd /path/to/AMG_OS
python3 scripts/controller_disk_maintenance.py --json
python3 scripts/controller_disk_maintenance.py --apply --keep-docker-tags 2
```

Do not mount the Docker socket into the public web container just for cleanup;
use the host-side script or a root-owned systemd timer instead.

Optional daily timer on the Hetzner host:

```bash
cd /path/to/AMG_OS
sudo scripts/install_controller_disk_maintenance_timer.sh
```

## Operational Constraints

- No closed API vision services.
- No automatic upload workflows.
- Keep `REQUIRE_2257_DOC=False` unchanged in config.
- Prioritize visual quality validation before shipping speed changes.
