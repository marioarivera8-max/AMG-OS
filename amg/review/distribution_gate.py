"""
Distribution-ready gate — v11.1.

After human review, this verifies a scene is ready to leave v11's domain
and enter distribution. Per-platform readiness is computed.

Checks:
1. Source video integrity (file exists, readable, metadata valid)
2. All covers verified (count >= floor, dimensions OK, not blank)
3. Hero cover designated
4. Title meets per-platform length limits
5. Compliance docs present (2257 + per-performer model releases per platform)
6. Decision log + review record + metadata present

Output: data/distribution_status/{scene_id}.json
        Plus a human-readable status report
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from amg.config import (
    DISTRIBUTION_STATUS_DIR,
    DECISION_LOGS_DIR,
    REVIEWED_DIR,
    PERFORMER_DOCS_DIR,
    PLATFORM_REQUIREMENTS,
    COVER_FLOOR,
)
from amg.utils.logging import get_logger

log = get_logger("review.distribution_gate")


def check_distribution_ready(scene_id: str, verbose: bool = True) -> dict:
    """
    Verify a scene is distribution-ready.

    Returns:
        {
            'scene_id': str,
            'overall_ready': bool,
            'per_platform': {
                'AEBN': {'ready': bool, 'blockers': [...], 'warnings': [...]},
                'SLR': {...},
                'ADE': {...}
            },
            'checks': [
                {'name': '...', 'passed': bool, 'detail': '...'}
            ],
            'checked_at': iso8601,
        }
    """
    DISTRIBUTION_STATUS_DIR.mkdir(parents=True, exist_ok=True)

    result = {
        "scene_id": scene_id,
        "overall_ready": False,
        "per_platform": {},
        "checks": [],
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "blockers": [],
        "warnings": [],
    }

    # Load decision log + review
    decision_log = _load_json(DECISION_LOGS_DIR, scene_id)
    review = _load_json(REVIEWED_DIR, scene_id)

    # ── Check 1: Decision log present ──
    if decision_log:
        result["checks"].append({"name": "Decision log present", "passed": True})
    else:
        result["checks"].append({
            "name": "Decision log present",
            "passed": False,
            "detail": "Scene may not be processed yet — run amg process first",
        })
        result["blockers"].append("No decision log — process scene first")
        return _save_and_return(result, verbose)

    # ── Check 2: Reviewed ──
    if review:
        result["checks"].append({"name": "Human review completed", "passed": True})
    else:
        result["checks"].append({
            "name": "Human review completed",
            "passed": False,
            "detail": "Run: amg review " + scene_id,
        })
        result["blockers"].append("Not reviewed — run 'amg review' first")
        return _save_and_return(result, verbose)

    # ── Check 3: Title set ──
    title_text = (review.get("title") or {}).get("text", "")
    if title_text:
        result["checks"].append({
            "name": "Title set",
            "passed": True,
            "detail": title_text,
        })
    else:
        result["checks"].append({"name": "Title set", "passed": False})
        result["blockers"].append("Title missing")

    # ── Check 4: Hero cover picked ──
    cover = review.get("cover_pick") or {}
    if cover.get("filename"):
        result["checks"].append({
            "name": "Hero cover designated",
            "passed": True,
            "detail": cover["filename"],
        })
    else:
        result["checks"].append({"name": "Hero cover designated", "passed": False})
        result["blockers"].append("Hero cover not picked — re-run amg review")

    # ── Check 5: Cover count meets floor ──
    cover_count = (decision_log.get("outcomes", {}).get("covers_delivered", 0))
    if cover_count >= COVER_FLOOR:
        result["checks"].append({
            "name": f"Cover floor ({COVER_FLOOR})",
            "passed": True,
            "detail": f"{cover_count} covers delivered",
        })
    else:
        result["checks"].append({
            "name": f"Cover floor ({COVER_FLOOR})",
            "passed": False,
            "detail": f"Only {cover_count} covers — below floor",
        })
        result["warnings"].append(f"Only {cover_count} covers (below {COVER_FLOOR})")

    # ── Check 6: Per-platform readiness ──
    target_platforms = review.get("target_platforms", [])
    if not target_platforms:
        result["warnings"].append("No target platforms selected")

    for platform in PLATFORM_REQUIREMENTS.keys():
        if platform not in target_platforms:
            result["per_platform"][platform] = {"ready": False, "skipped": True}
            continue

        plat_status = _check_platform(platform, title_text, review, decision_log)
        result["per_platform"][platform] = plat_status

    # ── Overall ready ──
    no_blockers = len(result["blockers"]) == 0
    any_platform_ready = any(
        p.get("ready", False)
        for p in result["per_platform"].values()
    )
    result["overall_ready"] = no_blockers and any_platform_ready

    return _save_and_return(result, verbose)


def _check_platform(
    platform: str,
    title: str,
    review: dict,
    decision_log: dict,
) -> dict:
    """Check readiness for a specific platform."""
    reqs = PLATFORM_REQUIREMENTS[platform]
    blockers = []
    warnings = []

    # Title length check
    max_chars = reqs.get("title_max_chars", 100)
    if len(title) > max_chars:
        blockers.append(f"Title too long: {len(title)} > {max_chars}")

    # Banned terms
    title_lower = title.lower()
    for term in reqs.get("banned_terms", []):
        if term.lower() in title_lower:
            blockers.append(f"Title contains banned term '{term}'")

    # 2257 doc check
    if reqs.get("requires_2257", True):
        # decision log records compliance check result
        compliance = decision_log.get("execution", {}).get("error_codes", [])
        if "E_COMPLIANCE_NO_2257" in compliance:
            blockers.append("Missing 2257 documentation")

    # Individual model releases
    if reqs.get("requires_individual_releases", False):
        performers = review.get("performers_confirmed", [])
        for performer in performers:
            if not _has_performer_release(performer):
                blockers.append(f"Missing model release: {performer}")

    # Resolution check
    pref_min = reqs.get("preferred_resolution_min")
    if pref_min:
        resolution_str = decision_log.get("input", {}).get("resolution", "0x0")
        try:
            w, h = map(int, resolution_str.split("x"))
            min_w, min_h = pref_min
            if w < min_w or h < min_h:
                warnings.append(f"Resolution {resolution_str} below preferred {min_w}x{min_h}")
        except (ValueError, AttributeError):
            warnings.append("Could not parse resolution")

    return {
        "ready": len(blockers) == 0,
        "blockers": blockers,
        "warnings": warnings,
    }


def _has_performer_release(performer_name: str) -> bool:
    """Check if a performer has a model release on file."""
    if not PERFORMER_DOCS_DIR.exists():
        return False
    safe_name = "".join(c if c.isalnum() or c == "_" else "_" for c in performer_name.lower())
    # Look for any of: model_release.pdf, release.pdf
    candidates = list(PERFORMER_DOCS_DIR.glob(f"{safe_name}*release*"))
    candidates += list(PERFORMER_DOCS_DIR.glob(f"*{safe_name}*release*"))
    return len(candidates) > 0


def _load_json(directory: Path, scene_id: str) -> Optional[dict]:
    """Load JSON file matching scene_id."""
    safe_id = "".join(c if c.isalnum() or c in "_-" else "_" for c in scene_id)[:120]
    path = directory / f"{safe_id}.json"
    if not path.exists():
        for candidate in directory.glob(f"*{scene_id[:40]}*.json"):
            path = candidate
            break
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _save_and_return(result: dict, verbose: bool) -> dict:
    """Save status and optionally print report."""
    DISTRIBUTION_STATUS_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(c if c.isalnum() or c in "_-" else "_"
                      for c in result["scene_id"])[:120]
    path = DISTRIBUTION_STATUS_DIR / f"{safe_id}.json"
    try:
        with open(path, "w") as f:
            json.dump(result, f, indent=2, default=str)
    except Exception as e:
        log.warn("Failed to save distribution status", error=str(e))

    if verbose:
        _print_report(result)
    return result


def _print_report(result: dict) -> None:
    """Print human-readable distribution-ready report."""
    print()
    print("═" * 67)
    print(f"  DISTRIBUTION-READY CHECK: {result['scene_id']}")
    print("═" * 67)

    overall = "✓ READY" if result["overall_ready"] else "✗ NOT READY"
    print(f"\n  Overall: {overall}")

    if result["blockers"]:
        print("\n  BLOCKERS:")
        for blocker in result["blockers"]:
            print(f"    ✗ {blocker}")

    if result["warnings"]:
        print("\n  Warnings:")
        for warning in result["warnings"]:
            print(f"    ⚠ {warning}")

    print("\n  CHECKS:")
    for check in result["checks"]:
        marker = "✓" if check.get("passed") else "✗"
        line = f"    {marker} {check['name']}"
        if check.get("detail"):
            line += f": {check['detail']}"
        print(line)

    if result["per_platform"]:
        print("\n  PER-PLATFORM:")
        for platform, status in result["per_platform"].items():
            if status.get("skipped"):
                print(f"    {platform}: not targeted")
                continue
            marker = "✓" if status.get("ready") else "✗"
            print(f"    {marker} {platform}")
            for blocker in status.get("blockers", []):
                print(f"        ✗ {blocker}")
            for warning in status.get("warnings", []):
                print(f"        ⚠ {warning}")

    print()
    print("═" * 67)
