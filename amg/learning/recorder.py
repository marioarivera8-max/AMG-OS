"""
Learning recorder.

The decision_log.write_decision_log function already captures everything we need.
This module is a thin wrapper that:
1. Updates studio calibration history
2. Updates performer registry counts
3. Triggers any "learning hooks" that observe each scene
"""
from pathlib import Path
from typing import Optional

from amg.ingest.studio_profiles import update_calibration_history
from amg.utils.logging import get_logger

log = get_logger("learning.recorder")


def record_scene_outcome(
    studio_name: Optional[str],
    calibration: Optional[dict],
    performer_info: Optional[dict],
    top_pick_score: Optional[float] = None,
) -> None:
    """
    After a scene is processed, update auxiliary learning data.

    The decision_log itself is the primary record. This updates lighter-weight
    aggregations like studio calibration history and performer scene counts.
    """
    # Update studio calibration history with new sharpness floor measurement
    if studio_name and calibration and calibration.get("tier_1_floor"):
        try:
            update_calibration_history(studio_name, calibration["tier_1_floor"])
        except Exception as e:
            log.warn("Failed to update studio calibration history",
                     studio=studio_name, error=str(e))

    # TODO Phase 2: update performer registry with this scene's top score
    # TODO Phase 3: feed back into prompt adaptation
