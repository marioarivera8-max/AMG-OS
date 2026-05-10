"""Metadata fact-sheet builder for AMG-native text generation.

The scene-analysis sidecar is intentionally broad: it preserves scan and
evidence details for review, debugging, previews, and future learning. This
module distills that sidecar into a compact, NOXO-style fact sheet for retail
metadata generation: weighted tags, category candidates, action beats, OCR,
policy warnings, and a prompt-ready brief.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from amg.scoring.market_profile import (
    CATEGORY_ALIASES,
    MARKET_CATEGORY_PRIORITIES,
    MARKET_TAG_PRIORITIES,
    TAG_ALIASES,
)
from amg.utils.logging import get_logger

log = get_logger("analysis.metadata_fact_sheet")

SCHEMA_VERSION = "1.0"
SIDE_CAR_NAME = "metadata_fact_sheet.json"


_CATEGORY_BY_LABEL = {
    "POV": "POV",
    "ORAL_BJ": "Blowjob",
    "BLOWJOB": "Blowjob",
    "BLOWJOB_KNEELING": "Blowjob",
    "DEEPTHROAT": "Deepthroat",
    "ANAL": "Anal",
    "CREAMPIE": "Cumshot",
    "FACIAL": "Cumshot",
    "CUMSHOT": "Cumshot",
    "SQUIRT": "Squirt",
    "SQUIRTING": "Squirt",
    "TOYS": "Toys",
    "TOY": "Toys",
    "SOLO_FEMALE": "Solo Female",
    "MILF": "MILF",
    "GROUP": "Group Sex",
    "THREESOME": "Threesome",
    "FOURSOME": "Group Sex",
    "ORGY": "Group Sex",
    "GANGBANG": "Gangbang",
    "BDSM": "BDSM",
    "FETISH": "Fetish",
    "KINK": "Fetish",
    "AMATEUR": "Amateur",
    "VERIFIED_MODELS": "Verified Models",
    "VERIFIED_AMATEURS": "Verified Amateurs",
    "STEP_FAMILY": "Step Fantasy",
}


_TAG_BY_LABEL = {
    "POV": "pov",
    "ORAL_BJ": "blowjob",
    "BLOWJOB": "blowjob",
    "BLOWJOB_KNEELING": "blowjob",
    "DEEPTHROAT": "deepthroat",
    "ANAL": "anal",
    "DOGGY": "doggy style",
    "DOGGY_STYLE": "doggy style",
    "MISSIONARY": "missionary",
    "COWGIRL": "cowgirl",
    "REVERSE_COWGIRL": "reverse cowgirl",
    "CREAMPIE": "creampie",
    "FACIAL": "facial",
    "CUMSHOT": "cumshot",
    "SQUIRT": "squirting",
    "SQUIRTING": "squirting",
    "TOYS": "toys",
    "TOY": "toys",
    "SOLO_FEMALE": "solo female",
    "MILF": "milf",
    "GROUP": "group sex",
    "THREESOME": "threesome",
    "FOURSOME": "foursome",
    "ORGY": "orgy",
    "GANGBANG": "gangbang",
    "BDSM": "bdsm",
    "FETISH": "fetish",
    "KINK": "kink",
    "AMATEUR": "amateur",
    "STEP_FAMILY": "step fantasy",
}


def build_metadata_fact_sheet(
    *,
    analysis: Optional[dict],
    scene_context: Optional[dict] = None,
    saved_covers: Optional[List[dict]] = None,
    insight: Optional[dict] = None,
) -> Dict[str, Any]:
    """Build a compact fact sheet for metadata generation.

    The result is deterministic and safe to persist. It does not call any AI
    service; it only summarizes existing AMG evidence.
    """
    analysis = analysis if isinstance(analysis, dict) else {}
    scene_context = scene_context if isinstance(scene_context, dict) else {}
    saved_covers = list(saved_covers or [])
    insight = insight if isinstance(insight, dict) else {}

    signals: Dict[str, Dict[str, Any]] = {}
    for genre in scene_context.get("genres") or []:
        _add_signal(signals, genre, 0.78, "input_genre")

    for row in analysis.get("tags") or []:
        if not isinstance(row, dict):
            continue
        label = row.get("tag")
        probability_raw = row.get("probability")
        if probability_raw is None:
            probability_raw = row.get("confidence")
        probability = _clamp_float(probability_raw, default=0.0)
        count = int(row.get("count") or 1)
        _add_signal(
            signals,
            label,
            max(0.25, probability) * min(1.0, 0.65 + count * 0.05),
            "analysis_tag",
            count=count,
        )

    for section in analysis.get("sections") or []:
        if not isinstance(section, dict):
            continue
        _add_signal(
            signals,
            section.get("section_tag"),
            _clamp_float(section.get("confidence"), default=0.5),
            "semantic_section",
        )

    for cover in saved_covers:
        if not isinstance(cover, dict):
            continue
        base = max(0.0, min(1.0, _safe_float(cover.get("score"), default=70.0) / 100.0))
        _add_signal(signals, cover.get("type"), base, "saved_cover_type")
        _add_signal(
            signals,
            cover.get("position_label"),
            max(base, _clamp_float(cover.get("position_label_confidence"), default=0.0)),
            "saved_cover_position",
        )
        for label in list(cover.get("genre_tags") or []) + list(cover.get("subgenre_tags") or []):
            _add_signal(signals, label, max(base, 0.55), "saved_cover_taxonomy")

    sections = _compact_sections(analysis.get("sections") or [])
    thumbnail_moments = _compact_thumbnail_moments(analysis.get("thumbnail_moments") or [])
    policy_warnings = _compact_policy_flags(analysis.get("policy_flags") or [])
    ocr_text = _compact_ocr(analysis.get("ocr_results") or [])
    weighted_tags = _rank_signals(signals)
    category_candidates = _category_candidates(weighted_tags)
    tag_candidates = _tag_candidates(weighted_tags, insight=insight)
    action_beats = _action_beats(sections, weighted_tags)

    fact_sheet: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "scene_id": scene_context.get("scene_id") or analysis.get("scene_id"),
            "studio": scene_context.get("studio"),
            "performers": list(scene_context.get("performers") or []),
            "scene_type": scene_context.get("scene_type") or "STANDARD",
            "genres": list(scene_context.get("genres") or []),
            "operator_description": scene_context.get("description") or "",
            "duration_sec": (analysis.get("source") or {}).get("duration_sec"),
            "resolution": (analysis.get("source") or {}).get("resolution"),
        },
        "sections": sections,
        "action_beats": action_beats,
        "weighted_tags": weighted_tags,
        "category_candidates": category_candidates,
        "tag_candidates": tag_candidates,
        "thumbnail_moments": thumbnail_moments,
        "ocr_text": ocr_text,
        "policy_warnings": policy_warnings,
        "evidence_count": len(analysis.get("evidence") or []),
    }
    fact_sheet["prompt_brief"] = compact_fact_sheet_prompt_context(fact_sheet)
    return fact_sheet


def write_metadata_fact_sheet(work_dir: Path, fact_sheet: Dict[str, Any]) -> Optional[Path]:
    """Persist a metadata fact sheet next to scene-analysis outputs."""
    try:
        out = Path(work_dir) / SIDE_CAR_NAME
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            json.dump(fact_sheet, f, indent=2, default=str)
        return out
    except Exception as exc:  # noqa: BLE001 - sidecar failure is non-fatal
        log.warn("Failed to write metadata fact sheet", error=str(exc), path=str(Path(work_dir) / SIDE_CAR_NAME))
        return None


def load_metadata_fact_sheet(work_dir: Optional[Path]) -> Optional[dict]:
    if not work_dir:
        return None
    path = Path(work_dir) / SIDE_CAR_NAME
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def compact_fact_sheet_prompt_context(fact_sheet: Optional[dict], *, max_chars: int = 1400) -> str:
    """Render a small, factual prompt block for metadata generation."""
    fact_sheet = fact_sheet if isinstance(fact_sheet, dict) else {}
    source = fact_sheet.get("source") if isinstance(fact_sheet.get("source"), dict) else {}

    parts: List[str] = []
    source_bits = []
    for key in ("studio", "scene_type", "operator_description"):
        val = source.get(key)
        if val:
            source_bits.append(f"{key}={val}")
    performers = source.get("performers") if isinstance(source.get("performers"), list) else []
    if performers:
        source_bits.append("performers=" + ", ".join(str(x) for x in performers[:4]))
    genres = source.get("genres") if isinstance(source.get("genres"), list) else []
    if genres:
        source_bits.append("genres=" + ", ".join(str(x) for x in genres[:8]))
    if source_bits:
        parts.append("scene facts: " + "; ".join(source_bits))

    sections = [
        f"{s.get('section_tag')}({s.get('confidence')})"
        for s in (fact_sheet.get("sections") or [])[:8]
        if isinstance(s, dict) and s.get("section_tag")
    ]
    if sections:
        parts.append("semantic sections: " + ", ".join(sections))

    beats = [
        str(b.get("label") or "")
        for b in (fact_sheet.get("action_beats") or [])[:8]
        if isinstance(b, dict) and b.get("label")
    ]
    if beats:
        parts.append("action beats: " + ", ".join(beats))

    cats = [
        str(c.get("category") or "")
        for c in (fact_sheet.get("category_candidates") or [])[:12]
        if isinstance(c, dict) and c.get("category")
    ]
    if cats:
        parts.append("category candidates: " + ", ".join(cats))

    tags = [
        str(t.get("tag") or "")
        for t in (fact_sheet.get("tag_candidates") or [])[:24]
        if isinstance(t, dict) and t.get("tag")
    ]
    if tags:
        parts.append("tag candidates: " + ", ".join(tags))

    ocr = [str(x.get("text") or "") for x in (fact_sheet.get("ocr_text") or [])[:3] if isinstance(x, dict)]
    if ocr:
        parts.append("visible text/OCR: " + " | ".join(ocr))

    warnings = [
        str(w.get("flag") or "")
        for w in (fact_sheet.get("policy_warnings") or [])[:6]
        if isinstance(w, dict) and w.get("flag")
    ]
    if warnings:
        parts.append("review warnings: " + ", ".join(warnings))

    return "; ".join(parts)[:max_chars].rstrip()


def _add_signal(
    signals: Dict[str, Dict[str, Any]],
    raw_label: Any,
    confidence: float,
    source: str,
    *,
    count: int = 1,
) -> None:
    label = _clean_label(raw_label)
    if not label or label in {"NONE", "OTHER", "UNKNOWN"}:
        return
    row = signals.setdefault(
        label,
        {"label": label, "confidence": 0.0, "count": 0, "sources": []},
    )
    row["confidence"] = max(float(row.get("confidence") or 0.0), _clamp_float(confidence, default=0.0))
    row["count"] = int(row.get("count") or 0) + max(1, int(count or 1))
    if source and source not in row["sources"]:
        row["sources"].append(source)


def _rank_signals(signals: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = list(signals.values())
    for row in rows:
        row["score"] = round(
            _clamp_float(row.get("confidence"), default=0.0) + min(0.25, int(row.get("count") or 0) * 0.025),
            4,
        )
    rows.sort(key=lambda x: (float(x.get("score") or 0.0), int(x.get("count") or 0), str(x.get("label"))), reverse=True)
    return rows[:80]


def _category_candidates(weighted_tags: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    _add_candidate(rows, "HD Porn", 0.35, "base")
    for signal in weighted_tags:
        label = _clean_label(signal.get("label"))
        cat = _CATEGORY_BY_LABEL.get(label)
        if not cat:
            continue
        _add_candidate(rows, _normalize_category(cat), float(signal.get("score") or 0.0), label)
    ranked = list(rows.values())
    prio = {c.lower(): i for i, c in enumerate(MARKET_CATEGORY_PRIORITIES)}
    ranked.sort(key=lambda x: (-float(x.get("confidence") or 0.0), prio.get(str(x.get("category")).lower(), 999), str(x.get("category"))))
    return ranked[:15]


def _tag_candidates(weighted_tags: List[Dict[str, Any]], *, insight: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for signal in weighted_tags:
        label = _clean_label(signal.get("label"))
        tag = _TAG_BY_LABEL.get(label) or _label_to_tag(label)
        _add_candidate(rows, _normalize_tag(tag), float(signal.get("score") or 0.0), label, key_name="tag")
    for key in ("setting", "mood", "location_hint"):
        raw = str(insight.get(key) or "").strip()
        if raw and raw.lower() != "none":
            _add_candidate(rows, _normalize_tag(raw), 0.35, f"insight_{key}", key_name="tag")
    for feature in insight.get("notable_features") or []:
        _add_candidate(rows, _normalize_tag(feature), 0.35, "insight_feature", key_name="tag")
    ranked = list(rows.values())
    prio = {t.lower(): i for i, t in enumerate(MARKET_TAG_PRIORITIES)}
    ranked.sort(key=lambda x: (-float(x.get("confidence") or 0.0), prio.get(str(x.get("tag")).lower(), 999), str(x.get("tag"))))
    return ranked[:30]


def _add_candidate(
    rows: Dict[str, Dict[str, Any]],
    value: str,
    confidence: float,
    source: str,
    *,
    key_name: str = "category",
) -> None:
    clean = value.strip()
    if not clean:
        return
    key = clean.lower()
    row = rows.setdefault(key, {key_name: clean, "confidence": 0.0, "sources": []})
    row["confidence"] = round(max(float(row.get("confidence") or 0.0), _clamp_float(confidence, default=0.0)), 4)
    if source and source not in row["sources"]:
        row["sources"].append(source)


def _compact_sections(sections: Iterable[dict]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        label = _clean_label(sec.get("section_tag"))
        if not label:
            continue
        out.append(
            {
                "section_tag": label,
                "time_segments": sec.get("time_segments") or [],
                "thumbnail_timestamp": sec.get("thumbnail_timestamp"),
                "confidence": round(_clamp_float(sec.get("confidence"), default=0.0), 3),
                "relative_activity": sec.get("relative_activity"),
                "is_critical": bool(sec.get("is_critical")),
                "evidence_ids": list(sec.get("evidence_ids") or []),
            }
        )
    out.sort(key=lambda x: (float(x.get("confidence") or 0.0), float(x.get("relative_activity") or 0.0)), reverse=True)
    return out[:24]


def _compact_thumbnail_moments(rows: Iterable[dict]) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "timestamp_sec": row.get("timestamp_sec"),
                "score": row.get("score"),
                "reason": row.get("reason"),
                "cover_rank": row.get("cover_rank"),
                "evidence_id": row.get("evidence_id"),
            }
        )
    return out[:12]


def _compact_policy_flags(rows: Iterable[dict]) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        flag = _clean_label(row.get("flag") or row.get("type"))
        if not flag:
            continue
        out.append(
            {
                "flag": flag,
                "type": row.get("type"),
                "timestamp_sec": row.get("timestamp_sec"),
                "confidence": row.get("confidence"),
                "review_only": bool(row.get("review_only", True)),
                "evidence_id": row.get("evidence_id"),
            }
        )
    return out[:24]


def _compact_ocr(rows: Iterable[dict]) -> List[Dict[str, Any]]:
    out = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = " ".join(str(row.get("full_text") or "").split())
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "text": text[:240],
                "language_code": row.get("language_code"),
                "time_segment": row.get("time_segment"),
                "confidence": row.get("confidence"),
            }
        )
    return out[:8]


def _action_beats(sections: List[Dict[str, Any]], weighted_tags: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    tag_score = {str(t.get("label")): float(t.get("score") or 0.0) for t in weighted_tags}
    out = []
    for sec in sections[:12]:
        label = str(sec.get("section_tag") or "")
        if not label:
            continue
        out.append(
            {
                "label": label,
                "retail_label": _label_to_tag(label),
                "confidence": round(min(1.0, max(float(sec.get("confidence") or 0.0), tag_score.get(label, 0.0))), 4),
                "thumbnail_timestamp": sec.get("thumbnail_timestamp"),
                "evidence_ids": list(sec.get("evidence_ids") or []),
            }
        )
    return out


def _normalize_category(raw: str) -> str:
    s = " ".join(str(raw or "").replace("_", " ").split())
    alias = CATEGORY_ALIASES.get(s.lower())
    if alias:
        s = alias
    if s.lower() == "pov":
        return "POV"
    return " ".join(part.capitalize() if part.lower() not in {"hd", "pov"} else part.upper() for part in s.split())


def _normalize_tag(raw: Any) -> str:
    s = " ".join(str(raw or "").replace("_", " ").replace("-", " ").lower().split())
    s = re.sub(r"[^\w\s]", "", s).strip()
    s = TAG_ALIASES.get(s, s)
    return s[:32].strip()


def _label_to_tag(label: Any) -> str:
    clean = _clean_label(label)
    if not clean:
        return ""
    return _normalize_tag(clean)


def _clean_label(raw: Any) -> str:
    label = str(raw or "").strip().upper().replace("-", "_").replace(" ", "_")
    label = re.sub(r"[^A-Z0-9_]+", "", label)
    return re.sub(r"_+", "_", label).strip("_")


def _clamp_float(raw: Any, *, default: float) -> float:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        val = default
    return max(0.0, min(1.0, val))


def _safe_float(raw: Any, *, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)
