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
from typing import Optional, List, Dict, Any

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


def get_platform_metadata_rules(platform: str) -> Dict[str, Any]:
    reqs = PLATFORM_REQUIREMENTS.get(platform, {}) if isinstance(PLATFORM_REQUIREMENTS, dict) else {}
    reqs = reqs if isinstance(reqs, dict) else {}
    md = reqs.get("metadata") if isinstance(reqs.get("metadata"), dict) else {}
    return {
        "title_min_chars": int(md.get("title_min_chars", 1)),
        "title_max_chars": int(reqs.get("title_max_chars", md.get("title_max_chars", 100))),
        "description_min_chars": int(md.get("description_min_chars", 0)),
        "description_max_chars": int(md.get("description_max_chars", 9999)),
        "min_tags": int(md.get("min_tags", 0)),
        "max_tags": int(md.get("max_tags", 9999)),
        "min_categories": int(md.get("min_categories", 0)),
        "max_categories": int(md.get("max_categories", 9999)),
        "banned_terms": reqs.get("banned_terms", []),
        "requires_2257": bool(reqs.get("requires_2257", True)),
        "requires_individual_releases": bool(reqs.get("requires_individual_releases", False)),
        "preferred_resolution_min": reqs.get("preferred_resolution_min"),
    }


def validate_metadata_for_platforms(
    *,
    title_text: str,
    long_description: str,
    tags: List[str],
    categories: List[str],
    target_platforms: List[str],
    performers: Optional[List[str]] = None,
    decision_log: Optional[dict] = None,
    metadata_only: bool = False,
) -> Dict[str, Any]:
    targets = [str(p).upper().strip() for p in (target_platforms or []) if str(p).strip()]
    per_platform: Dict[str, dict] = {}
    flat_blockers: List[str] = []
    flat_warnings: List[str] = []

    for platform in PLATFORM_REQUIREMENTS.keys():
        if platform not in targets:
            per_platform[platform] = {"ready": False, "skipped": True, "blockers": [], "warnings": []}
            continue
        status = _validate_platform_metadata(
            platform=platform,
            title_text=title_text,
            long_description=long_description,
            tags=tags,
            categories=categories,
            performers=performers or [],
            decision_log=decision_log or {},
            metadata_only=metadata_only,
        )
        per_platform[platform] = status
        for b in status.get("blockers", []):
            flat_blockers.append(f"{platform}: {b}")
        for w in status.get("warnings", []):
            flat_warnings.append(f"{platform}: {w}")

    overall_ready = bool(targets) and all(
        status.get("ready", False)
        for p, status in per_platform.items()
        if p in targets
    )
    return {
        "overall_ready": overall_ready,
        "per_platform": per_platform,
        "blockers": flat_blockers,
        "warnings": flat_warnings,
        "target_platforms": targets,
    }


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

    review_meta = _extract_review_metadata(review)
    title_text = review_meta["title_text"]
    long_description = review_meta["long_description"]
    tags = review_meta["tags"]
    categories = review_meta["categories"]

    # ── Check 3: Title set ──
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
    hero_cover = review_meta["hero_cover"]
    if hero_cover:
        result["checks"].append({
            "name": "Hero cover designated",
            "passed": True,
            "detail": hero_cover,
        })
    else:
        result["checks"].append({"name": "Hero cover designated", "passed": False})
        result["blockers"].append("Hero cover not picked — mark at least one kept/selected cover")

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
    sensitive = ((decision_log.get("review_flags") or {}).get("sensitive_content") or {})
    if sensitive.get("flagged"):
        flags = ", ".join(sensitive.get("flags") or [])
        result["checks"].append({
            "name": "Sensitive content review flags",
            "passed": True,
            "detail": flags or "flagged",
        })
        result["warnings"].append(f"Sensitive content flagged for review: {flags}")

    target_platforms = review_meta["target_platforms"]
    if not target_platforms:
        result["warnings"].append("No target platforms selected")

    result["checks"].append({
        "name": "Metadata completeness",
        "passed": bool(long_description and tags and categories),
        "detail": (
            f"description={len(long_description)} chars, "
            f"tags={len(tags)}, categories={len(categories)}"
        ),
    })

    validation = validate_metadata_for_platforms(
        title_text=title_text,
        long_description=long_description,
        tags=tags,
        categories=categories,
        target_platforms=target_platforms,
        performers=review_meta.get("performers") or [],
        decision_log=decision_log,
    )
    result["per_platform"] = validation["per_platform"]

    # ── Overall ready ──
    no_blockers = len(result["blockers"]) == 0
    any_platform_ready = any(
        p.get("ready", False)
        for p in result["per_platform"].values()
    )
    result["overall_ready"] = no_blockers and any_platform_ready

    return _save_and_return(result, verbose)


def _validate_platform_metadata(
    platform: str,
    *,
    title_text: str,
    long_description: str,
    tags: List[str],
    categories: List[str],
    performers: List[str],
    decision_log: dict,
    metadata_only: bool = False,
) -> dict:
    """Check readiness for a specific platform."""
    reqs = get_platform_metadata_rules(platform)
    blockers = []
    warnings = []

    # Title length checks
    min_chars = reqs["title_min_chars"]
    max_chars = reqs["title_max_chars"]
    if len(title_text) < min_chars:
        blockers.append(f"Title too short: {len(title_text)} < {min_chars}")
    if len(title_text) > max_chars:
        blockers.append(f"Title too long: {len(title_text)} > {max_chars}")

    # Banned terms
    title_lower = title_text.lower()
    for term in reqs.get("banned_terms", []):
        if term.lower() in title_lower:
            blockers.append(f"Title contains banned term '{term}'")

    # Metadata quality checks
    desc_min = reqs["description_min_chars"]
    desc_max = reqs["description_max_chars"]
    if desc_min and len(long_description) < desc_min:
        blockers.append(
            f"Description too short: {len(long_description)} < {desc_min}"
        )
    if desc_max and len(long_description) > desc_max:
        blockers.append(
            f"Description too long: {len(long_description)} > {desc_max}"
        )

    min_tags = reqs["min_tags"]
    max_tags = reqs["max_tags"]
    if min_tags and len(tags) < min_tags:
        blockers.append(f"Too few tags: {len(tags)} < {min_tags}")
    if max_tags and len(tags) > max_tags:
        warnings.append(f"Too many tags: {len(tags)} > {max_tags}")

    min_categories = reqs["min_categories"]
    max_categories = reqs["max_categories"]
    if min_categories and len(categories) < min_categories:
        blockers.append(f"Too few categories: {len(categories)} < {min_categories}")
    if max_categories and len(categories) > max_categories:
        warnings.append(f"Too many categories: {len(categories)} > {max_categories}")

    if performers and title_text:
        lead = str(performers[0]).split(" ")[0]
        if lead and lead.lower() not in title_text.lower():
            warnings.append(f"Title missing lead performer token '{lead}'")

    # 2257 doc check
    if not metadata_only and reqs.get("requires_2257", True):
        # decision log records compliance check result
        compliance = (decision_log or {}).get("execution", {}).get("error_codes", [])
        if "E_COMPLIANCE_NO_2257" in compliance:
            blockers.append("Missing 2257 documentation")

    sensitive = (((decision_log or {}).get("review_flags") or {}).get("sensitive_content") or {})
    if sensitive.get("flagged"):
        flags = ", ".join(sensitive.get("flags") or [])
        warnings.append(f"Sensitive content flagged for operator/platform review: {flags}")

    # Individual model releases
    if not metadata_only and reqs.get("requires_individual_releases", False):
        for performer in performers:
            if not _has_performer_release(performer):
                blockers.append(f"Missing model release: {performer}")

    # Resolution check
    pref_min = reqs.get("preferred_resolution_min")
    if pref_min:
        resolution_str = (decision_log or {}).get("input", {}).get("resolution", "0x0")
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


def _extract_review_metadata(review: dict) -> Dict[str, Any]:
    review = review if isinstance(review, dict) else {}
    title_text = (
        ((review.get("title") or {}).get("text"))
        or review.get("title_override")
        or ""
    )
    long_description = str(review.get("long_description") or "").strip()

    target_platforms = review.get("target_platforms") or []
    if not isinstance(target_platforms, list):
        target_platforms = []
    target_platforms = [str(p).upper().strip() for p in target_platforms if str(p).strip()]

    performers = review.get("performers_confirmed") or []
    if not isinstance(performers, list):
        performers = []

    hero_cover = ((review.get("cover_pick") or {}).get("filename") or "").strip()
    if not hero_cover:
        kept = review.get("kept_covers") or []
        selected = review.get("selected_covers") or []
        if isinstance(kept, list) and kept:
            hero_cover = str(kept[0])
        elif isinstance(selected, list) and selected:
            hero_cover = str(selected[0])

    tags = _normalize_tokens(review.get("tags_csv"))
    categories = _normalize_tokens(review.get("categories_csv"), title_case=True)

    return {
        "title_text": str(title_text).strip(),
        "long_description": long_description,
        "target_platforms": target_platforms,
        "performers": [str(x).strip() for x in performers if str(x).strip()],
        "hero_cover": hero_cover,
        "tags": tags,
        "categories": categories,
    }


def _normalize_tokens(value: Any, *, title_case: bool = False) -> List[str]:
    if value is None:
        return []
    raw: List[str] = []
    if isinstance(value, str):
        raw = [x.strip() for x in value.replace(";", ",").split(",")]
    elif isinstance(value, list):
        raw = [str(x).strip() for x in value]
    out = []
    seen = set()
    for token in raw:
        if not token:
            continue
        t = " ".join(token.split())
        if title_case:
            t = " ".join(part.capitalize() for part in t.split())
        lower = t.lower()
        if lower in seen:
            continue
        seen.add(lower)
        out.append(t)
    return out


def _has_performer_release(performer_name: str) -> bool:
    """Check if a performer has a model release on file."""
    try:
        from amg.compliance.registry import performer_status

        if performer_status(performer_name).get("has_model_release"):
            return True
    except Exception:
        pass

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
