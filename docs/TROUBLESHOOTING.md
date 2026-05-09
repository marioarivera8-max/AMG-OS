# AMG OS Troubleshooting

Use this for active cloud and local issues. Prioritize direct evidence from logs
and current runtime env.

## 1) Controller Is Up But Jobs Stall

Check service and recent logs:

```bash
ssh -i "$HOME/.ssh/id_ed25519" root@5.161.231.249 "systemctl status amg-controller --no-pager"
ssh -i "$HOME/.ssh/id_ed25519" root@5.161.231.249 "journalctl -u amg-controller -n 200 --no-pager"
```

Confirm controller env still matches live baseline (`runpod`, `fast`, streaming on,
backend `ffmpeg_cuda`, 6/6 workers, GPU CV enabled).

## 2) Pod Ready Delays

- verify image tag exists in GHCR and matches controller env
- check if pod is pulling model/image cold
- retry with one scene to isolate startup vs pipeline time

If readiness loops while pod state is running, inspect controller log for endpoint
probe failures and stale status behavior.

## 3) Processing Too Slow

Use decision log timing split first:

- `decode_wall_sec`
- `cv_wall_sec`
- `ai_wall_sec`

Then tune the dominant stage instead of broad changes.

Also confirm stream metadata:

- `video_backend` (expect `ffmpeg-cuda+pyav` on cloud)
- `gpu_cv_mode` / `gpu_cv_backend`

## 4) Quality Regressed (blurry or weak covers)

- verify sharpness gates and fallback path from decision log
- verify parse/score success counters in stream scan output
- compare against known good short-scene visual baseline

Do not ship speed changes if visual quality regresses.

If regression appears after GPU migration, rollback one layer at a time:

- set `AMG_GPU_DEDUP_ENABLED=0`
- then `AMG_GPU_CV_ENABLED=0`
- then `AMG_RUNPOD_VIDEO_BACKEND=auto`

## 5) Local CLI Health Check

```bash
amg verify
amg version
python -m pytest tests/test_stream_scan.py tests/test_job_backend.py
```

## 6) Hard Policy Checks

If proposed fix violates any of these, reject it:

- no closed API vision services
- no auto-upload automation
- do not alter `REQUIRE_2257_DOC=False`
