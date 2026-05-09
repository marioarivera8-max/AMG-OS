#!/usr/bin/env python3
"""GPU migration timing report.

Compares decision-log timing splits across two cohorts:
- baseline: runs where GPU CV/dedup were disabled or fallback backend was used
- gpu: runs where ffmpeg-cuda backend and GPU CV mode were active

This script is intentionally read-only and relies on decision-log fields so it
can run on controller snapshots without extra infra.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any


def _iter_logs(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in sorted(root.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            rows.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return rows


def _stream_phase(row: dict[str, Any]) -> dict[str, Any]:
    return ((row.get("execution") or {}).get("phases") or {}).get("stream_scan") or {}


def _is_gpu_run(stream: dict[str, Any]) -> bool:
    backend = str(stream.get("video_backend") or "")
    cv_mode = str(stream.get("gpu_cv_mode") or "")
    return backend == "ffmpeg-cuda+pyav" and cv_mode == "gpu"


def _mean(vals: list[float]) -> float:
    return float(mean(vals)) if vals else 0.0


def _fmt(sec: float) -> str:
    mins, rem = divmod(int(round(sec)), 60)
    return f"{mins}m{rem:02d}s"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("decision_logs", nargs="?", default="data/decision_logs")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    rows = _iter_logs(Path(args.decision_logs))[: max(1, args.limit)]
    if not rows:
        print("No decision logs found.")
        return 1

    baseline: list[dict[str, Any]] = []
    gpu: list[dict[str, Any]] = []
    for r in rows:
        s = _stream_phase(r)
        (gpu if _is_gpu_run(s) else baseline).append(r)

    def collect(group: list[dict[str, Any]], key: str) -> list[float]:
        out: list[float] = []
        for r in group:
            s = _stream_phase(r)
            out.append(float(s.get(key) or 0.0))
        return out

    def total(group: list[dict[str, Any]]) -> list[float]:
        out: list[float] = []
        for r in group:
            out.append(float((r.get("execution") or {}).get("total_duration_sec") or 0.0))
        return out

    print("GPU Migration Timing Report")
    print("=" * 72)
    print(f"scenes considered: {len(rows)}")
    print(f"baseline cohort:   {len(baseline)}")
    print(f"gpu cohort:        {len(gpu)}")
    print("")
    for label, group in (("baseline", baseline), ("gpu", gpu)):
        if not group:
            continue
        d = _mean(collect(group, "decode_wall_sec"))
        c = _mean(collect(group, "cv_wall_sec"))
        a = _mean(collect(group, "ai_wall_sec"))
        t = _mean(total(group))
        print(f"[{label}]")
        print(f"  mean_total:  {_fmt(t)}")
        print(f"  mean_decode: {_fmt(d)}")
        print(f"  mean_cv:     {_fmt(c)}")
        print(f"  mean_ai:     {_fmt(a)}")
        print("")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
