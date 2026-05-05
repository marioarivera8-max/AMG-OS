"""
Simple local registry for training artifacts and runs.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from amg.config import TRAINING_REGISTRY_PATH


def record_training_artifact(
    artifact_type: str,
    artifact_path: Path,
    metadata: Optional[Dict[str, object]] = None,
) -> dict:
    TRAINING_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "artifact_type": artifact_type,
        "artifact_path": str(artifact_path),
        "metadata": metadata or {},
    }
    with open(TRAINING_REGISTRY_PATH, "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def read_training_registry(limit: int = 1000) -> List[dict]:
    if not TRAINING_REGISTRY_PATH.exists():
        return []
    rows: List[dict] = []
    with open(TRAINING_REGISTRY_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    if limit > 0:
        return rows[-limit:]
    return rows


def summarize_training_registry(limit: int = 200) -> dict:
    rows = read_training_registry(limit=limit)
    by_type: Dict[str, int] = {}
    latest_by_type: Dict[str, dict] = {}
    latest_existing_by_type: Dict[str, dict] = {}
    for row in rows:
        t = str(row.get("artifact_type") or "unknown")
        by_type[t] = by_type.get(t, 0) + 1
        latest_by_type[t] = row
        p = row.get("artifact_path")
        if isinstance(p, str) and p and Path(p).exists():
            latest_existing_by_type[t] = row

    return {
        "total_rows": len(rows),
        "by_type": by_type,
        "latest_by_type": latest_by_type,
        "latest_existing_by_type": latest_existing_by_type,
    }
