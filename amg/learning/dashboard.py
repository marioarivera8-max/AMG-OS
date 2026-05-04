"""
Performance dashboard — v11.1.

Powers `amg dashboard` command. Surfaces:
- Recent batch performance (trends)
- Per-studio quality breakdown
- Error patterns over time
- Throughput projections
"""
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from amg.learning.batch_tracker import load_recent_summaries
from amg.learning.analyzer import analyze_logs
from amg.utils.timing import format_duration


def render_dashboard(days: int = 30) -> str:
    """Render the full dashboard as text."""
    lines = []
    lines.append("=" * 80)
    lines.append("  AMG OS — Performance Dashboard")
    lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M %Z')}")
    lines.append(f"  Period: Last {days} days")
    lines.append("=" * 80)
    lines.append("")

    # Section 1: Recent batches
    lines.extend(_render_recent_batches())
    lines.append("")

    # Section 2: Quality metrics
    lines.extend(_render_quality_metrics(days))
    lines.append("")

    # Section 3: Performance trend
    lines.extend(_render_performance_trend())
    lines.append("")

    # Section 4: Per-studio breakdown
    lines.extend(_render_studio_breakdown(days))
    lines.append("")

    # Section 5: Error patterns
    lines.extend(_render_error_patterns(days))
    lines.append("")

    lines.append("=" * 80)
    return "\n".join(lines)


def _render_recent_batches() -> List[str]:
    """Recent batch performance summary."""
    lines = ["RECENT BATCHES:"]
    summaries = load_recent_summaries(n=10)

    if not summaries:
        lines.append("  No batches recorded yet.")
        return lines

    lines.append("")
    lines.append(f"  {'Date':<20} {'Scenes':<10} {'Avg/Scene':<14} {'Total':<14} {'Trend':<14}")
    lines.append("  " + "-" * 76)

    for s in summaries:
        date_str = s.get("completed_at", "")[:16].replace("T", " ")
        scenes = s.get("scenes_completed", 0)
        total = scenes + s.get("scenes_with_warnings", 0)
        attempted = s.get("scenes_attempted", 0)
        scenes_str = f"{total}/{attempted}"
        avg = s.get("avg_scene_duration_sec", 0)
        avg_str = format_duration(avg) if avg else "-"
        batch_dur = s.get("batch_duration_sec", 0)
        batch_str = format_duration(batch_dur)

        trend = s.get("trend", {})
        verdict = trend.get("verdict", "?")
        delta = trend.get("delta_pct", 0)
        if verdict == "faster":
            trend_str = f"⚡ {delta:+.0f}%"
        elif verdict == "stable":
            trend_str = "→ stable"
        elif verdict == "slower":
            trend_str = f"⚠ {delta:+.0f}%"
        elif verdict == "much_slower":
            trend_str = f"✗ {delta:+.0f}%"
        else:
            trend_str = "-"

        lines.append(f"  {date_str:<20} {scenes_str:<10} {avg_str:<14} {batch_str:<14} {trend_str:<14}")

    return lines


def _render_quality_metrics(days: int) -> List[str]:
    """Cover quality metrics."""
    analysis = analyze_logs(days_back=days)

    lines = ["QUALITY METRICS:"]
    lines.append("")
    lines.append(f"  Total scenes processed: {analysis['total_scenes']}")
    lines.append(f"  Avg covers per scene:   {analysis['avg_covers_per_scene']:.1f}")
    lines.append(f"  Avg top-pick score:     {analysis['avg_top_pick_score']:.1f}")
    lines.append(f"  Floor compliance:       {analysis['floor_compliance_pct']:.1f}%")
    lines.append(f"  Fallback usage:         {analysis['fallback_pct']:.1f}%")

    return lines


def _render_performance_trend() -> List[str]:
    """Performance trend analysis."""
    summaries = load_recent_summaries(n=20)

    lines = ["PERFORMANCE TREND:"]

    if len(summaries) < 2:
        lines.append("  Need at least 2 batches for trend analysis.")
        return lines

    # Recent vs earlier
    recent_5 = summaries[:5]
    earlier_5 = summaries[5:10] if len(summaries) >= 10 else []

    recent_avg = (sum(s.get("avg_scene_duration_sec", 0) for s in recent_5) / len(recent_5)
                  if recent_5 else 0)
    earlier_avg = (sum(s.get("avg_scene_duration_sec", 0) for s in earlier_5) / len(earlier_5)
                   if earlier_5 else 0)

    lines.append("")
    lines.append(f"  Recent 5 batches avg:  {format_duration(recent_avg)}")
    if earlier_avg > 0:
        lines.append(f"  Earlier 5 batches avg: {format_duration(earlier_avg)}")
        delta = recent_avg - earlier_avg
        delta_pct = (delta / earlier_avg) * 100 if earlier_avg else 0
        if delta < 0:
            lines.append(f"  Direction:             ⚡ Speeding up ({delta_pct:+.1f}%)")
        elif delta_pct > 25:
            lines.append(f"  Direction:             ⚠ MUCH slower ({delta_pct:+.1f}%) — investigate")
        elif delta_pct > 10:
            lines.append(f"  Direction:             ⚠ Slower ({delta_pct:+.1f}%)")
        else:
            lines.append(f"  Direction:             → Stable ({delta_pct:+.1f}%)")

    return lines


def _render_studio_breakdown(days: int) -> List[str]:
    """Per-studio breakdown."""
    analysis = analyze_logs(days_back=days)

    lines = ["PER-STUDIO BREAKDOWN:"]
    lines.append("")

    by_studio = analysis.get("by_studio", {})
    if not by_studio:
        lines.append("  No studios processed yet.")
        return lines

    lines.append(f"  {'Studio':<20} {'Scenes':<8} {'Avg Top-Pick':<14} {'Timeouts':<10}")
    lines.append("  " + "-" * 60)

    for studio, data in sorted(by_studio.items(), key=lambda x: -x[1]["scenes"]):
        lines.append(
            f"  {studio:<20} {data['scenes']:<8} "
            f"{data['avg_top_pick']:.1f}{'':10} {data['timeouts']:<10}"
        )

    return lines


def _render_error_patterns(days: int) -> List[str]:
    """Error code frequency."""
    analysis = analyze_logs(days_back=days)
    error_counts = analysis.get("error_counts", {})

    lines = ["ERROR PATTERNS:"]
    lines.append("")

    if not error_counts:
        lines.append("  No errors recorded — clean operations.")
        return lines

    for code, count in sorted(error_counts.items(), key=lambda x: -x[1]):
        bar = "█" * min(40, count)
        lines.append(f"  {code:<25} {count:<5} {bar}")

    return lines
