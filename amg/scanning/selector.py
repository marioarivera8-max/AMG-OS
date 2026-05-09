"""
Live top-K selector for the streaming scan.

Receives scored candidates as they come back from the AI dispatcher and
maintains a snapshot of the current best ``target_k`` covers using a
fused score:

    fused = ai_weight * ai_score + sharp_weight * normalize(sharpness)
            + zone_bonus(finish=+5, buildup=+3)

At ``finalize`` time it applies a **post-AI sharpness gate** (default
25th-percentile of all sharpness samples seen) before picking the top-K.
This is the safety net the classic pipeline lacked — it's why
FALLBACK-C could ship blurry covers when the simplified prompt scored
them above 30 (the 2026-05-08 Y&B_003 audit). Frames the AI loved but
that are visually blurry are now downgraded out of the cover set.

Min-time-gap dedup keeps the final cover set varied even when many
high-fused-score frames cluster around the same moment.

Thread-safe — the dispatcher's worker threads call ``add()`` concurrently.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

import numpy as np

from amg.config import (
    SHARPNESS_HARD_FLOOR,
    STREAMING_FUSED_AI_WEIGHT,
    STREAMING_FUSED_SHARP_WEIGHT,
    STREAMING_POST_AI_SHARP_PERCENTILE,
    STREAMING_ZONE_BONUS_BUILDUP,
    STREAMING_ZONE_BONUS_FINISH,
    SINGLE_PASS_MIN_GAP_SEC,
)
from amg.utils.logging import get_logger

log = get_logger("scanning.selector")


class LiveSelector:
    """Collect scored candidates, expose a finalize() that returns top-K."""

    def __init__(
        self,
        target_k: int,
        *,
        ai_weight: float = STREAMING_FUSED_AI_WEIGHT,
        sharp_weight: float = STREAMING_FUSED_SHARP_WEIGHT,
        zone_bonus_finish: float = STREAMING_ZONE_BONUS_FINISH,
        zone_bonus_buildup: float = STREAMING_ZONE_BONUS_BUILDUP,
        post_ai_sharp_percentile: float = STREAMING_POST_AI_SHARP_PERCENTILE,
        min_gap_sec: float = SINGLE_PASS_MIN_GAP_SEC,
        sharpness_normaliser: float = 10.0,
    ):
        if target_k <= 0:
            raise ValueError("target_k must be > 0")
        self.target_k = int(target_k)
        self.ai_w = float(ai_weight)
        self.sharp_w = float(sharp_weight)
        self.bonus_finish = float(zone_bonus_finish)
        self.bonus_buildup = float(zone_bonus_buildup)
        self.post_ai_pct = float(post_ai_sharp_percentile)
        self.min_gap = float(min_gap_sec)
        self.sharp_norm = max(1.0, float(sharpness_normaliser))

        self._lock = threading.Lock()
        # All scored candidates (parse_succeeded only). Kept in arrival
        # order; finalize() does the sort + gate + dedup at the end.
        self._scored: List[Dict[str, Any]] = []
        # Counts kept separately so callers can introspect mid-run
        # without walking _scored.
        self._n_seen = 0
        self._n_parse_failed = 0
        self._n_score_zero = 0

    # ----- write -----

    def add(self, candidate: Dict[str, Any]) -> None:
        """Record a scored candidate. Recompute its fused score in place
        so callers can introspect ``candidate['fused_score']``.

        Candidates that didn't parse, or scored 0 (typical for AI errors),
        are tracked in stats but never enter the pickable pool.
        """
        scored = candidate.get("scored_frame")
        with self._lock:
            self._n_seen += 1
            if scored is None or not getattr(scored, "parse_succeeded", False):
                self._n_parse_failed += 1
                return
            ai_score = float(getattr(scored, "score", 0.0) or 0.0)
            if ai_score <= 0:
                self._n_score_zero += 1
                return
            sharpness = float(candidate.get("sharpness") or 0.0)
            zone_tags = candidate.get("zone_tags") or []
            fused = self._fused_score(ai_score, sharpness, zone_tags)
            candidate["fused_score"] = fused
            self._scored.append(candidate)

    # ----- read -----

    def stats(self) -> dict:
        with self._lock:
            return {
                "seen": self._n_seen,
                "scored_pool": len(self._scored),
                "parse_failed": self._n_parse_failed,
                "score_zero": self._n_score_zero,
            }

    def finalize(self) -> Dict[str, Any]:
        """Pick the final cover set.

        Returns:
            {
              'picks': [candidate dicts, sorted by fused_score desc, len <= target_k],
              'stats': {...},
              'sharpness_floor_used': float,
              'gate_relaxed': bool,
            }
        """
        with self._lock:
            pool = list(self._scored)

        if not pool:
            return {
                "picks": [],
                "stats": self.stats(),
                "sharpness_floor_used": 0.0,
                "gate_relaxed": False,
            }

        sharpnesses = np.asarray([c.get("sharpness") or 0.0 for c in pool], dtype=float)

        # Post-AI sharpness gate: drop frames the AI loved but that are
        # visually blurry. Floor is the configured percentile of the
        # actual scored population, never below the absolute hard floor.
        floor = 0.0
        gate_relaxed = False
        if self.post_ai_pct > 0:
            floor = max(
                float(np.percentile(sharpnesses, self.post_ai_pct)),
                float(SHARPNESS_HARD_FLOOR),
            )
            qualifying = [c for c in pool if (c.get("sharpness") or 0.0) >= floor]
            # If the gate leaves us short of target_k, relax to the 10th
            # percentile so we still ship a cover floor — but flag it so
            # the operator knows quality may be uneven.
            if len(qualifying) < self.target_k:
                gate_relaxed = True
                relaxed_floor = max(
                    float(np.percentile(sharpnesses, min(10.0, self.post_ai_pct))),
                    float(SHARPNESS_HARD_FLOOR) / 2.0,
                )
                qualifying = [c for c in pool if (c.get("sharpness") or 0.0) >= relaxed_floor]
                floor = relaxed_floor
                log.warn(
                    "post-AI sharpness gate relaxed",
                    requested_pct=self.post_ai_pct,
                    relaxed_floor=round(floor, 1),
                    pool_size=len(pool),
                    qualifying=len(qualifying),
                )
        else:
            qualifying = list(pool)

        qualifying.sort(key=lambda c: c.get("fused_score") or 0.0, reverse=True)

        # Min-time-gap dedup so the cover set spans the scene rather than
        # clustering on a single hot moment.
        picks: List[Dict[str, Any]] = []
        for cand in qualifying:
            ts = float(cand.get("timestamp_sec") or 0.0)
            if any(
                abs(ts - float(p.get("timestamp_sec") or 0.0)) < self.min_gap
                for p in picks
            ):
                continue
            picks.append(cand)
            if len(picks) >= self.target_k:
                break

        # If min-gap dedup left us short, top off ignoring the gap. We'd
        # rather ship covers slightly close in time than ship fewer.
        if len(picks) < self.target_k:
            for cand in qualifying:
                if cand in picks:
                    continue
                picks.append(cand)
                if len(picks) >= self.target_k:
                    break

        return {
            "picks": picks,
            "stats": self.stats(),
            "sharpness_floor_used": float(floor),
            "gate_relaxed": gate_relaxed,
        }

    # ----- internals -----

    def _fused_score(
        self,
        ai_score: float,
        sharpness: float,
        zone_tags: List[str],
    ) -> float:
        sharp_norm = min(100.0, max(0.0, sharpness / self.sharp_norm))
        zone_b = 0.0
        if "finish_zone" in zone_tags:
            zone_b += self.bonus_finish
        if "buildup_zone" in zone_tags:
            zone_b += self.bonus_buildup
        return self.ai_w * ai_score + self.sharp_w * sharp_norm + zone_b
