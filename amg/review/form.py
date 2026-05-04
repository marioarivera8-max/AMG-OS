"""
Human review form — v11.1.

Terminal-based form for reviewing a processed scene. Operator:
1. Reviews AI-generated title suggestions, picks one or types own
2. Picks the hero cover from candidates
3. Confirms/adjusts AI-detected genres
4. Confirms performers
5. Selects target platforms
6. Sets release date
7. Adds notes (optional)

Output: data/reviewed/{scene_id}.json with all decisions captured.
"""
import json
import sys
from datetime import datetime, date
from pathlib import Path
from typing import List, Optional

from amg.config import (
    REVIEWED_DIR,
    DECISION_LOGS_DIR,
    PLATFORM_REQUIREMENTS,
)
from amg.scoring.title_generator import generate_titles
from amg.scoring.ai_client import AIClient
from amg.utils.logging import get_logger

log = get_logger("review.form")


def open_review_form(scene_id: str) -> Optional[dict]:
    """
    Open the human review form for a processed scene.

    Returns the captured review decisions dict, or None if cancelled.
    """
    # Load the scene's decision log
    decision_log = _load_decision_log(scene_id)
    if not decision_log:
        print(f"\n✗ No decision log found for scene: {scene_id}")
        print(f"  Expected at: {DECISION_LOGS_DIR / (scene_id + '.json')}")
        print(f"  Has the scene been processed yet?")
        return None

    # Check if previously reviewed
    existing = _load_existing_review(scene_id)
    if existing:
        print(f"\n⚠ Scene already reviewed on {existing.get('reviewed_at', 'unknown date')}")
        proceed = _prompt_yn("Re-open review and overwrite? [y/N]: ", default=False)
        if not proceed:
            return existing

    print()
    print("═" * 67)
    print(f"  SCENE REVIEW: {scene_id}")
    duration = decision_log.get("execution", {}).get("total_duration_sec", 0)
    covers = decision_log.get("outcomes", {}).get("covers_delivered", 0)
    top_score = decision_log.get("outcomes", {}).get("top_pick_score", 0)
    print(f"  Processed in {_fmt_dur(duration)} · {covers} covers · Top score {top_score}")
    print("═" * 67)

    # ───── Section 1: TITLE ─────
    title = _section_title(decision_log)

    # ───── Section 2: COVER ─────
    cover = _section_cover(scene_id, decision_log)

    # ───── Section 3: GENRES ─────
    genres = _section_genres(decision_log)

    # ───── Section 4: PERFORMERS ─────
    performers = _section_performers(decision_log)

    # ───── Section 5: PLATFORMS ─────
    platforms = _section_platforms(title)

    # ───── Section 6: RELEASE DATE ─────
    release_date = _section_release_date()

    # ───── Section 7: NOTES ─────
    notes = _section_notes()

    # ───── Confirm + Save ─────
    review_decision = {
        "scene_id": scene_id,
        "reviewed_at": datetime.utcnow().isoformat() + "Z",
        "title": title,
        "cover_pick": cover,
        "genres_confirmed": genres,
        "performers_confirmed": performers,
        "target_platforms": platforms,
        "release_date": release_date,
        "notes": notes,
        "version": "11.1.0",
    }

    print()
    print("─" * 67)
    print("  REVIEW SUMMARY")
    print("─" * 67)
    print(f"  Title:       {title['text']}")
    print(f"  Cover:       {cover.get('filename', '<not set>')}")
    print(f"  Genres:      {', '.join(genres)}")
    print(f"  Performers:  {', '.join(performers)}")
    print(f"  Platforms:   {', '.join(platforms)}")
    print(f"  Release:     {release_date}")
    if notes:
        print(f"  Notes:       {notes}")
    print()

    confirm = _prompt_yn("Approve and save? [Y/n]: ", default=True)
    if not confirm:
        print("Cancelled. No changes saved.")
        return None

    save_review_decision(review_decision)
    print(f"\n✓ Review saved. Run 'amg ready {scene_id}' to verify distribution-readiness.")
    return review_decision


def save_review_decision(review: dict) -> Path:
    """Save review decision to disk."""
    REVIEWED_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(c if c.isalnum() or c in "_-" else "_" for c in review["scene_id"])[:120]
    path = REVIEWED_DIR / f"{safe_id}.json"
    with open(path, "w") as f:
        json.dump(review, f, indent=2, default=str)
    return path


# ============================================================
# SECTION HELPERS
# ============================================================

def _section_title(decision_log: dict) -> dict:
    """Section 1: Title selection with AI suggestions."""
    print()
    print("  ① TITLE")
    print("  " + "─" * 63)

    studio = decision_log.get("input", {}).get("studio") or "Unknown"
    performers_list = []
    # Try to get performers from various places
    perf_info = decision_log.get("input", {})
    if perf_info.get("performer_count"):
        # If we have count but no names, use the studio's regulars from profile
        performers_list = _guess_performer_names(studio, perf_info.get("performer_count", 1))

    scene_type = decision_log.get("input", {}).get("scene_type", "STANDARD")
    genres = decision_log.get("input", {}).get("genres", [])
    description = decision_log.get("input", {}).get("description", "")

    print("  Generating title suggestions...")
    try:
        suggestions = generate_titles(
            studio=studio,
            performers=performers_list,
            scene_type=scene_type,
            genres=genres,
            description=description,
            language="en",
            n_suggestions=5,
        )
    except Exception as e:
        log.warn("Title generation failed", error=str(e))
        suggestions = []

    if suggestions:
        print()
        print("  Suggestions:")
        for i, sug in enumerate(suggestions, 1):
            text = sug["text"]
            char_count = sug.get("char_count", len(text))
            style = sug.get("style", "?")
            warnings = sug.get("warnings", [])
            warning_str = f" ⚠ {warnings[0]}" if warnings else ""
            print(f"    {i}. {text}")
            print(f"       [{char_count} chars · {style}]{warning_str}")
        print(f"    {len(suggestions) + 1}. Type my own")
        print()

        choice = _prompt_int(
            f"  Pick title (1-{len(suggestions) + 1}): ",
            min_val=1, max_val=len(suggestions) + 1,
        )
        if choice <= len(suggestions):
            chosen = suggestions[choice - 1]
            return {
                "text": chosen["text"],
                "style": chosen["style"],
                "source": "ai_suggestion",
                "ai_suggestions": suggestions,
            }
        # Else fall through to manual entry

    # Manual entry
    while True:
        custom = input("  Enter title: ").strip()
        if not custom:
            print("  Title cannot be empty.")
            continue
        if len(custom) > 150:
            print("  Title too long (max 150 chars).")
            continue
        return {
            "text": custom,
            "style": "operator_entered",
            "source": "manual",
            "ai_suggestions": suggestions,
        }


def _section_cover(scene_id: str, decision_log: dict) -> dict:
    """Section 2: Cover selection."""
    print()
    print("  ② COVER (pick the hero)")
    print("  " + "─" * 63)

    # Find cover files
    work_dir = _find_work_dir(scene_id)
    if not work_dir:
        print("  ⚠ Could not find work directory. Cover pick skipped.")
        return {"filename": None, "rank": None, "score": None}

    covers_dir = work_dir / "covers"
    if not covers_dir.exists():
        print(f"  ⚠ Covers directory not found: {covers_dir}")
        return {"filename": None, "rank": None, "score": None}

    cover_files = sorted(covers_dir.glob("*.jpg"))
    if not cover_files:
        print("  ⚠ No cover files found.")
        return {"filename": None, "rank": None, "score": None}

    print(f"  Found {len(cover_files)} covers in {covers_dir.name}/covers/")
    print(f"  Open contact sheet now? It's at:")
    print(f"  {work_dir}/00_*_contact_sheet.jpg")
    print()
    print("  Listing top 10:")
    for i, cover_file in enumerate(cover_files[:10], 1):
        print(f"    {i:2d}. {cover_file.name}")
    if len(cover_files) > 10:
        print(f"    ... and {len(cover_files) - 10} more")
    print(f"    {len(cover_files) + 1}. Pick later (skip)")
    print()

    choice = _prompt_int(
        f"  Pick hero cover (1-{len(cover_files) + 1}): ",
        min_val=1, max_val=len(cover_files) + 1,
    )

    if choice == len(cover_files) + 1:
        return {"filename": None, "rank": None, "score": None, "deferred": True}

    chosen_file = cover_files[choice - 1]
    return {
        "filename": chosen_file.name,
        "path": str(chosen_file),
        "rank": choice,
    }


def _section_genres(decision_log: dict) -> List[str]:
    """Section 3: Genre confirmation."""
    print()
    print("  ③ GENRES (AI-detected, confirm or adjust)")
    print("  " + "─" * 63)

    detected = decision_log.get("input", {}).get("genres", [])
    print(f"  AI detected: {', '.join(detected) if detected else '(none)'}")
    print()
    print("  Press ENTER to accept, or type comma-separated genres to override:")
    response = input("  > ").strip()

    if not response:
        return detected

    # Parse override
    new_genres = [g.strip().upper() for g in response.split(",") if g.strip()]
    return new_genres


def _section_performers(decision_log: dict) -> List[str]:
    """Section 4: Performer confirmation."""
    print()
    print("  ④ PERFORMERS")
    print("  " + "─" * 63)

    studio = decision_log.get("input", {}).get("studio") or "Unknown"
    count = decision_log.get("input", {}).get("performer_count", 0)

    suggested = _guess_performer_names(studio, count)
    if suggested:
        print(f"  From {studio} regulars: {', '.join(suggested)}")
    print(f"  Expected count from code: {count}")
    print()
    print("  Press ENTER to accept suggestions, or type comma-separated names:")
    response = input("  > ").strip()

    if not response:
        return suggested
    return [n.strip() for n in response.split(",") if n.strip()]


def _section_platforms(title: dict) -> List[str]:
    """Section 5: Platform target selection."""
    print()
    print("  ⑤ PLATFORM TARGETS")
    print("  " + "─" * 63)

    title_len = len(title["text"])
    print(f"  Title length: {title_len} chars")

    available = list(PLATFORM_REQUIREMENTS.keys())
    print()
    for platform in available:
        max_chars = PLATFORM_REQUIREMENTS[platform].get("title_max_chars", 100)
        fits = title_len <= max_chars
        flag = "✓" if fits else "✗ TOO LONG"
        print(f"    {platform}: max {max_chars} chars [{flag}]")

    print()
    print("  Type platforms (comma-separated), or ENTER for all that fit:")
    response = input("  > ").strip()

    if not response:
        return [p for p in available
                if title_len <= PLATFORM_REQUIREMENTS[p].get("title_max_chars", 100)]

    requested = [p.strip().upper() for p in response.split(",")]
    return [p for p in requested if p in available]


def _section_release_date() -> str:
    """Section 6: Release date."""
    print()
    print("  ⑥ RELEASE DATE")
    print("  " + "─" * 63)
    today_str = date.today().isoformat()
    print(f"  Default: today ({today_str})")
    print()
    response = input("  Release date (YYYY-MM-DD) or ENTER for today: ").strip()

    if not response:
        return today_str

    try:
        # Validate
        datetime.strptime(response, "%Y-%m-%d")
        return response
    except ValueError:
        print(f"  Invalid date format. Using today.")
        return today_str


def _section_notes() -> str:
    """Section 7: Optional notes."""
    print()
    print("  ⑦ NOTES (optional)")
    print("  " + "─" * 63)
    notes = input("  > ").strip()
    return notes


# ============================================================
# HELPERS
# ============================================================

def _load_decision_log(scene_id: str) -> Optional[dict]:
    """Load a scene's decision log."""
    safe_id = "".join(c if c.isalnum() or c in "_-" else "_" for c in scene_id)[:120]
    path = DECISION_LOGS_DIR / f"{safe_id}.json"
    if not path.exists():
        # Try alternate format
        for candidate in DECISION_LOGS_DIR.glob(f"*{scene_id[:40]}*.json"):
            path = candidate
            break
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _load_existing_review(scene_id: str) -> Optional[dict]:
    """Load existing review if present."""
    safe_id = "".join(c if c.isalnum() or c in "_-" else "_" for c in scene_id)[:120]
    path = REVIEWED_DIR / f"{safe_id}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _find_work_dir(scene_id: str) -> Optional[Path]:
    """Try to locate the v11 work directory for a scene."""
    # Check common AMG_Processing locations
    home = Path.home()
    search_roots = [
        home / "AMG_Processing",
        home / "AMG_OS" / "incoming",
    ]
    target_pattern = f"{scene_id}_amg_v11"

    for root in search_roots:
        if not root.exists():
            continue
        for path in root.rglob(target_pattern):
            if path.is_dir():
                return path
        # Try partial match
        for path in root.rglob(f"*{scene_id[:40]}*_amg_v11*"):
            if path.is_dir():
                return path

    return None


def _guess_performer_names(studio: str, count: int) -> List[str]:
    """Guess performer names from studio profile."""
    from amg.ingest.studio_profiles import load_studio_profile
    profile = load_studio_profile(studio)
    if not profile:
        return []

    regulars = profile.get("performers", {}).get("regular", [])
    costars = profile.get("performers", {}).get("frequent_costars", [])

    # Combine, take up to count
    combined = regulars + costars
    return combined[:count] if count else combined


def _prompt_yn(prompt: str, default: bool = False) -> bool:
    """Yes/no prompt with default."""
    response = input(prompt).strip().lower()
    if not response:
        return default
    return response in ("y", "yes")


def _prompt_int(prompt: str, min_val: int = 1, max_val: int = 99) -> int:
    """Integer prompt with range validation."""
    while True:
        try:
            val = int(input(prompt).strip())
            if min_val <= val <= max_val:
                return val
            print(f"  Enter a number between {min_val} and {max_val}.")
        except ValueError:
            print("  Enter a valid number.")


def _fmt_dur(seconds: float) -> str:
    """Format duration for display."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    return f"{m}m {s:02d}s"
