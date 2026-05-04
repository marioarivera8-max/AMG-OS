"""
Scene library search — v11.1.

Powers `amg find` command. Searches across:
- Decision logs (all processed scenes)
- Review records (reviewed scenes get extra metadata)
- Distribution status (filter by ready/blocked)

Filters supported:
- --genre <tag>           Match any scene with this genre
- --performer <name>      Match scenes featuring this performer
- --studio <name>         Match scenes from this studio
- --scene-type <type>     Match by scene type
- --min-score <n>         Minimum top-pick score
- --processed-after <date> Date filter
- --reviewed              Only reviewed scenes
- --not-reviewed          Only un-reviewed scenes
- --ready-for <platform>  Distribution-ready for specific platform
"""
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from amg.config import (
    DECISION_LOGS_DIR,
    REVIEWED_DIR,
    DISTRIBUTION_STATUS_DIR,
)


def find_scenes(
    genre: Optional[str] = None,
    performer: Optional[str] = None,
    studio: Optional[str] = None,
    scene_type: Optional[str] = None,
    min_score: Optional[float] = None,
    processed_after: Optional[str] = None,
    reviewed: Optional[bool] = None,
    ready_for: Optional[str] = None,
    limit: int = 100,
) -> List[dict]:
    """
    Search the scene library.

    Returns list of dicts:
        {
            'scene_id': str,
            'studio': str,
            'scene_type': str,
            'genres': [...],
            'top_pick_score': float,
            'duration_sec': float,
            'processed_at': str,
            'reviewed': bool,
            'distribution_ready': dict (if applicable),
        }
    """
    if not DECISION_LOGS_DIR.exists():
        return []

    cutoff = None
    if processed_after:
        try:
            cutoff = datetime.fromisoformat(processed_after.replace("Z", ""))
        except ValueError:
            pass

    matches = []

    for log_file in DECISION_LOGS_DIR.glob("*.json"):
        try:
            with open(log_file) as f:
                record = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        # Date filter
        if cutoff:
            ts_str = record.get("timestamp_processed", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", ""))
                if ts < cutoff:
                    continue
            except ValueError:
                continue

        # Studio filter
        rec_studio = record.get("input", {}).get("studio")
        if studio and rec_studio != studio:
            continue

        # Scene type filter
        rec_type = record.get("input", {}).get("scene_type", "")
        if scene_type and rec_type != scene_type.upper():
            continue

        # Genre filter
        rec_genres = [g.upper() for g in record.get("input", {}).get("genres", [])]
        if genre and genre.upper() not in rec_genres:
            continue

        # Score filter
        rec_score = record.get("outcomes", {}).get("top_pick_score", 0) or 0
        if min_score is not None and rec_score < min_score:
            continue

        # Performer filter (check confirmed performers from review)
        if performer:
            scene_id = record.get("scene_id", "")
            review = _load_review(scene_id)
            if review:
                confirmed = [p.lower() for p in review.get("performers_confirmed", [])]
                if performer.lower() not in " ".join(confirmed):
                    continue
            else:
                # No review → skip if performer filter is active
                continue

        # Reviewed filter
        scene_id = record.get("scene_id", "")
        review = _load_review(scene_id)
        if reviewed is True and not review:
            continue
        if reviewed is False and review:
            continue

        # Distribution-ready filter
        ready_status = None
        if ready_for:
            ready_status = _load_distribution_status(scene_id)
            if not ready_status:
                continue
            platform_status = ready_status.get("per_platform", {}).get(ready_for, {})
            if not platform_status.get("ready", False):
                continue

        # Build match record
        matches.append({
            "scene_id": scene_id,
            "studio": rec_studio,
            "scene_type": rec_type,
            "genres": rec_genres,
            "top_pick_score": rec_score,
            "duration_sec": record.get("input", {}).get("duration_sec", 0),
            "covers_delivered": record.get("outcomes", {}).get("covers_delivered", 0),
            "processed_at": record.get("timestamp_processed"),
            "reviewed": review is not None,
            "title": (review.get("title") or {}).get("text") if review else None,
            "distribution_ready": ready_status,
        })

    # Sort by top_pick_score desc, then processed_at desc
    matches.sort(
        key=lambda x: (x.get("top_pick_score", 0), x.get("processed_at") or ""),
        reverse=True,
    )

    return matches[:limit]


def format_search_results(results: List[dict]) -> str:
    """Format search results as human-readable text table."""
    if not results:
        return "No matching scenes found."

    lines = []
    lines.append("=" * 100)
    lines.append(f"  Search Results: {len(results)} scene(s)")
    lines.append("=" * 100)

    # Header
    lines.append("")
    lines.append(f"  {'#':<4} {'Score':<6} {'Studio':<18} {'Type':<18} {'Title or Scene ID':<40}")
    lines.append("  " + "-" * 96)

    for i, r in enumerate(results, 1):
        score_str = f"{r['top_pick_score']:.1f}" if r['top_pick_score'] else "-.-"
        studio = (r.get('studio') or 'Unknown')[:17]
        scene_type = (r.get('scene_type') or 'STANDARD')[:17]

        # Show title if reviewed, else scene_id
        if r.get('title'):
            display = r['title'][:39]
        else:
            display = (r.get('scene_id') or '?')[:39]

        review_marker = "✓" if r.get("reviewed") else " "
        lines.append(f"  {i:<4} {score_str:<6} {studio:<18} {scene_type:<18} {review_marker} {display}")

    lines.append("")
    lines.append("=" * 100)
    lines.append(f"  Legend: ✓ = reviewed")
    lines.append("=" * 100)

    return "\n".join(lines)


def _load_review(scene_id: str) -> Optional[dict]:
    """Load review record."""
    safe_id = "".join(c if c.isalnum() or c in "_-" else "_" for c in scene_id)[:120]
    path = REVIEWED_DIR / f"{safe_id}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _load_distribution_status(scene_id: str) -> Optional[dict]:
    """Load distribution status record."""
    safe_id = "".join(c if c.isalnum() or c in "_-" else "_" for c in scene_id)[:120]
    path = DISTRIBUTION_STATUS_DIR / f"{safe_id}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None
