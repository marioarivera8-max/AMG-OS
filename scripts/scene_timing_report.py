#!/usr/bin/env python3
"""Summarize recent AMG decision-log timing for speed work."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any


def _load_logs(root: Path, limit: int) -> list[tuple[float, Path, dict[str, Any]]]:
    rows: list[tuple[float, Path, dict[str, Any]]] = []
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            execution = data.get("execution") or {}
            if not execution.get("total_duration_sec"):
                continue
            rows.append((path.stat().st_mtime, path, data))
        except Exception:
            continue
    rows.sort(reverse=True, key=lambda x: x[0])
    return rows[:limit]


def _phase_duration(data: dict[str, Any], name: str) -> float:
    phase = (data.get("execution") or {}).get("phases", {}).get(name) or {}
    return float(phase.get("duration_sec") or 0.0)


def _fmt(sec: float) -> str:
    mins, rem = divmod(int(round(sec)), 60)
    return f"{mins}m{rem:02d}s"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        nargs="?",
        default="data/decision_logs",
        help="Decision-log directory",
    )
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    logs = _load_logs(Path(args.root), args.limit)
    if not logs:
        print(f"No decision logs found under {args.root}")
        return 1

    totals = []
    print("Recent scene timing")
    print("=" * 80)
    for _, path, data in logs:
        execution = data.get("execution") or {}
        outcomes = data.get("outcomes") or {}
        input_info = data.get("input") or {}
        phases = execution.get("phases") or {}
        total = float(execution.get("total_duration_sec") or 0.0)
        totals.append(total)
        scene_id = data.get("scene_id") or path.stem
        print(f"\n{scene_id}")
        print(f"  processed: {data.get('timestamp_processed')}")
        print(f"  source: {_fmt(float(input_info.get('duration_sec') or 0.0))}, {input_info.get('size_gb', 0):.2f}GB, {input_info.get('resolution')}")
        print(f"  total: {_fmt(total)}  covers: {outcomes.get('covers_delivered')}  top: {outcomes.get('top_pick_score')}")
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
            for key in ("aborted", "abort_reason", "frames_extracted", "ai_scored_count", "final_count", "skipped", "reason"):
                if key in phase:
                    suffix.append(f"{key}={phase[key]}")
            print(f"    {name}: {_fmt(duration)} {' '.join(suffix)}")

    print("\nSummary")
    print(f"  scenes: {len(totals)}")
    print(f"  mean_total: {_fmt(mean(totals))}")
    print(f"  mean_tier_scan: {_fmt(mean(_phase_duration(data, 'tier_scan') for _, _, data in logs))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
