"""
Error form — v11.1.

Renders failures in a readable, actionable format. Maps error codes to
recovery actions so the operator knows what to do next.
"""
from typing import List, Optional


# Map error codes to recovery instructions
ERROR_RECOVERY = {
    "E_AI_UNAVAILABLE": {
        "what_happened": "Ollama AI service is not responding.",
        "fix": "brew services restart ollama",
        "then": "amg verify",
        "and_finally": "Re-run the batch.",
    },
    "E_AI_TIMEOUT": {
        "what_happened": "AI scoring timed out (3 retries failed). Ollama may be overloaded.",
        "fix": "brew services restart ollama",
        "then": "Wait 10 seconds for model to reload",
        "and_finally": "amg resume <scene_id> to continue",
    },
    "E_AI_MODEL_NOT_LOADED": {
        "what_happened": "Vision model is not loaded in Ollama.",
        "fix": "ollama pull qwen2.5vl:7b",
        "then": "amg verify",
        "and_finally": "Re-run.",
    },
    "E_TIMEOUT_HARD": {
        "what_happened": "Processing exceeded 50% of video duration (hard budget).",
        "fix": "Check that Ollama optimizations are active: amg verify",
        "then": "If verified, scene may be unusual (very long, complex, etc.)",
        "and_finally": "Consider increasing TIME_BUDGET_HARD_PCT in config.yaml",
    },
    "E_TIMEOUT_ABSOLUTE": {
        "what_happened": "Hit 20-minute absolute time cap.",
        "fix": "Source video may be unusually long or system was busy.",
        "then": "Try again later, or process this scene alone (not in batch).",
        "and_finally": "If repeated, consider reviewing config time budgets.",
    },
    "E_SOURCE_UNREADABLE": {
        "what_happened": "Cannot read the source video file (corrupt, missing codec, or permission issue).",
        "fix": "Check file: ffprobe '<path>'",
        "then": "Verify file integrity, re-export from source if needed.",
        "and_finally": "If file is fine, check ffmpeg installation.",
    },
    "E_SOURCE_TOO_SHORT": {
        "what_happened": "Source video is less than 60 seconds — too short for v11.",
        "fix": "Verify this is the right file (not a teaser/preview).",
        "then": "Skip this file — it's not a full scene.",
        "and_finally": "",
    },
    "E_COMPLIANCE_NO_2257": {
        "what_happened": "No 2257 documentation found in scene folder.",
        "fix": "Place 2257 docs in same folder as video (named: 2257.pdf, 2257.jpg, or id.pdf).",
        "then": "Re-run the scene.",
        "and_finally": "If 2257 should be optional, set REQUIRE_2257_DOC=false in config.",
    },
    "E_FLOOR_NOT_MET": {
        "what_happened": "Pipeline could not deliver minimum 15 covers even after fallback cascade.",
        "fix": "Source content may be unusual (very dark, heavily clothed, compilation, etc.)",
        "then": "Review the partial output — may still be usable",
        "and_finally": "Consider lowering COVER_FLOOR for this scene type",
    },
    "E_FLOOR_FALLBACK_D": {
        "what_happened": "Floor met but Fallback D (pure CV) was used. Some covers are unrated.",
        "fix": "Review covers manually — some may be subpar",
        "then": "Mark scene as REVIEW_NEEDED in distribution status",
        "and_finally": "If repeated, scene type may not match AI training",
    },
    "E_CALIB_ALL_BLURRY": {
        "what_happened": "Source video is uniformly blurry — even 75th percentile fails sharpness threshold.",
        "fix": "Check source quality — may be low-bitrate or corrupted",
        "then": "Try original master file if available",
        "and_finally": "Skip if source is genuinely poor quality",
    },
}


def format_error_report(
    scene_id: str,
    error_codes: List[str],
    duration_sec: float = 0,
    partial_covers: int = 0,
    expected_covers: int = 15,
    work_dir: Optional[str] = None,
) -> str:
    """
    Build a human-readable error report for a failed scene.

    Returns formatted string ready to print.
    """
    lines = []
    lines.append("═" * 67)
    lines.append(f"  ⚠ SCENE FAILED: {scene_id}")
    lines.append("═" * 67)
    lines.append("")

    # Pick the most actionable error code
    primary = error_codes[0] if error_codes else "UNKNOWN"
    recovery = ERROR_RECOVERY.get(primary)

    if recovery:
        lines.append("  WHAT HAPPENED:")
        lines.append(f"    {recovery['what_happened']}")
        if partial_covers > 0:
            lines.append(f"    Got {partial_covers}/{expected_covers} covers before failure.")
        lines.append("")

        lines.append("  RECOMMENDED FIX:")
        lines.append(f"    1. {recovery['fix']}")
        if recovery.get("then"):
            lines.append(f"    2. {recovery['then']}")
        if recovery.get("and_finally"):
            lines.append(f"    3. {recovery['and_finally']}")
        lines.append("")
    else:
        lines.append("  WHAT HAPPENED:")
        lines.append(f"    Unknown error: {primary}")
        lines.append("    Check ~/AMG_OS/data/logs/runs/ for full log.")
        lines.append("")

    # Technical details
    lines.append(f"  TECHNICAL: {', '.join(error_codes)}")
    if duration_sec > 0:
        mins, secs = divmod(int(duration_sec), 60)
        lines.append(f"  TIME SPENT: {mins}m{secs:02d}s before abort")
    if work_dir:
        lines.append(f"  PARTIAL OUTPUT: {work_dir}")
    lines.append("")

    # Quick actions
    lines.append("  QUICK ACTIONS:")
    lines.append(f"    [r] Retry now           amg resume \"{scene_id}\"")
    lines.append(f"    [s] Skip this scene     (no action — continue batch)")
    lines.append(f"    [m] Mark for review     amg ready \"{scene_id}\" (forces review later)")
    lines.append("")

    lines.append("═" * 67)

    return "\n".join(lines)


def format_partial_success_report(
    scene_id: str,
    covers_delivered: int,
    expected: int,
    fallbacks_used: List[str],
    warnings: List[str],
) -> str:
    """Report for scenes that completed but with degraded quality."""
    lines = []
    lines.append("─" * 67)
    lines.append(f"  ⚠ COMPLETED WITH WARNINGS: {scene_id}")
    lines.append("─" * 67)
    lines.append(f"  Delivered: {covers_delivered}/{expected} covers")
    if fallbacks_used:
        lines.append(f"  Fallbacks used: {', '.join(fallbacks_used)}")
    for w in warnings:
        lines.append(f"  ⚠ {w}")
    lines.append("─" * 67)
    return "\n".join(lines)
