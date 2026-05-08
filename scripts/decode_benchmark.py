#!/usr/bin/env python3
"""Benchmark AMG video-reader decode/sample speed without running AI."""
from __future__ import annotations

import argparse
import subprocess
import os
import time
from pathlib import Path


def _probe_ffmpeg_nvdec(video: Path, seconds: int) -> dict:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-hwaccel",
        "cuda",
        "-hwaccel_output_format",
        "cuda",
        "-t",
        str(seconds),
        "-i",
        str(video),
        "-f",
        "null",
        "-",
    ]
    start = time.time()
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return {
        "backend": "ffmpeg-nvdec",
        "returncode": proc.returncode,
        "wall_sec": time.time() - start,
        "stderr": proc.stderr.strip()[-500:],
    }


def _run(video: Path, backend: str, interval: float, limit: int) -> dict:
    os.environ["AMG_VIDEO_BACKEND"] = backend
    # Import after setting env so reader picks up the requested backend.
    from amg.video.reader import VideoReader

    start = time.time()
    count = 0
    first_ts = None
    last_ts = None
    with VideoReader(video) as vr:
        backend_name = vr.backend_name
        duration = vr.duration_sec
        for ts, _frame in vr.iter_frames_sequential(0, duration, interval):
            count += 1
            first_ts = ts if first_ts is None else first_ts
            last_ts = ts
            if limit and count >= limit:
                break
    wall = time.time() - start
    return {
        "backend": backend,
        "backend_name": backend_name,
        "frames": count,
        "wall_sec": wall,
        "fps_sampled": count / wall if wall > 0 else 0,
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--backend", action="append", default=["pyav", "opencv"])
    parser.add_argument("--probe-nvdec", action="store_true", help="Also run an ffmpeg CUDA/NVDEC smoke benchmark")
    parser.add_argument("--nvdec-seconds", type=int, default=120)
    args = parser.parse_args()

    for backend in args.backend:
        result = _run(args.video, backend, args.interval, args.limit)
        print(
            f"{result['backend']} ({result['backend_name']}): "
            f"{result['frames']} frames in {result['wall_sec']:.2f}s "
            f"({result['fps_sampled']:.2f} sampled-fps), "
            f"range={result['first_ts']}..{result['last_ts']}"
        )
    if args.probe_nvdec:
        result = _probe_ffmpeg_nvdec(args.video, args.nvdec_seconds)
        status = "ok" if result["returncode"] == 0 else f"failed rc={result['returncode']}"
        print(f"ffmpeg-nvdec: {status} in {result['wall_sec']:.2f}s for first {args.nvdec_seconds}s")
        if result["stderr"]:
            print(result["stderr"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
