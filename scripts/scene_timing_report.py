#!/usr/bin/env python3
"""Summarize AMG timing for speed work.

Reads decision logs for pipeline phase timings and, when available, the UI
run-timing ledger for controller/pod timing (download, pipeline wait, and
pipeline execution). This is the first stop before changing performance
knobs: it tells us whether the current scene is decode-bound, AI-bound,
download-bound, or fallback-bound.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any


def _load_logs(root: Path, limit: int, scene_filter: str | None = None) -> list[tuple[float, Path, dict[str, Any]]]:
    rows: list[tuple[float, Path, dict[str, Any]]] = []
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            execution = data.get("execution") or {}
            if not execution.get("total_duration_sec"):
                continue
            scene_id = str(data.get("scene_id") or path.stem)
            if scene_filter and scene_filter.lower() not in scene_id.lower():
                continue
            rows.append((path.stat().st_mtime, path, data))
        except Exception:
            continue
    rows.sort(reverse=True, key=lambda x: x[0])
    return rows[:limit]


def _load_run_timings(path: Path) -> dict[str, dict[str, Any]]:
    by_scene: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return by_scene
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            scene_id = str(row.get("scene_id") or "")
            if scene_id:
                by_scene[scene_id] = row
    except Exception:
        return by_scene
    return by_scene


def _phase_duration(data: dict[str, Any], name: str) -> float:
    phase = (data.get("execution") or {}).get("phases", {}).get(name) or {}
    return float(phase.get("duration_sec") or 0.0)


def _fmt(sec: float | None) -> str:
    if sec is None:
        return "n/a"
    mins, rem = divmod(int(round(float(sec))), 60)
    return f"{mins}m{rem:02d}s"


def _stream_split(phase: dict[str, Any]) -> dict[str, float]:
    return {
        "decode": float(phase.get("decode_wall_sec") or 0.0),
        "cv": float(phase.get("cv_wall_sec") or 0.0),
        "ai": float(phase.get("ai_wall_sec") or 0.0),
    }


def _bottleneck_note(data: dict[str, Any], run_row: dict[str, Any] | None) -> str:
    phases = (data.get("execution") or {}).get("phases") or {}
    stream = phases.get("stream_scan") or {}
    split = _stream_split(stream)
    download = float((run_row or {}).get("pod_download_sec") or 0.0)
    fallback = _phase_duration(data, "floor_enforcement")
    output = _phase_duration(data, "output")
    parts = {
        "download": download,
        "decode": split["decode"],
        "cv": split["cv"],
        "ai": split["ai"],
        "fallback": fallback,
        "output": output,
    }
    name, value = max(parts.items(), key=lambda item: item[1])
    if value <= 0:
        return "need timing data"
    if name == "download":
        return "download-bound: prefetch/overlap next cloud job"
    if name == "decode":
        return "decode-bound: low-res pipe or segment-parallel scan"
    if name == "ai":
        return "AI-bound: raise parallelism or test batched/local VLM server"
    if name == "cv":
        return "CV-bound: move more analysis to smaller frames/GPU"
    if name == "fallback":
        return "fallback-bound: fix selector/prompt/gates before speeding up"
    return "output-bound: improve frame-cache hit rate or final extraction"


def _json_summary(logs: list[tuple[float, Path, dict[str, Any]]], run_rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for _, path, data in logs:
        scene_id = data.get("scene_id") or path.stem
        phases = (data.get("execution") or {}).get("phases") or {}
        stream = phases.get("stream_scan") or {}
        run_row = run_rows.get(str(scene_id), {})
        out.append(
            {
                "scene_id": scene_id,
                "decision_log": str(path),
                "total_sec": float((data.get("execution") or {}).get("total_duration_sec") or 0.0),
                "elapsed_sec": run_row.get("elapsed_sec"),
                "pod_download_sec": run_row.get("pod_download_sec"),
                "pod_pipeline_wait_sec": run_row.get("pod_pipeline_wait_sec"),
                "pod_pipeline_sec": run_row.get("pod_pipeline_sec"),
                "stream_scan": {
                    "duration_sec": float(stream.get("duration_sec") or 0.0),
                    "decode_wall_sec": float(stream.get("decode_wall_sec") or 0.0),
                    "cv_wall_sec": float(stream.get("cv_wall_sec") or 0.0),
                    "ai_wall_sec": float(stream.get("ai_wall_sec") or 0.0),
                    "submitted": stream.get("submitted"),
                    "completed": stream.get("completed"),
                    "parse_failed": stream.get("parse_failed"),
                    "score_zero": stream.get("score_zero"),
                    "segment_count": stream.get("segment_count"),
                    "low_res_analysis": stream.get("low_res_analysis"),
                },
                "bottleneck": _bottleneck_note(data, run_row),
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default="data/decision_logs", help="Decision-log directory")
    parser.add_argument("--run-timings", default="data/logs/run_timings.jsonl", help="UI run timing JSONL")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--scene", help="Only include scene ids containing this text")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable summary")
    args = parser.parse_args()

    logs = _load_logs(Path(args.root), args.limit, scene_filter=args.scene)
    if not logs:
        print(f"No decision logs found under {args.root}")
        return 1

    run_rows = _load_run_timings(Path(args.run_timings))
    if args.json:
        print(json.dumps(_json_summary(logs, run_rows), indent=2))
        return 0

    totals = []
    print("Recent scene timing")
    print("=" * 96)
    for _, path, data in logs:
        execution = data.get("execution") or {}
        outcomes = data.get("outcomes") or {}
        input_info = data.get("input") or {}
        phases = execution.get("phases") or {}
        total = float(execution.get("total_duration_sec") or 0.0)
        totals.append(total)
        scene_id = str(data.get("scene_id") or path.stem)
        run_row = run_rows.get(scene_id, {})
        stream = phases.get("stream_scan") or {}
        split = _stream_split(stream)

        print(f"\n{scene_id}")
        print(f"  processed: {data.get('timestamp_processed')}")
        print(
            "  source: "
            f"{_fmt(float(input_info.get('duration_sec') or 0.0))}, "
            f"{float(input_info.get('size_gb') or 0):.2f}GB, {input_info.get('resolution')}"
        )
        print(
            f"  total: {_fmt(total)}  elapsed: {_fmt(run_row.get('elapsed_sec'))}  "
            f"covers: {outcomes.get('covers_delivered')}  top: {outcomes.get('top_pick_score')}"
        )
        if run_row:
            print(
                "  pod: "
                f"download={_fmt(run_row.get('pod_download_sec'))} "
                f"pipeline_wait={_fmt(run_row.get('pod_pipeline_wait_sec'))} "
                f"pipeline={_fmt(run_row.get('pod_pipeline_sec'))}"
            )
        if stream:
            print(
                "  stream split: "
                f"decode={_fmt(split['decode'])} cv={_fmt(split['cv'])} ai={_fmt(split['ai'])} "
                f"submitted={stream.get('submitted')} completed={stream.get('completed')} "
                f"parse_failed={stream.get('parse_failed')} score_zero={stream.get('score_zero')} "
                f"segments={stream.get('segment_count', 1)} low_res={stream.get('low_res_analysis')}"
            )
        print(f"  read: {_bottleneck_note(data, run_row)}")

        for name, phase in sorted(
            phases.items(),
            key=lambda item: float((item[1] or {}).get("duration_sec") or 0.0),
            reverse=True,
        ):
            if not isinstance(phase, dict):
                continue
            duration = float(phase.get("duration_sec") or 0.0)
            if duration <= 0 and not phase.get("skipped"):
                continue
            suffix = []
            for key in ("aborted", "abort_reason", "final_count", "skipped", "reason"):
                if key in phase:
                    suffix.append(f"{key}={phase[key]}")
            print(f"    {name}: {_fmt(duration)} {' '.join(suffix)}")

    print("\nSummary")
    print(f"  scenes: {len(totals)}")
    print(f"  mean_total: {_fmt(mean(totals))}")
    print(f"  mean_stream_scan: {_fmt(mean(_phase_duration(data, 'stream_scan') for _, _, data in logs))}")
    print(f"  mean_tier_scan: {_fmt(mean(_phase_duration(data, 'tier_scan') for _, _, data in logs))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
