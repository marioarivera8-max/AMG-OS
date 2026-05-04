"""
Append-only audit log for compliance events.

Records significant decisions and their justifications. Never deleted.

Used for:
- Compliance trail (e.g., "scene X processed on date Y with 2257 verified")
- Forensic analysis if questions arise
- Operator accountability across multi-Mac fleet
"""
import json
from datetime import datetime
from pathlib import Path

from amg.config import DATA_DIR, DEFAULT_OPERATOR, DEFAULT_MACHINE_ID

AUDIT_LOG_PATH = DATA_DIR / "audit_log.jsonl"


def audit_event(
    event_type: str,
    scene_id: str = None,
    details: dict = None,
    operator: str = None,
    machine_id: str = None,
) -> None:
    """
    Append an event to the audit log.

    Args:
        event_type: Event category, e.g.:
                    'scene_processed', 'scene_aborted',
                    '2257_verified', '2257_missing',
                    'fallback_d_used', 'manual_override'
        scene_id: Optional scene identifier
        details: Optional dict of extra context
        operator: Override default operator name
        machine_id: Override default machine name
    """
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "event_type": event_type,
        "scene_id": scene_id,
        "operator": operator or DEFAULT_OPERATOR,
        "machine_id": machine_id or DEFAULT_MACHINE_ID,
        "details": details or {},
    }

    try:
        with open(AUDIT_LOG_PATH, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        # Audit log should never crash the main flow
        pass
