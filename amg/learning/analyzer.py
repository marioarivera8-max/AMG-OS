"""
Decision log analyzer.

Reads all decision logs in data/decision_logs/ and surfaces patterns.
Powers the `amg analyze` command.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from amg.config import DECISION_LOGS_DIR
from amg.utils.logging import get_logger

log = get_logger("learning.analyzer")


def analyze_logs(
    days_back: int = 30,
    operator: Optional[str] = None,
    studio: Optional[str] = None,
) -> dict:
    """
    Analyze decision logs from the last N days.

    Returns a dict with aggregated metrics suitable for `amg analyze` output.
    """
    cutoff = datetime.utcnow() - timedelta(days=days_back)

    if not DECISION_LOGS_DIR.exists():
        return _empty_analysis()

    logs = []
    for log_file in DECISION_LOGS_DIR.glob("*.json"):
        try:
            with open(log_file) as f:
                record = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        ts_str = record.get("timestamp_processed", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", ""))
        except ValueError:
            continue

        if ts < cutoff:
            continue

        # Filter by operator/studio if specified
        if operator and record.get("operator") != operator:
            continue
        if studio:
            input_studio = record.get("input", {}).get("studio")
            if input_studio != studio:
                continue

        logs.append(record)

    return _aggregate(logs, days_back)


def _aggregate(logs, days_back):
    """Compute aggregate metrics from filtered logs."""
    if not logs:
        return _empty_analysis(days_back)

    total_runtime = sum(r.get("execution", {}).get("total_duration_sec", 0) for r in logs)
    total_scenes = len(logs)

    # Quality metrics
    cover_counts = [r.get("outcomes", {}).get("covers_delivered", 0) for r in logs]
    top_picks = [
        r.get("outcomes", {}).get("top_pick_score", 0)
        for r in logs
        if r.get("outcomes", {}).get("top_pick_score")
    ]

    floor_compliance_count = sum(1 for c in cover_counts if c >= 10)

    # Fallback usage
    fallback_uses = defaultdict(int)
    for r in logs:
        for f in r.get("outcomes", {}).get("fallbacks_used", []):
            fallback_uses[f] += 1

    # Per-studio breakdown
    by_studio = defaultdict(lambda: {"scenes": 0, "top_pick_sum": 0, "top_pick_count": 0,
                                     "timeouts": 0})
    for r in logs:
        studio = r.get("input", {}).get("studio") or "Unknown"
        s = by_studio[studio]
        s["scenes"] += 1
        score = r.get("outcomes", {}).get("top_pick_score")
        if score:
            s["top_pick_sum"] += score
            s["top_pick_count"] += 1
        for code in r.get("execution", {}).get("error_codes", []):
            if "TIMEOUT" in code:
                s["timeouts"] += 1

    studio_summary = {}
    for studio, data in by_studio.items():
        studio_summary[studio] = {
            "scenes": data["scenes"],
            "avg_top_pick": (data["top_pick_sum"] / data["top_pick_count"])
                            if data["top_pick_count"] else 0,
            "timeouts": data["timeouts"],
        }

    # Error frequency
    error_counts = defaultdict(int)
    for r in logs:
        for code in r.get("execution", {}).get("error_codes", []):
            error_counts[code] += 1

    return {
        "period_days": days_back,
        "total_scenes": total_scenes,
        "total_runtime_sec": total_runtime,
        "avg_per_scene_sec": total_runtime / total_scenes if total_scenes else 0,
        "avg_covers_per_scene": sum(cover_counts) / len(cover_counts) if cover_counts else 0,
        "avg_top_pick_score": sum(top_picks) / len(top_picks) if top_picks else 0,
        "floor_compliance_pct": (floor_compliance_count / total_scenes * 100)
                                if total_scenes else 0,
        "fallback_usage": dict(fallback_uses),
        "fallback_pct": (sum(fallback_uses.values()) / total_scenes * 100)
                        if total_scenes else 0,
        "by_studio": studio_summary,
        "error_counts": dict(error_counts),
        "logs_analyzed": total_scenes,
    }


def _empty_analysis(days_back=30):
    return {
        "period_days": days_back,
        "total_scenes": 0,
        "total_runtime_sec": 0,
        "avg_per_scene_sec": 0,
        "avg_covers_per_scene": 0,
        "avg_top_pick_score": 0,
        "floor_compliance_pct": 0,
        "fallback_usage": {},
        "fallback_pct": 0,
        "by_studio": {},
        "error_counts": {},
        "logs_analyzed": 0,
    }


def generate_report(analysis: dict) -> str:
    """
    Format an analysis dict as a human-readable text report.
    """
    if analysis["total_scenes"] == 0:
        return f"No scenes processed in the last {analysis['period_days']} days."

    lines = [
        "=" * 64,
        "AMG OS — Performance Analysis",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M %Z')}",
        f"Period: Last {analysis['period_days']} days",
        "=" * 64,
        "",
        "PROCESSING VOLUME:",
        f"  Total scenes processed: {analysis['total_scenes']}",
        f"  Total runtime: {_fmt(analysis['total_runtime_sec'])}",
        f"  Avg per scene: {_fmt(analysis['avg_per_scene_sec'])}",
        "",
        "QUALITY METRICS:",
        f"  Avg covers per scene: {analysis['avg_covers_per_scene']:.1f}",
        f"  Avg top-pick score: {analysis['avg_top_pick_score']:.1f}",
        f"  Floor compliance: {analysis['floor_compliance_pct']:.1f}%",
        f"  Fallback usage: {analysis['fallback_pct']:.1f}%",
        "",
        "PER-STUDIO BREAKDOWN:",
    ]
    for studio, data in sorted(analysis["by_studio"].items(), key=lambda x: -x[1]["scenes"]):
        lines.append(
            f"  {studio:<20} {data['scenes']:3d} scenes, "
            f"avg {data['avg_top_pick']:.1f} top-pick, "
            f"{data['timeouts']} timeouts"
        )

    if analysis["error_counts"]:
        lines.extend(["", "ERROR FREQUENCY:"])
        for code, count in sorted(analysis["error_counts"].items(), key=lambda x: -x[1]):
            lines.append(f"  {code:<25} {count}")

    lines.append("=" * 64)
    return "\n".join(lines)


def _fmt(seconds):
    """Format duration."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"
