#!/usr/bin/env python3
"""Benchmark local VLM scoring engines on AMG scene frames.

This is intentionally local-only. It targets Ollama-compatible /api/chat
servers by default, but the api URL/model are flags so a pod can compare
Ollama against another local server without changing pipeline code.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import mean
from typing import Any

from amg.scoring.ai_client import AIClient
from amg.scoring.prompt import SYSTEM_PROMPT, build_scoring_prompt
from amg.video.metadata import get_metadata
from amg.video.reader import VideoReader


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    idx = int(round((len(vals) - 1) * pct / 100.0))
    return vals[max(0, min(idx, len(vals) - 1))]


def _sample_frames(video_path: Path, *, count: int, start_pct: float, end_pct: float) -> list[dict[str, Any]]:
    meta = get_metadata(video_path)
    if not meta:
        raise RuntimeError(f"could not read video metadata: {video_path}")
    duration = float(meta.get("duration_sec") or 0.0)
    if duration <= 0:
        raise RuntimeError(f"video duration is zero: {video_path}")

    lo = max(0.0, min(1.0, start_pct))
    hi = max(lo, min(1.0, end_pct))
    start = duration * lo
    end = duration * hi
    if count <= 1:
        timestamps = [(start + end) / 2.0]
    else:
        step = (end - start) / float(count - 1)
        timestamps = [start + i * step for i in range(count)]

    with VideoReader(video_path) as vr:
        frames = vr.get_frames_at(timestamps)
    return [
        {"timestamp_sec": ts, "frame": frame}
        for ts, frame in zip(timestamps, frames)
        if frame is not None
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path, help="Scene video to sample")
    parser.add_argument("--api-url", help="Ollama-compatible /api/chat URL")
    parser.add_argument("--model", help="Vision model name")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--count", type=int, default=24)
    parser.add_argument("--start-pct", type=float, default=0.05)
    parser.add_argument("--end-pct", type=float, default=0.95)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    frames = _sample_frames(
        args.video,
        count=max(1, args.count),
        start_pct=args.start_pct,
        end_pct=args.end_pct,
    )
    if not frames:
        raise RuntimeError("no frames sampled")

    prompt = build_scoring_prompt(
        primary_scene_type="STANDARD",
        detected_genres=[],
        performer_count=None,
        studio_language="en",
        studio_hint=None,
    )
    if args.api_url:
        client = AIClient(api_url=args.api_url, model=args.model)
    else:
        client = AIClient(model=args.model)
    workers = max(1, int(args.workers or 1))
    durations: list[float] = []
    successes = 0
    errors: dict[str, int] = {}

    def _score(entry: dict[str, Any]) -> dict[str, Any]:
        t0 = time.time()
        resp = client.score_frame(entry["frame"], prompt, system_prompt=SYSTEM_PROMPT)
        elapsed = time.time() - t0
        return {
            "timestamp_sec": entry["timestamp_sec"],
            "success": resp.success,
            "duration_sec": elapsed,
            "error_code": resp.error_code,
            "attempts": resp.attempts,
            "raw_len": len(resp.raw_text or ""),
        }

    wall0 = time.time()
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_score, frame) for frame in frames]
        for fut in as_completed(futures):
            row = fut.result()
            rows.append(row)
            durations.append(float(row["duration_sec"]))
            if row["success"]:
                successes += 1
            else:
                code = row.get("error_code") or "unknown"
                errors[code] = errors.get(code, 0) + 1
    wall = time.time() - wall0

    summary = {
        "video": str(args.video),
        "api_url": client.api_url,
        "model": client.vision_model,
        "workers": workers,
        "frames_sampled": len(frames),
        "successes": successes,
        "errors": errors,
        "wall_sec": round(wall, 3),
        "sum_call_sec": round(sum(durations), 3),
        "mean_call_sec": round(mean(durations), 3) if durations else 0.0,
        "p50_call_sec": round(_percentile(durations, 50), 3),
        "p95_call_sec": round(_percentile(durations, 95), 3),
        "parallelism_factor": round((sum(durations) / wall), 3) if wall > 0 else 0.0,
        "rows": rows,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print("Local VLM engine benchmark")
        print("=" * 80)
        print(f"model: {summary['model']}")
        print(f"api_url: {summary['api_url']}")
        print(f"frames: {summary['frames_sampled']}  workers: {workers}  successes: {successes}")
        print(
            f"wall: {summary['wall_sec']}s  mean_call: {summary['mean_call_sec']}s  "
            f"p95: {summary['p95_call_sec']}s  parallelism: {summary['parallelism_factor']}x"
        )
        if errors:
            print(f"errors: {errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
