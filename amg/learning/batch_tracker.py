"""
Batch performance tracking — v11.1.

Records per-batch summary at end of each `amg batch` run.
Used by `amg dashboard` for trend analysis.
"""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from amg.config import (
    BATCH_SUMMARIES_DIR,
    PERFORMANCE_TREND_BASELINE_BATCHES,
    DEFAULT_OPERATOR,
    DEFAULT_MACHINE_ID,
)
from amg.utils.logging import get_logger

log = get_logger("learning.batch_tracker")


def write_batch_summary(
    batch_results: dict,
    batch_duration_sec: float,
    scenes_attempted: int,
    operator: Optional[str] = None,
    machine_id: Optional[str] = None,
) -> Path:
    """
    Write a batch summary record.

    Args:
        batch_results: {'completed': [...], 'warnings': [...], 'aborted': [...]}
        batch_duration_sec: Total batch wall-clock time
        scenes_attempted: Total scene count
    """
    BATCH_SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)

    completed = batch_results.get("completed", [])
    warnings = batch_results.get("warnings", [])
    aborted = batch_results.get("aborted", [])

    successful = len(completed) + len(warnings)

    # Compute per-scene timings
    scene_times = []
    for r in completed + warnings:
        dur = r.get("total_duration_sec", 0)
        if dur > 0:
            scene_times.append(dur)

    avg_scene = sum(scene_times) / len(scene_times) if scene_times else 0
    min_scene = min(scene_times) if scene_times else 0
    max_scene = max(scene_times) if scene_times else 0

    summary = {
        "batch_id": f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "started_at": datetime.fromtimestamp(time.time() - batch_duration_sec).isoformat(),
        "completed_at": datetime.utcnow().isoformat() + "Z",
        "operator": operator or DEFAULT_OPERATOR,
        "machine_id": machine_id or DEFAULT_MACHINE_ID,

        "scenes_attempted": scenes_attempted,
        "scenes_completed": len(completed),
        "scenes_with_warnings": len(warnings),
        "scenes_aborted": len(aborted),
        "success_rate": successful / scenes_attempted if scenes_attempted else 0,

        "batch_duration_sec": batch_duration_sec,
        "avg_scene_duration_sec": avg_scene,
        "min_scene_duration_sec": min_scene,
        "max_scene_duration_sec": max_scene,

        "scene_details": [
            {
                "scene_id": r.get("scene_id"),
                "duration_sec": r.get("total_duration_sec"),
                "covers_saved": r.get("covers_saved"),
                "top_pick_score": r.get("top_pick_score"),
                "error_codes": r.get("error_codes", []),
            }
            for r in (completed + warnings + aborted)
        ],

        # Performance trend comparison
        "trend": _compute_trend(avg_scene),
    }

    summary_path = BATCH_SUMMARIES_DIR / f"{summary['batch_id']}.json"
    try:
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        log.info("Batch summary written", path=str(summary_path))
    except Exception as e:
        log.error("Failed to write batch summary", error=str(e))

    return summary_path


def _compute_trend(current_avg: float) -> dict:
    """Compare current avg-per-scene to recent baseline."""
    if not BATCH_SUMMARIES_DIR.exists():
        return {"baseline_count": 0, "delta_pct": 0, "verdict": "first_batch"}

    summaries = sorted(
        BATCH_SUMMARIES_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    recent_avgs = []
    for path in summaries[:PERFORMANCE_TREND_BASELINE_BATCHES]:
        try:
            with open(path) as f:
                data = json.load(f)
            recent_avgs.append(data.get("avg_scene_duration_sec", 0))
        except Exception:
            continue

    if not recent_avgs:
        return {"baseline_count": 0, "delta_pct": 0, "verdict": "first_batch"}

    baseline = sum(recent_avgs) / len(recent_avgs)
    if baseline == 0:
        return {"baseline_count": len(recent_avgs), "delta_pct": 0, "verdict": "no_baseline"}

    delta_pct = ((current_avg - baseline) / baseline) * 100

    if delta_pct < -10:
        verdict = "faster"
    elif delta_pct > 25:
        verdict = "much_slower"
    elif delta_pct > 10:
        verdict = "slower"
    else:
        verdict = "stable"

    return {
        "baseline_count": len(recent_avgs),
        "baseline_avg_sec": baseline,
        "delta_pct": delta_pct,
        "verdict": verdict,
    }


def load_recent_summaries(n: int = 30) -> List[dict]:
    """Load the last N batch summaries."""
    if not BATCH_SUMMARIES_DIR.exists():
        return []

    summaries = sorted(
        BATCH_SUMMARIES_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    results = []
    for path in summaries[:n]:
        try:
            with open(path) as f:
                results.append(json.load(f))
        except Exception:
            continue

    return results
