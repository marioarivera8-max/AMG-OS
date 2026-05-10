"""Cover eligibility validation before final output.

This is the backend guardrail for high-scoring but impossible cover picks:
the model can occasionally emit contradictory fields such as
``TYPE=PENETRATION`` while also saying penetration is not visible. Those
frames must not reach saved cover output.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from amg.config import (
    COVER_MIN_PENETRATION_CONFIDENCE,
    COVER_PRESENCE_GATE_ENABLED,
    COVER_PRESENCE_GATE_MODE,
    COVER_SUSPICIOUS_RECHECK_ENABLED,
    COVER_SUSPICIOUS_RECHECK_MAX,
)
from amg.utils.logging import get_logger

log = get_logger("output.cover_validation")


EXPLICIT_ACTION_EVIDENCE = {"EXPLICIT_PENETRATION"}
SUSPICIOUS_ACTION_EVIDENCE = {"OCCLUDED", "WATER_OCCLUSION", "NONE"}


def validate_cover_candidate(entry: dict) -> dict:
    """Attach and return deterministic cover validation metadata."""
    validation = _base_validation()
    scored = entry.get("scored_frame") if isinstance(entry, dict) else None

    if not COVER_PRESENCE_GATE_ENABLED:
        validation["eligible"] = True
        validation["validator_source"] = "disabled"
        entry["cover_validation"] = validation
        return validation

    if scored is None:
        _reject(validation, "NO_SCORED_FRAME")
        entry["cover_validation"] = validation
        return validation
    if not getattr(scored, "parse_succeeded", False):
        _reject(validation, "PARSE_FAILED")
        entry["cover_validation"] = validation
        return validation
    if getattr(scored, "tier_a_fail_code", None):
        _reject(validation, f"TIER_A_FAIL_{getattr(scored, 'tier_a_fail_code')}")
        entry["cover_validation"] = validation
        return validation

    score = _float(getattr(scored, "score", 0.0), 0.0)
    type_ = str(getattr(scored, "type_", "") or "").strip().upper()
    evidence = str(getattr(scored, "action_evidence", "NONE") or "NONE").strip().upper()
    pen_visible = bool(getattr(scored, "penetration_visible", False))
    pen_conf = _float(getattr(scored, "penetration_confidence", 0.0), 0.0)

    validation["score"] = round(score, 3)
    validation["type"] = type_
    validation["action_evidence"] = evidence
    validation["penetration_visible"] = pen_visible
    validation["penetration_confidence"] = round(pen_conf, 3)

    if score <= 0:
        _reject(validation, "NON_POSITIVE_SCORE")

    if type_ == "PENETRATION":
        if not pen_visible:
            _reject(validation, "PENETRATION_TYPE_WITHOUT_VISIBLE_PENETRATION")
        if pen_conf < float(COVER_MIN_PENETRATION_CONFIDENCE):
            _reject(validation, "LOW_PENETRATION_CONFIDENCE")
        if evidence not in EXPLICIT_ACTION_EVIDENCE:
            _reject(validation, f"BAD_PENETRATION_EVIDENCE_{evidence or 'NONE'}")
    elif type_ in {"SEX_ACT", "FINISH", "BUILDUP"}:
        if evidence in SUSPICIOUS_ACTION_EVIDENCE and score >= 80.0:
            _warn(validation, f"SUSPICIOUS_ACTION_EVIDENCE_{evidence}")
            validation["needs_recheck"] = True
        if not pen_visible and evidence == "NONE" and score >= 85.0:
            _warn(validation, "HIGH_SCORE_WEAK_ACTION_EVIDENCE")
            validation["needs_recheck"] = True
    elif score >= 80.0 and type_ in {"UNKNOWN", "COMPOSITION"}:
        _warn(validation, "HIGH_SCORE_NON_ACTION_TYPE")
        validation["needs_recheck"] = True

    if validation["reject_codes"] and COVER_PRESENCE_GATE_MODE == "demote":
        validation["warnings"].extend(validation["reject_codes"])
        validation["reject_codes"] = []
        validation["eligible"] = True
        validation["needs_recheck"] = True

    validation["eligible"] = not validation["reject_codes"]
    entry["cover_validation"] = validation
    return validation


def apply_cover_validation(entries: List[dict]) -> Dict[str, Any]:
    for entry in entries or []:
        if isinstance(entry, dict):
            validate_cover_candidate(entry)
    return summarize_cover_validation(entries or [])


def summarize_cover_validation(entries: List[dict]) -> Dict[str, Any]:
    total = 0
    eligible = 0
    rejected = 0
    suspicious = 0
    rechecked = 0
    recheck_rejected = 0
    reject_counts: Dict[str, int] = {}
    warning_counts: Dict[str, int] = {}
    evidence: List[dict] = []

    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        v = entry.get("cover_validation")
        if not isinstance(v, dict):
            continue
        total += 1
        if v.get("eligible"):
            eligible += 1
        else:
            rejected += 1
        if v.get("needs_recheck") or v.get("warnings"):
            suspicious += 1
        if v.get("recheck_result"):
            rechecked += 1
            if not v.get("eligible"):
                recheck_rejected += 1
        for code in v.get("reject_codes") or []:
            reject_counts[str(code)] = reject_counts.get(str(code), 0) + 1
        for code in v.get("warnings") or []:
            warning_counts[str(code)] = warning_counts.get(str(code), 0) + 1
        if (v.get("reject_codes") or v.get("warnings")) and len(evidence) < 20:
            scored = entry.get("scored_frame")
            evidence.append(
                {
                    "timestamp_sec": entry.get("timestamp_sec"),
                    "score": _float(getattr(scored, "score", 0.0), 0.0) if scored else 0.0,
                    "type": getattr(scored, "type_", None) if scored else None,
                    "reject_codes": list(v.get("reject_codes") or []),
                    "warnings": list(v.get("warnings") or []),
                    "eligible": bool(v.get("eligible")),
                }
            )

    return {
        "enabled": bool(COVER_PRESENCE_GATE_ENABLED),
        "mode": COVER_PRESENCE_GATE_MODE,
        "total_checked": total,
        "eligible": eligible,
        "rejected": rejected,
        "suspicious": suspicious,
        "rechecked": rechecked,
        "recheck_rejected": recheck_rejected,
        "reject_counts": dict(sorted(reject_counts.items())),
        "warning_counts": dict(sorted(warning_counts.items())),
        "evidence": evidence,
    }


def recheck_suspicious_selected(
    selected: List[dict],
    *,
    ai_client: Any,
    system_prompt: Optional[str] = None,
    max_rechecks: int = COVER_SUSPICIOUS_RECHECK_MAX,
) -> Dict[str, Any]:
    """Run a bounded local vision recheck on suspicious selected candidates."""
    if not COVER_PRESENCE_GATE_ENABLED or not COVER_SUSPICIOUS_RECHECK_ENABLED:
        return {"enabled": False, "attempted": 0, "rejected": 0, "errors": 0}
    if ai_client is None or max_rechecks <= 0:
        return {"enabled": True, "attempted": 0, "rejected": 0, "errors": 0, "reason": "unavailable"}

    attempted = 0
    rejected = 0
    errors = 0
    prompt = build_presence_recheck_prompt()

    for entry in selected or []:
        if attempted >= int(max_rechecks):
            break
        validation = entry.get("cover_validation")
        if not isinstance(validation, dict):
            validation = validate_cover_candidate(entry)
        if not validation.get("eligible") or not validation.get("needs_recheck"):
            continue
        frame = entry.get("frame")
        if frame is None:
            validation["recheck_result"] = {
                "success": False,
                "error": "missing_frame",
            }
            continue
        attempted += 1
        try:
            resp = ai_client.score_frame(frame, prompt, system_prompt=system_prompt)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            validation["recheck_result"] = {"success": False, "error": str(exc)[:200]}
            continue
        if not getattr(resp, "success", False):
            errors += 1
            validation["recheck_result"] = {
                "success": False,
                "error": str(getattr(resp, "error_code", None) or getattr(resp, "error_message", "") or "ai_error")[:200],
            }
            continue
        parsed = parse_presence_recheck_response(getattr(resp, "raw_text", "") or "")
        validation["recheck_result"] = parsed
        if _recheck_rejects(parsed):
            _reject(validation, f"RECHECK_{parsed.get('reason_code') or 'FAILED_PRESENCE'}")
            validation["eligible"] = False
            validation["validator_source"] = "ollama_suspicious_recheck"
            rejected += 1
        else:
            validation["validator_source"] = "ollama_suspicious_recheck"
            validation["needs_recheck"] = False

    return {
        "enabled": True,
        "attempted": attempted,
        "rejected": rejected,
        "errors": errors,
        "max_rechecks": int(max_rechecks),
    }


def build_presence_recheck_prompt() -> str:
    return """Inspect this adult VOD candidate cover frame for basic cover validity.

Only answer what is visible in the image. The purpose is to reject empty-set,
transition, or background-only frames.

OUTPUT FORMAT:
PERFORMER_VISIBLE: <yes|no>
NUDITY_OR_SEX_ACT_VISIBLE: <yes|no>
EMPTY_SET: <yes|no>
REASON_CODE: <OK|EMPTY_SET|NO_PERFORMER|NO_RETAIL_SUBJECT|UNCLEAR>
CONFIDENCE: <0.00-1.00>
END
"""


def parse_presence_recheck_response(raw_text: str) -> Dict[str, Any]:
    performer = _yes_no(_line(raw_text, "PERFORMER_VISIBLE"))
    retail_subject = _yes_no(_line(raw_text, "NUDITY_OR_SEX_ACT_VISIBLE"))
    empty = _yes_no(_line(raw_text, "EMPTY_SET"))
    reason = _clean_code(_line(raw_text, "REASON_CODE")) or "UNCLEAR"
    conf = _float(_line(raw_text, "CONFIDENCE"), 0.0)
    return {
        "success": True,
        "performer_visible": performer,
        "nudity_or_sex_act_visible": retail_subject,
        "empty_set": empty,
        "reason_code": reason,
        "confidence": round(max(0.0, min(1.0, conf)), 3),
    }


def _recheck_rejects(parsed: dict) -> bool:
    if parsed.get("empty_set") is True:
        return True
    if parsed.get("performer_visible") is False:
        return True
    if parsed.get("nudity_or_sex_act_visible") is False:
        return True
    reason = str(parsed.get("reason_code") or "").upper()
    return reason in {"EMPTY_SET", "NO_PERFORMER", "NO_RETAIL_SUBJECT"}


def _base_validation() -> dict:
    return {
        "eligible": True,
        "reject_codes": [],
        "warnings": [],
        "needs_recheck": False,
        "recheck_result": None,
        "validator_source": "deterministic",
    }


def _reject(validation: dict, code: str) -> None:
    code = _clean_code(code)
    if code and code not in validation["reject_codes"]:
        validation["reject_codes"].append(code)
    validation["eligible"] = False


def _warn(validation: dict, code: str) -> None:
    code = _clean_code(code)
    if code and code not in validation["warnings"]:
        validation["warnings"].append(code)


def _line(raw: str, name: str) -> str:
    m = re.search(rf"^\s*{re.escape(name)}:\s*(.+)$", raw or "", re.IGNORECASE | re.MULTILINE)
    return (m.group(1).strip() if m else "")


def _yes_no(raw: str) -> Optional[bool]:
    val = str(raw or "").strip().lower()
    if val in {"yes", "true", "1"}:
        return True
    if val in {"no", "false", "0"}:
        return False
    return None


def _clean_code(raw: Any) -> str:
    code = str(raw or "").strip().upper().replace("-", "_").replace(" ", "_")
    code = re.sub(r"[^A-Z0-9_]+", "", code)
    code = re.sub(r"_+", "_", code).strip("_")
    return code


def _float(raw: Any, default: float) -> float:
    try:
        if raw is None:
            return default
        return float(raw)
    except (TypeError, ValueError):
        return default
