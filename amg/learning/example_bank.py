"""
Approved metadata example-bank builder + retrieval.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from amg.config import (
    DATA_DIR,
    DECISION_LOGS_DIR,
    OPERATOR_FEEDBACK_PATH,
    REVIEWED_DIR,
    TRAINING_EXAMPLES_DIR,
    TRAINING_TEXT_DIR,
)
from amg.learning.training_registry import record_training_artifact


@dataclass
class ExampleBankExportStats:
    reviewed_rows_scanned: int
    exported_rows: int
    skipped_rows: int
    output_jsonl_path: Path
    prompt_json_path: Path


def export_approved_example_bank(
    *,
    days_back: int = 180,
    max_rows: Optional[int] = None,
    output_name: str = "approved_example_bank.jsonl",
    prompt_name: str = "approved_example_bank_prompt.json",
) -> ExampleBankExportStats:
    reviewed_rows = _load_reviewed_rows(days_back=days_back)
    feedback_by_scene = _load_feedback_by_scene(days_back=days_back)
    TRAINING_EXAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    TRAINING_TEXT_DIR.mkdir(parents=True, exist_ok=True)
    out_jsonl = TRAINING_EXAMPLES_DIR / _safe_name(output_name)
    if out_jsonl.suffix != ".jsonl":
        out_jsonl = out_jsonl.with_suffix(".jsonl")
    out_prompt = TRAINING_TEXT_DIR / _safe_name(prompt_name)
    if out_prompt.suffix != ".json":
        out_prompt = out_prompt.with_suffix(".json")

    exported = 0
    skipped = 0
    prompt_rows: List[Dict[str, Any]] = []
    preserved_seed_rows = _load_preserved_seed_rows(out_jsonl)
    with open(out_jsonl, "w", encoding="utf-8") as f:
        for seed_row in preserved_seed_rows:
            f.write(json.dumps(seed_row, ensure_ascii=True) + "\n")
            prompt_rows.append(_to_prompt_row(seed_row))
            exported += 1
        for row in reviewed_rows:
            ex = _to_example_record(row, feedback_by_scene=feedback_by_scene)
            if ex is None:
                skipped += 1
                continue
            f.write(json.dumps(ex, ensure_ascii=True) + "\n")
            prompt_rows.append(_to_prompt_row(ex))
            exported += 1
            if max_rows and exported >= int(max_rows):
                break

    prompt_payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "rows": prompt_rows,
    }
    with open(out_prompt, "w", encoding="utf-8") as f:
        json.dump(prompt_payload, f, indent=2)

    stats = ExampleBankExportStats(
        reviewed_rows_scanned=len(reviewed_rows),
        exported_rows=exported,
        skipped_rows=skipped,
        output_jsonl_path=out_jsonl,
        prompt_json_path=out_prompt,
    )
    record_training_artifact(
        "example_bank_export",
        out_jsonl,
        metadata={
            "days_back": int(days_back),
            "reviewed_rows_scanned": stats.reviewed_rows_scanned,
            "exported_rows": stats.exported_rows,
            "skipped_rows": stats.skipped_rows,
            "preserved_seed_rows": len(preserved_seed_rows),
            "prompt_json_path": str(out_prompt),
        },
    )
    return stats


def _load_preserved_seed_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            source = row.get("source") if isinstance(row.get("source"), dict) else {}
            source_type = str(source.get("source_type") or "").strip().lower()
            if source_type in {"operator_word_doc", "manual_seed", "vod_cover_seed_zip"}:
                out.append(row)
    return out


def retrieve_top_k_examples(
    *,
    studio: Optional[str],
    scene_type: Optional[str],
    genres: List[str],
    position_summary: Dict[str, int],
    performers: List[str],
    top_k: int = 3,
    max_chars_per_example: int = 320,
    bank_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    rows = _load_example_bank_rows(bank_path=bank_path)
    if not rows:
        return []
    query = _query_tokens(
        studio=studio,
        scene_type=scene_type,
        genres=genres,
        position_summary=position_summary,
        performers=performers,
    )
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for row in rows:
        score = _similarity_score(query, row)
        if score <= 0:
            continue
        scored.append((score, row))
    scored.sort(
        key=lambda x: (
            -x[0],
            -_source_signal_score(x[1]),
            str(x[1].get("scene_id") or ""),
        )
    )
    out: List[Dict[str, Any]] = []
    for score, row in scored[: max(1, int(top_k))]:
        meta = row.get("approved_metadata") if isinstance(row.get("approved_metadata"), dict) else {}
        text = str(meta.get("long_description") or "").strip()
        if len(text) > max_chars_per_example:
            text = text[: max(80, int(max_chars_per_example))].rstrip() + "..."
        out.append(
            {
                "scene_id": row.get("scene_id"),
                "studio": (row.get("scene_features") or {}).get("studio"),
                "scene_type": (row.get("scene_features") or {}).get("scene_type"),
                "genres": (row.get("scene_features") or {}).get("genres") or [],
                "title": str(meta.get("title") or "").strip(),
                "long_description": text,
                "tags": meta.get("tags") or [],
                "categories": meta.get("categories") or [],
                "quality_score": round(float(score), 4),
            }
        )
    return out


def _load_example_bank_rows(*, bank_path: Optional[Path]) -> List[Dict[str, Any]]:
    src = bank_path or (TRAINING_EXAMPLES_DIR / "approved_example_bank.jsonl")
    if not src.exists():
        # Best-effort auto-build so retrieval can work without manual export.
        try:
            export_approved_example_bank()
        except Exception:
            return []
    if not src.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with open(src, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _load_reviewed_rows(*, days_back: int) -> List[dict]:
    if not REVIEWED_DIR.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days_back)))
    out: List[dict] = []
    for p in REVIEWED_DIR.glob("*.json"):
        try:
            row = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        ts = _parse_ts(str(row.get("timestamp") or ""))
        if ts and ts < cutoff:
            continue
        out.append(row)
    out.sort(key=lambda x: str(x.get("timestamp") or ""), reverse=True)
    return out


def _load_feedback_by_scene(*, days_back: int) -> Dict[str, Dict[str, int]]:
    if not OPERATOR_FEEDBACK_PATH.exists():
        return {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days_back)))
    out: Dict[str, Dict[str, int]] = {}
    with open(OPERATOR_FEEDBACK_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            ts = _parse_ts(str(row.get("timestamp") or ""))
            if ts and ts < cutoff:
                continue
            sid = str(row.get("scene_id") or "").strip()
            if not sid:
                continue
            decision = str(((row.get("operator") or {}).get("decision")) or "").strip().lower()
            bucket = out.setdefault(sid, {"keep": 0, "reject": 0, "maybe": 0, "other": 0})
            if decision in {"keep", "reject", "maybe"}:
                bucket[decision] += 1
            else:
                bucket["other"] += 1
    return out


def _to_example_record(row: dict, *, feedback_by_scene: Dict[str, Dict[str, int]]) -> Optional[Dict[str, Any]]:
    scene_id = str(row.get("scene_id") or "").strip()
    if not scene_id:
        return None
    title = str(row.get("title_override") or "").strip()
    long_description = str(row.get("long_description") or "").strip()
    tags = _parse_csv_tokens(row.get("tags_csv"), lowercase=True)
    categories = _parse_csv_tokens(row.get("categories_csv"), title_case=True)
    if not title or not long_description or not tags or not categories:
        return None

    decision = _load_decision_log(scene_id)
    insight = _load_scene_insight(scene_id)
    features = _build_scene_features(row=row, decision=decision, insight=insight)
    quality = _build_quality_metadata(row=row, feedback=feedback_by_scene.get(scene_id) or {})

    # "Approved" corpus should favor scenes with positive review outcomes.
    if quality.get("metadata_acceptance_score", 0.0) < 0.2:
        return None
    if quality.get("feedback_reject_rate", 0.0) >= 0.8:
        return None

    return {
        "example_id": f"{scene_id}:{str(row.get('timestamp') or '')}",
        "scene_id": scene_id,
        "source": {
            "reviewed_path": str(REVIEWED_DIR / f"{_safe_scene_id(scene_id)}.json"),
            "decision_log_path": str(DECISION_LOGS_DIR / f"{_safe_scene_id(scene_id)}.json"),
            "feedback_path": str(OPERATOR_FEEDBACK_PATH) if OPERATOR_FEEDBACK_PATH.exists() else None,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "scene_features": features,
        "approved_metadata": {
            "title": title,
            "long_description": long_description,
            "tags": tags,
            "categories": categories,
        },
        "quality": quality,
    }


def _build_scene_features(*, row: dict, decision: Optional[dict], insight: Optional[dict]) -> Dict[str, Any]:
    input_blob = (decision or {}).get("input") if isinstance((decision or {}).get("input"), dict) else {}
    scene_type = str(input_blob.get("scene_type") or "").strip() or "STANDARD"
    genres_raw = input_blob.get("genres") if isinstance(input_blob.get("genres"), list) else []
    genres = [str(g).upper().strip() for g in genres_raw if str(g).strip()]
    positions = _collect_positions(row=row, decision=decision)
    performers = [str(p).strip() for p in (input_blob.get("performers") or []) if str(p).strip()]
    return {
        "studio": str(input_blob.get("studio") or "").strip() or None,
        "scene_type": scene_type,
        "genres": genres,
        "positions": positions,
        "performers": performers,
        "title_tone": str(row.get("title_tone") or "").strip() or None,
        "rule_pack_id": str(row.get("rule_pack_id") or "").strip() or None,
        "insight_mood": str((insight or {}).get("mood") or "").strip() or None,
        "insight_setting": str((insight or {}).get("setting") or "").strip() or None,
    }


def _collect_positions(*, row: dict, decision: Optional[dict]) -> List[str]:
    counts: Dict[str, int] = {}
    per_cover = row.get("per_cover") if isinstance(row.get("per_cover"), dict) else {}
    for _fname, item in per_cover.items():
        if not isinstance(item, dict):
            continue
        pos = str(item.get("pos") or "").strip().upper()
        if not pos:
            continue
        counts[pos] = counts.get(pos, 0) + 1
    saved_covers = ((decision or {}).get("outcomes") or {}).get("saved_covers") or []
    for item in saved_covers:
        if not isinstance(item, dict):
            continue
        pos = str(item.get("position_label") or "").strip().upper()
        if not pos or pos == "OTHER":
            continue
        counts[pos] = counts.get(pos, 0) + 1
    ordered = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return [x[0] for x in ordered[:6]]


def _build_quality_metadata(*, row: dict, feedback: Dict[str, int]) -> Dict[str, Any]:
    mv = row.get("metadata_validation") if isinstance(row.get("metadata_validation"), dict) else {}
    blockers = mv.get("blockers") if isinstance(mv.get("blockers"), list) else []
    keep = int(feedback.get("keep", 0) or 0)
    reject = int(feedback.get("reject", 0) or 0)
    maybe = int(feedback.get("maybe", 0) or 0)
    denom = max(1, keep + reject + maybe)
    acceptance_label = str(row.get("metadata_acceptance_label") or "").strip().lower() or "unknown"
    acceptance_score = 1.0 if acceptance_label == "unchanged" else (0.65 if acceptance_label == "edited" else 0.5)
    if bool(mv.get("overall_ready")):
        acceptance_score = min(1.0, acceptance_score + 0.1)
    if blockers:
        acceptance_score = max(0.0, acceptance_score - min(0.6, 0.08 * len(blockers)))
    return {
        "metadata_ready": bool(mv.get("overall_ready")),
        "metadata_blocker_count": len(blockers),
        "metadata_acceptance_label": acceptance_label,
        "metadata_acceptance_score": round(acceptance_score, 4),
        "title_edit_distance": _safe_float(row.get("title_edit_distance"), default=0.0),
        "description_edit_distance": _safe_float(row.get("description_edit_distance"), default=0.0),
        "feedback_keep_rate": round(keep / denom, 4),
        "feedback_reject_rate": round(reject / denom, 4),
        "feedback_maybe_rate": round(maybe / denom, 4),
        "metadata_edit_reason_codes": _parse_reason_codes(row.get("metadata_edit_reason_codes")),
    }


def _to_prompt_row(ex: Dict[str, Any]) -> Dict[str, Any]:
    sf = ex.get("scene_features") if isinstance(ex.get("scene_features"), dict) else {}
    md = ex.get("approved_metadata") if isinstance(ex.get("approved_metadata"), dict) else {}
    quality = ex.get("quality") if isinstance(ex.get("quality"), dict) else {}
    return {
        "scene_id": ex.get("scene_id"),
        "studio": sf.get("studio"),
        "scene_type": sf.get("scene_type"),
        "genres": sf.get("genres") or [],
        "positions": sf.get("positions") or [],
        "title": md.get("title"),
        "long_description": md.get("long_description"),
        "tags": md.get("tags") or [],
        "categories": md.get("categories") or [],
        "quality_score": quality.get("metadata_acceptance_score"),
    }


def _query_tokens(
    *,
    studio: Optional[str],
    scene_type: Optional[str],
    genres: List[str],
    position_summary: Dict[str, int],
    performers: List[str],
) -> Dict[str, set[str]]:
    return {
        "studio": _token_set(studio),
        "scene_type": _token_set(scene_type),
        "genres": {str(x).upper().strip() for x in (genres or []) if str(x).strip()},
        "positions": {str(k).upper().strip() for k in (position_summary or {}).keys() if str(k).strip()},
        "performers": _token_set(" ".join(performers or [])),
    }


def _similarity_score(query: Dict[str, set[str]], row: Dict[str, Any]) -> float:
    sf = row.get("scene_features") if isinstance(row.get("scene_features"), dict) else {}
    cand = {
        "studio": _token_set(sf.get("studio")),
        "scene_type": _token_set(sf.get("scene_type")),
        "genres": {str(x).upper().strip() for x in (sf.get("genres") or []) if str(x).strip()},
        "positions": {str(x).upper().strip() for x in (sf.get("positions") or []) if str(x).strip()},
        "performers": _token_set(" ".join(sf.get("performers") or [])),
    }
    weights = {
        "studio": 0.30,
        "scene_type": 0.22,
        "genres": 0.24,
        "positions": 0.14,
        "performers": 0.10,
    }
    score = 0.0
    for key, w in weights.items():
        score += w * _jaccard(query.get(key, set()), cand.get(key, set()))
    quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
    score += 0.08 * max(0.0, min(1.0, _safe_float(quality.get("metadata_acceptance_score"), default=0.0)))
    score -= 0.04 * max(0.0, min(1.0, _safe_float(quality.get("feedback_reject_rate"), default=0.0)))
    # Published-success seeds (operator-approved shipped covers) should be
    # treated as premium signals, even when they have sparse long-form text.
    score += 0.22 * _source_signal_score(row)
    return max(0.0, min(1.0, score))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _load_decision_log(scene_id: str) -> Optional[dict]:
    path = DECISION_LOGS_DIR / f"{_safe_scene_id(scene_id)}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _load_scene_insight(scene_id: str) -> Optional[dict]:
    p = DATA_DIR / "work_dirs" / _safe_scene_id(scene_id) / "insight.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _parse_csv_tokens(value: Any, *, title_case: bool = False, lowercase: bool = False) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw = [str(x).strip() for x in value]
    else:
        raw = [x.strip() for x in re.split(r"[,;\n|]", str(value))]
    out: List[str] = []
    seen = set()
    for token in raw:
        t = " ".join(token.split())
        if not t:
            continue
        if lowercase:
            t = t.lower()
        if title_case:
            t = " ".join(part.capitalize() for part in t.split())
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def _parse_reason_codes(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw = [str(x).strip().lower() for x in value]
    else:
        raw = [x.strip().lower() for x in re.split(r"[,;\n|]", str(value))]
    seen = set()
    out: List[str] = []
    for token in raw:
        t = re.sub(r"[^a-z0-9_ -]", "", token).strip().replace(" ", "_")
        if not t:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _parse_ts(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _token_set(raw: Any) -> set[str]:
    parts = re.findall(r"[a-z0-9]+", str(raw or "").lower())
    return {p for p in parts if p}


def _safe_float(value: Any, *, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float(default)
    if math.isnan(v) or math.isinf(v):
        return float(default)
    return v


def _source_signal_score(row: Dict[str, Any]) -> float:
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
    source_type = str(source.get("source_type") or "").strip().lower()
    published_flag = bool(source.get("published_success_seed"))
    if source_type == "vod_cover_seed_zip" and published_flag:
        return 1.0
    if source_type in {"operator_word_doc", "manual_seed"}:
        # keep these strong, but below shipped-cover priors
        return 0.75
    return max(0.0, min(1.0, _safe_float(quality.get("metadata_acceptance_score"), default=0.0)))


def _safe_scene_id(scene_id: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in str(scene_id))[:120]


def _safe_name(name: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_." else "_" for c in (name or "example_bank"))
    return out[:120] or "example_bank"
