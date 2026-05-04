"""
Studio threshold recalibrator.

Updates studio profile's calibration history based on accumulated decision logs.
Powers `amg calibrate <studio>` command.

This is the entry point for Phase 2 of the learning roadmap (after 50+ scenes,
we can compute meaningful per-studio statistical baselines).
"""
import json
from pathlib import Path
from typing import Optional

from amg.config import DECISION_LOGS_DIR
from amg.ingest.studio_profiles import (
    load_studio_profile,
    save_studio_profile,
    get_or_create_profile,
)
from amg.utils.logging import get_logger

log = get_logger("learning.calibrator")


def recalibrate_studio(studio_name: str, min_scenes: int = 5) -> dict:
    """
    Recompute calibration thresholds for a studio from decision logs.

    Args:
        studio_name: Studio to recalibrate (e.g., "YasminaBrady")
        min_scenes: Minimum scenes needed before recalibration is meaningful

    Returns:
        {
            'success': bool,
            'scenes_used': int,
            'old_floor': float or None,
            'new_floor': float or None,
            'recommendation': str,
        }
    """
    # Gather sharpness floors from this studio's decision logs
    floors = []
    for log_file in DECISION_LOGS_DIR.glob("*.json"):
        try:
            with open(log_file) as f:
                record = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        if record.get("input", {}).get("studio") != studio_name:
            continue

        cal = record.get("execution", {}).get("calibration")
        if cal and cal.get("tier_1_floor"):
            floors.append(cal["tier_1_floor"])

    if len(floors) < min_scenes:
        return {
            "success": False,
            "scenes_used": len(floors),
            "old_floor": None,
            "new_floor": None,
            "recommendation": f"Need at least {min_scenes} scenes for {studio_name} "
                              f"(have {len(floors)}). Process more scenes first.",
        }

    # Compute new statistics
    sorted_floors = sorted(floors)
    n = len(sorted_floors)

    new_avg = sum(sorted_floors) / n
    new_p25 = sorted_floors[n // 4]
    new_p75 = sorted_floors[3 * n // 4]

    # Get current profile
    profile = get_or_create_profile(studio_name)
    old_avg = profile.get("calibration_history", {}).get("tier_1_floor_avg")

    # Update
    profile["calibration_history"] = {
        "tier_1_floor_avg": new_avg,
        "tier_1_floor_p25": new_p25,
        "tier_1_floor_p75": new_p75,
        "scenes_processed": n,
        "_recent_floors": sorted_floors[-50:],  # Keep last 50
        "last_updated": None,  # save_studio_profile sets this
    }

    save_studio_profile(profile)

    delta_pct = ((new_avg - old_avg) / old_avg * 100) if old_avg else 0
    if abs(delta_pct) > 5:
        recommendation = f"Calibration shifted {delta_pct:+.1f}% — applied automatically."
    else:
        recommendation = f"Calibration stable (Δ={delta_pct:+.1f}%) — minimal change needed."

    log.info("Studio recalibrated",
             studio=studio_name,
             scenes=n,
             new_avg=new_avg,
             delta_pct=delta_pct)

    return {
        "success": True,
        "scenes_used": n,
        "old_floor": old_avg,
        "new_floor": new_avg,
        "delta_pct": delta_pct,
        "recommendation": recommendation,
    }
