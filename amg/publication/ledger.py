"""Append-only publication ledger.

This records operator-driven publication milestones without automating
downstream platform uploads.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from amg.config import (
    PUBLICATION_EVENTS_PATH,
    PUBLICATION_STATUS_DIR,
    DEFAULT_OPERATOR,
)

PUBLICATION_STATUSES = {
    "packaged",
    "submitted",
    "accepted",
    "published",
    "rejected",
    "needs_changes",
    "removed",
}


def record_publication_event(
    scene_id: str,
    platform: str,
    status: str,
    *,
    operator: Optional[str] = None,
    external_id: Optional[str] = None,
    receipt_path: Optional[Path | str] = None,
    package_path: Optional[Path | str] = None,
    notes: Optional[str] = None,
    rejection_reason: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Append a publication event and update the latest-status snapshot."""
    scene_id = str(scene_id or "").strip()
    if not scene_id:
        raise ValueError("scene_id is required")
    platform = str(platform or "").strip().upper()
    if not platform:
        raise ValueError("platform is required")
    status = str(status or "").strip().lower()
    if status not in PUBLICATION_STATUSES:
        choices = ", ".join(sorted(PUBLICATION_STATUSES))
        raise ValueError(f"invalid publication status '{status}' (expected one of: {choices})")

    event = {
        "event_id": uuid.uuid4().hex,
        "scene_id": scene_id,
        "platform": platform,
        "status": status,
        "operator": operator or DEFAULT_OPERATOR,
        "timestamp": _utc_now_iso(),
        "external_id": str(external_id).strip() if external_id else None,
        "receipt_path": str(receipt_path) if receipt_path else None,
        "package_path": str(package_path) if package_path else None,
        "notes": str(notes).strip() if notes else None,
        "rejection_reason": str(rejection_reason).strip() if rejection_reason else None,
        "metadata": metadata or {},
    }

    PUBLICATION_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PUBLICATION_EVENTS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=True, default=str) + "\n")
    _update_status_snapshot(event)
    return event


def load_publication_status(scene_id: str) -> Dict[str, Any]:
    """Load the latest per-platform publication status for a scene."""
    path = PUBLICATION_STATUS_DIR / f"{_safe_scene_id(scene_id)}.json"
    if not path.exists():
        return {"scene_id": scene_id, "platforms": {}, "events_count": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"scene_id": scene_id, "platforms": {}, "events_count": 0, "load_error": str(path)}


def list_publication_events(scene_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Return recent ledger events, newest first."""
    if not PUBLICATION_EVENTS_PATH.exists():
        return []
    target = str(scene_id).strip() if scene_id else None
    rows: List[Dict[str, Any]] = []
    with open(PUBLICATION_EVENTS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if target and row.get("scene_id") != target:
                continue
            rows.append(row)
    if limit and limit > 0:
        rows = rows[-limit:]
    return list(reversed(rows))


def _update_status_snapshot(event: Dict[str, Any]) -> None:
    PUBLICATION_STATUS_DIR.mkdir(parents=True, exist_ok=True)
    path = PUBLICATION_STATUS_DIR / f"{_safe_scene_id(event['scene_id'])}.json"
    current = load_publication_status(event["scene_id"])
    platforms = current.setdefault("platforms", {})
    platform_row = platforms.setdefault(event["platform"], {})
    platform_row.update(
        {
            "status": event["status"],
            "updated_at": event["timestamp"],
            "operator": event.get("operator"),
            "external_id": event.get("external_id"),
            "receipt_path": event.get("receipt_path"),
            "package_path": event.get("package_path"),
            "notes": event.get("notes"),
            "rejection_reason": event.get("rejection_reason"),
            "metadata": event.get("metadata") or {},
            "last_event_id": event.get("event_id"),
        }
    )
    current["scene_id"] = event["scene_id"]
    current["updated_at"] = event["timestamp"]
    current["events_count"] = int(current.get("events_count", 0) or 0) + 1
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
    tmp.replace(path)


def _safe_scene_id(scene_id: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in str(scene_id or ""))[:120]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
