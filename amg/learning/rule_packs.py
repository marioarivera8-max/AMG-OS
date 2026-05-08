"""
Rule-pack registry and activation helpers.

Rule packs are operator-reviewed metadata heuristics that can be activated in
canary/full mode without redeploying code. They are persisted under data/training
and tracked in the training registry for auditability.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from amg.config import (
    RULE_CANARY_PCT,
    TRAINING_ACTIVE_RULE_PACK_PATH,
    TRAINING_RULE_PACKS_DIR,
)
from amg.learning.training_registry import record_training_artifact


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(s: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_." else "_" for c in (s or "rule_pack"))
    return out[:120] or "rule_pack"


def _normalize_pack(raw: Dict[str, Any], fallback_id: str) -> Dict[str, Any]:
    constraints = raw.get("constraints") if isinstance(raw.get("constraints"), dict) else {}
    return {
        "rule_pack_id": str(raw.get("rule_pack_id") or fallback_id).strip() or fallback_id,
        "created_at_utc": str(raw.get("created_at_utc") or utc_now_iso()),
        "description": str(raw.get("description") or "").strip(),
        "source": str(raw.get("source") or "manual"),
        "constraints": {
            "banned_title_terms": _to_str_list(constraints.get("banned_title_terms")),
            "title_prefixes": _to_str_list(constraints.get("title_prefixes")),
            "title_suffixes": _to_str_list(constraints.get("title_suffixes")),
            "required_title_tokens": _to_str_list(constraints.get("required_title_tokens")),
            "preferred_title_styles": _to_str_list(constraints.get("preferred_title_styles")),
            "description_phrase_boost": _to_str_list(constraints.get("description_phrase_boost")),
            "category_boost": _to_str_list(constraints.get("category_boost")),
            "tag_boost": _to_str_list(constraints.get("tag_boost")),
            "description_min_chars": _to_int_or_none(constraints.get("description_min_chars")),
            "description_max_chars": _to_int_or_none(constraints.get("description_max_chars")),
            "retrieval_stage": _normalize_retrieval_stage(constraints.get("retrieval_stage")),
            "retrieval_top_k": _normalize_retrieval_top_k(constraints.get("retrieval_top_k")),
        },
        "metrics": raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {},
        "notes": str(raw.get("notes") or "").strip() or None,
    }


def _to_str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen = set()
    for item in value:
        s = " ".join(str(item or "").strip().split())
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _to_int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None


def _normalize_retrieval_stage(value: Any) -> str:
    stage = str(value or "titles").strip().lower()
    if stage not in {"off", "titles", "titles_description", "full"}:
        return "titles"
    return stage


def _normalize_retrieval_top_k(value: Any) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        out = 3
    return max(1, min(out, 5))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pack_path(rule_pack_id: str) -> Path:
    return TRAINING_RULE_PACKS_DIR / f"{_safe_name(rule_pack_id)}.json"


def save_rule_pack(
    *,
    rule_pack_id: str,
    constraints: Dict[str, Any],
    description: str = "",
    source: str = "manual",
    metrics: Optional[Dict[str, Any]] = None,
    notes: Optional[str] = None,
) -> Path:
    TRAINING_RULE_PACKS_DIR.mkdir(parents=True, exist_ok=True)
    pack = _normalize_pack(
        {
            "rule_pack_id": rule_pack_id,
            "created_at_utc": utc_now_iso(),
            "description": description,
            "source": source,
            "constraints": constraints or {},
            "metrics": metrics or {},
            "notes": notes,
        },
        fallback_id=_safe_name(rule_pack_id),
    )
    path = _pack_path(pack["rule_pack_id"])
    with open(path, "w") as f:
        json.dump(pack, f, indent=2)
    record_training_artifact(
        "rule_pack_saved",
        path,
        metadata={
            "rule_pack_id": pack["rule_pack_id"],
            "source": source,
        },
    )
    return path


def load_rule_pack(rule_pack_id: str) -> Optional[Dict[str, Any]]:
    path = _pack_path(rule_pack_id)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            raw = json.load(f)
    except Exception:
        return None
    return _normalize_pack(raw if isinstance(raw, dict) else {}, fallback_id=_safe_name(rule_pack_id))


def list_rule_packs(limit: int = 50) -> List[Dict[str, Any]]:
    if not TRAINING_RULE_PACKS_DIR.exists():
        return []
    paths = sorted(
        TRAINING_RULE_PACKS_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    out: List[Dict[str, Any]] = []
    for p in paths[: max(1, int(limit))]:
        try:
            with open(p) as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                out.append(_normalize_pack(raw, fallback_id=p.stem))
        except Exception:
            continue
    return out


def get_active_rule_pointer() -> Dict[str, Any]:
    if not TRAINING_ACTIVE_RULE_PACK_PATH.exists():
        return {
            "enabled": False,
            "mode": "off",
            "rule_pack_id": None,
            "canary_pct": 0.0,
            "updated_at_utc": None,
        }
    try:
        with open(TRAINING_ACTIVE_RULE_PACK_PATH) as f:
            raw = json.load(f)
    except Exception:
        return {
            "enabled": False,
            "mode": "off",
            "rule_pack_id": None,
            "canary_pct": 0.0,
            "updated_at_utc": None,
        }
    mode = str(raw.get("mode") or "off").strip().lower()
    if mode not in {"off", "canary", "full"}:
        mode = "off"
    canary_pct = 0.0
    try:
        canary_pct = float(raw.get("canary_pct", RULE_CANARY_PCT))
    except (TypeError, ValueError):
        canary_pct = RULE_CANARY_PCT
    canary_pct = max(0.0, min(canary_pct, 100.0))
    enabled = bool(raw.get("enabled")) and bool(raw.get("rule_pack_id")) and mode != "off"
    return {
        "enabled": enabled,
        "mode": mode,
        "rule_pack_id": (str(raw.get("rule_pack_id")).strip() if raw.get("rule_pack_id") else None),
        "canary_pct": canary_pct,
        "updated_at_utc": raw.get("updated_at_utc"),
    }


def set_active_rule_pack(*, rule_pack_id: str, mode: str = "canary", canary_pct: Optional[float] = None) -> Path:
    clean_mode = str(mode or "canary").strip().lower()
    if clean_mode not in {"canary", "full"}:
        raise ValueError(f"Unsupported mode: {mode}")
    if not load_rule_pack(rule_pack_id):
        raise FileNotFoundError(f"Rule pack not found: {rule_pack_id}")
    pct = RULE_CANARY_PCT if canary_pct is None else float(canary_pct)
    pct = max(0.0, min(float(pct), 100.0))
    TRAINING_ACTIVE_RULE_PACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "enabled": True,
        "mode": clean_mode,
        "rule_pack_id": rule_pack_id,
        "canary_pct": pct,
        "updated_at_utc": utc_now_iso(),
    }
    with open(TRAINING_ACTIVE_RULE_PACK_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    record_training_artifact(
        "rule_pack_activated",
        TRAINING_ACTIVE_RULE_PACK_PATH,
        metadata=payload,
    )
    return TRAINING_ACTIVE_RULE_PACK_PATH


def deactivate_rule_pack() -> Path:
    TRAINING_ACTIVE_RULE_PACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "enabled": False,
        "mode": "off",
        "rule_pack_id": None,
        "canary_pct": 0.0,
        "updated_at_utc": utc_now_iso(),
    }
    with open(TRAINING_ACTIVE_RULE_PACK_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    record_training_artifact(
        "rule_pack_deactivated",
        TRAINING_ACTIVE_RULE_PACK_PATH,
        metadata=payload,
    )
    return TRAINING_ACTIVE_RULE_PACK_PATH


def resolve_rule_pack_for_scene(scene_id: str) -> Dict[str, Any]:
    """
    Resolve whether a scene should get an active rule pack.

    Returns:
      {
        "applied": bool,
        "reason": str,
        "rule_pack_id": str|None,
        "mode": str,
        "canary_pct": float,
        "rule_pack": dict|None,
      }
    """
    ptr = get_active_rule_pointer()
    if not ptr.get("enabled"):
        return {
            "applied": False,
            "reason": "disabled",
            "rule_pack_id": None,
            "mode": ptr.get("mode", "off"),
            "canary_pct": float(ptr.get("canary_pct") or 0.0),
            "rule_pack": None,
        }
    rule_pack_id = str(ptr.get("rule_pack_id") or "").strip()
    pack = load_rule_pack(rule_pack_id)
    if not pack:
        return {
            "applied": False,
            "reason": "missing_rule_pack",
            "rule_pack_id": rule_pack_id or None,
            "mode": ptr.get("mode", "off"),
            "canary_pct": float(ptr.get("canary_pct") or 0.0),
            "rule_pack": None,
        }
    mode = str(ptr.get("mode") or "off").strip().lower()
    if mode == "full":
        return {
            "applied": True,
            "reason": "full_mode",
            "rule_pack_id": rule_pack_id,
            "mode": mode,
            "canary_pct": float(ptr.get("canary_pct") or 0.0),
            "rule_pack": pack,
        }
    pct = float(ptr.get("canary_pct") or 0.0)
    score = _stable_pct(scene_id)
    applied = score < pct
    return {
        "applied": applied,
        "reason": "canary_pass" if applied else "canary_skip",
        "rule_pack_id": rule_pack_id,
        "mode": mode,
        "canary_pct": pct,
        "rule_pack": pack if applied else None,
        "canary_bucket": round(score, 3),
    }


def _stable_pct(token: str) -> float:
    digest = hashlib.sha1(str(token or "").encode("utf-8")).hexdigest()
    v = int(digest[:8], 16) % 10000
    return v / 100.0
