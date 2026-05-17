"""Track estimated daily API spend for translate polish."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from amg.translate import config
from amg.translate.state_io import atomic_write_json


def _today_key() -> str:
    return date.today().isoformat()


def spend_file_path() -> Path:
    return config.SPEND_TRACKER_FILE


def load_spend_raw() -> dict[str, Any]:
    path = spend_file_path()
    if not path.exists():
        return {}
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def get_today_summary() -> tuple[int, float]:
    """Returns (batch_count_today, estimated_cost_usd_today)."""
    raw = load_spend_raw()
    key = _today_key()
    entry = raw.get(key)
    if not isinstance(entry, dict):
        return 0, 0.0
    batches = int(entry.get("batches", 0) or 0)
    cost = float(entry.get("estimated_cost_usd", 0.0) or 0.0)
    return batches, cost


def can_charge_batch() -> bool:
    """Returns False when today's recorded spend already meets or exceeds the cap."""
    _, spent = get_today_summary()
    cap = float(config.DAILY_SPEND_CAP_USD)
    return spent < cap


def record_batch_charge() -> None:
    """Add one batch estimate to today's tally (called after successful API batch)."""
    path = spend_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = load_spend_raw()
    key = _today_key()
    entry = raw.get(key)
    if not isinstance(entry, dict):
        entry = {"batches": 0, "estimated_cost_usd": 0.0}
    batches = int(entry.get("batches", 0) or 0) + 1
    prev_cost = float(entry.get("estimated_cost_usd", 0.0) or 0.0)
    cost = prev_cost + float(config.ESTIMATED_COST_PER_API_BATCH_USD)
    raw[key] = {"batches": batches, "estimated_cost_usd": round(cost, 4)}
    raw["_updated"] = datetime.now().isoformat(timespec="seconds")
    atomic_write_json(path, raw)
