"""
Timing threshold calibration from UI run timing ledger.

Uses percentile bands per phase:
  green <= p50
  yellow <= p75
  red > p90
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


def percentile(values: List[float], p: float) -> Optional[float]:
    """Linear-interpolated percentile in [0,1]."""
    if not values:
        return None
    arr = sorted(float(v) for v in values)
    if len(arr) == 1:
        return arr[0]
    idx = (len(arr) - 1) * max(0.0, min(1.0, float(p)))
    lo = int(idx)
    hi = min(lo + 1, len(arr) - 1)
    w = idx - lo
    return arr[lo] * (1.0 - w) + arr[hi] * w


def load_phase_durations(run_timings_path: Path) -> Dict[str, List[float]]:
    """
    Read data/logs/run_timings.jsonl and return {phase: [duration_sec, ...]}.
    """
    by_phase: Dict[str, List[float]] = {}
    if not run_timings_path.exists():
        return by_phase

    with open(run_timings_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            phase_map = row.get("phase_durations_sec") or {}
            if not isinstance(phase_map, dict):
                continue
            for phase, raw_sec in phase_map.items():
                try:
                    sec = float(raw_sec)
                except (TypeError, ValueError):
                    continue
                if sec <= 0:
                    continue
                by_phase.setdefault(str(phase), []).append(sec)
    return by_phase


def calibrate_phase_thresholds(
    run_timings_path: Path,
    min_samples: int = 5,
) -> List[dict]:
    """
    Build thresholds rows sorted by descending p75 duration.
    """
    phase_map = load_phase_durations(run_timings_path)
    out: List[dict] = []
    for phase, vals in phase_map.items():
        if len(vals) < max(1, int(min_samples)):
            continue
        p50 = percentile(vals, 0.50)
        p75 = percentile(vals, 0.75)
        p90 = percentile(vals, 0.90)
        out.append(
            {
                "phase": phase,
                "count": len(vals),
                "green_max_sec": round(p50 or 0.0, 1),
                "yellow_max_sec": round(p75 or 0.0, 1),
                "red_over_sec": round(p90 or 0.0, 1),
                "max_sec": round(max(vals), 1),
            }
        )
    out.sort(key=lambda r: r["yellow_max_sec"], reverse=True)
    return out


def format_thresholds_markdown_table(rows: List[dict]) -> str:
    if not rows:
        return (
            "| Phase | Green | Yellow | Red | Samples |\n"
            "|---|---:|---:|---:|---:|\n"
            "| *(insufficient data)* | - | - | - | 0 |"
        )
    lines = [
        "| Phase | Green | Yellow | Red | Samples |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| `{r['phase']}` | <= {r['green_max_sec']:.1f}s | "
            f"{r['green_max_sec'] + 0.1:.1f}-{r['yellow_max_sec']:.1f}s | "
            f"> {r['red_over_sec']:.1f}s | {r['count']} |"
        )
    return "\n".join(lines)


def update_cheat_sheet_thresholds(
    doc_path: Path,
    rows: List[dict],
    run_timings_path: Path,
    min_samples: int,
) -> bool:
    """
    Update markdown block in docs/phase_timing_cheat_sheet.md.
    Returns True when update succeeds.
    """
    if not doc_path.exists():
        return False
    start = "<!-- AUTO_THRESHOLD_TABLE_START -->"
    end = "<!-- AUTO_THRESHOLD_TABLE_END -->"

    try:
        text = doc_path.read_text()
    except Exception:
        return False

    if start not in text or end not in text:
        return False

    generated = datetime.now(timezone.utc).isoformat()
    table = format_thresholds_markdown_table(rows)
    block = (
        f"{start}\n"
        f"_Auto-calibrated from `{run_timings_path}` at {generated} "
        f"(min samples per phase: {min_samples})._\n\n"
        f"{table}\n"
        f"{end}"
    )
    before = text.split(start, 1)[0]
    after = text.split(end, 1)[1]
    try:
        doc_path.write_text(before + block + after)
    except Exception:
        return False
    return True
