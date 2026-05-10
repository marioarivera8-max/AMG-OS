"""
Shared scene-insight + title generation pipeline.

This module centralizes the logic used by the core pipeline, UI regenerate,
and CLI regenerate so all paths produce the same `insight.json` shape and
prompt inputs.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from amg.config import TITLE_TONE_DEFAULT, PLATFORM_REQUIREMENTS
from amg.ingest.folder_context import resolve_folder_context
from amg.ingest.performer_code import (
    detect_scene_type_from_code,
    parse_performer_code_with_context,
)
from amg.ingest.studio_profiles import detect_studio, get_or_create_profile
from amg.ingest.title_parser import derive_primary_scene_type, parse_title_with_context
from amg.scoring.ai_client import AIClient
from amg.scoring.scene_describer import (
    describe_scene_from_covers,
    generate_titles_with_insight,
    summarize_positions,
)
from amg.learning.rule_packs import resolve_rule_pack_for_scene
from amg.review.distribution_gate import validate_metadata_for_platforms
from amg.analysis.scene_analysis import build_analysis_summary, compact_prompt_context, load_scene_analysis
from amg.analysis.metadata_fact_sheet import (
    build_metadata_fact_sheet,
    compact_fact_sheet_prompt_context,
    write_metadata_fact_sheet,
)
from amg.utils.logging import get_logger

log = get_logger("scoring.insight_pipeline")


def generate_scene_insight_payload(
    *,
    video_path: Path,
    saved_covers: List[dict],
    work_dir: Optional[Path],
    title_tone: str = TITLE_TONE_DEFAULT,
    ai_client: Optional[AIClient] = None,
    persist: bool = True,
    include_scene_insight: bool = True,
) -> Dict[str, Any]:
    """
    Build scene insight + title payload, optionally writing insight.json.
    """
    video_path = Path(video_path)
    folder_ctx = resolve_folder_context(video_path)
    studio_name = folder_ctx.studio or detect_studio(video_path)
    studio_profile = get_or_create_profile(studio_name) if studio_name else None

    code_info = parse_performer_code_with_context(video_path, folder_ctx)
    title_info = parse_title_with_context(video_path, folder_ctx)
    title_info["folder_performers"] = folder_ctx.performers
    title_info["studio_profile"] = studio_profile or {}

    primary_type = derive_primary_scene_type(
        title_info.get("detected_genres", []),
        code_info.get("total") if code_info else None,
    )
    if code_info:
        from_code = detect_scene_type_from_code(code_info)
        if from_code != "STANDARD":
            primary_type = from_code

    performers = _resolve_performers(folder_ctx, title_info)
    description_for_prompt = (
        title_info.get("description") or folder_ctx.title or title_info.get("metadata_title") or ""
    )
    contact_sheet = _resolve_contact_sheet(work_dir)
    cover_paths = [Path(c["path"]) for c in (saved_covers or []) if c.get("path")]
    rule_resolution = resolve_rule_pack_for_scene(video_path.parent.name or video_path.stem)
    active_rule_pack = rule_resolution.get("rule_pack") if isinstance(rule_resolution, dict) else None
    analysis = load_scene_analysis(work_dir)

    insight_obj = None
    if include_scene_insight:
        insight_obj = describe_scene_from_covers(
            contact_sheet_path=contact_sheet,
            cover_paths=cover_paths,
            ai_client=ai_client,
        )
    scene_context = {
        "scene_id": video_path.parent.name or video_path.stem,
        "studio": studio_name,
        "performers": performers,
        "scene_type": primary_type,
        "genres": title_info.get("detected_genres", []),
        "description": description_for_prompt,
    }
    metadata_fact_sheet = build_metadata_fact_sheet(
        analysis=analysis,
        scene_context=scene_context,
        saved_covers=saved_covers or [],
        insight=insight_obj.to_dict() if insight_obj else {},
    )
    metadata_fact_sheet_path = None
    if persist and work_dir:
        metadata_fact_sheet_path = write_metadata_fact_sheet(Path(work_dir), metadata_fact_sheet)
    fact_context = compact_fact_sheet_prompt_context(metadata_fact_sheet)
    analysis_context = compact_prompt_context(analysis)
    if fact_context:
        analysis_context = f"{fact_context}; {analysis_context}" if analysis_context else fact_context
    position_summary = summarize_positions(saved_covers or [])
    title_payload = generate_titles_with_insight(
        studio=studio_name,
        performers=performers,
        scene_type=primary_type,
        genres=title_info.get("detected_genres", []),
        description=description_for_prompt,
        insight=insight_obj,
        position_summary=position_summary,
        title_tone=title_tone,
        ai_client=ai_client,
        rule_pack=active_rule_pack,
        analysis_context=analysis_context,
        metadata_fact_sheet=metadata_fact_sheet,
    )
    target_platforms = list(PLATFORM_REQUIREMENTS.keys())
    metadata_initial = _validate_generated_metadata(
        title_payload=title_payload,
        target_platforms=target_platforms,
        performers=performers,
        source_resolution=_source_resolution(metadata_fact_sheet),
    )
    repair_attempts = 0
    metadata_final = metadata_initial
    if metadata_initial.get("blockers"):
        repaired = _deterministic_metadata_repair(
            title_payload=title_payload,
            genres=title_info.get("detected_genres", []),
            title_text=_primary_title_text(title_payload),
            target_platforms=target_platforms,
            performers=performers,
            studio=studio_name,
            scene_type=primary_type,
            metadata_fact_sheet=metadata_fact_sheet,
        )
        if repaired:
            repair_attempts += 1
            title_payload.update(repaired)
            metadata_final = _validate_generated_metadata(
                title_payload=title_payload,
                target_platforms=target_platforms,
                performers=performers,
                source_resolution=_source_resolution(metadata_fact_sheet),
            )

        if metadata_final.get("blockers") and ai_client and ai_client.is_alive():
            rewritten = _ai_metadata_repair(
                ai_client=ai_client,
                title_text=_primary_title_text(title_payload),
                long_description=title_payload.get("long_description", ""),
                tags=title_payload.get("tags", []),
                categories=title_payload.get("categories", []),
                blockers=metadata_final.get("blockers", []),
            )
            if rewritten:
                repair_attempts += 1
                title_payload.update(rewritten)
                metadata_final = _validate_generated_metadata(
                    title_payload=title_payload,
                    target_platforms=target_platforms,
                    performers=performers,
                    source_resolution=_source_resolution(metadata_fact_sheet),
                )

    payload: Dict[str, Any] = {
        "studio": studio_name,
        "performers": performers,
        "scene_type": primary_type,
        "genres": title_info.get("detected_genres", []),
        "operator_description": description_for_prompt,
        "folder_context": {
            "is_generic_filename": folder_ctx.is_generic_filename,
            "source_folder": str(folder_ctx.source_folder) if folder_ctx.source_folder else None,
            "metadata_documents_found": len(folder_ctx.metadata_documents),
            "ancestor_names": folder_ctx.ancestor_names,
        },
        "scene_insight_enabled": bool(include_scene_insight),
        "insight": insight_obj.to_dict() if insight_obj else None,
        "position_summary": position_summary,
        "analysis_context": analysis_context,
        "analysis_summary": build_analysis_summary(analysis) if isinstance(analysis, dict) else {},
        "metadata_fact_sheet_path": str(metadata_fact_sheet_path) if metadata_fact_sheet_path else None,
        "metadata_fact_sheet_summary": _metadata_fact_sheet_summary(metadata_fact_sheet),
        "ai_titles": title_payload.get("titles", []),
        "long_description": title_payload.get("long_description", ""),
        "title_tone": title_payload.get("title_tone", title_tone),
        "ai_categories": title_payload.get("categories", []),
        "ai_tags": title_payload.get("tags", []),
        "ai_used": title_payload.get("ai_used", False),
        "text_model_primary": getattr(ai_client, "text_model", None) if ai_client else None,
        "vision_model_used": getattr(ai_client, "vision_model", None) if ai_client else None,
        "text_model_used": title_payload.get("text_model_effective") or (getattr(ai_client, "text_model", None) if ai_client else None),
        "text_model_fallback_used": bool(title_payload.get("text_model_fallback_used", False)),
        "text_model_fallback_model": title_payload.get("text_model_fallback_model"),
        "text_generation_status": title_payload.get("text_generation_status"),
        "text_generation_error_code": title_payload.get("text_generation_error_code"),
        "text_generation_error_message": title_payload.get("text_generation_error_message"),
        "retrieval_stage": title_payload.get("retrieval_stage"),
        "retrieval_scope": title_payload.get("retrieval_scope"),
        "retrieved_examples_count": int(title_payload.get("retrieved_examples_count", 0) or 0),
        "rule_pack_id": rule_resolution.get("rule_pack_id") if isinstance(rule_resolution, dict) else None,
        "rule_pack_applied": bool(rule_resolution.get("applied")) if isinstance(rule_resolution, dict) else False,
        "rule_pack_reason": rule_resolution.get("reason") if isinstance(rule_resolution, dict) else "unknown",
        "rule_pack_mode": rule_resolution.get("mode") if isinstance(rule_resolution, dict) else "off",
        "rule_pack_canary_pct": float(rule_resolution.get("canary_pct") or 0.0)
        if isinstance(rule_resolution, dict)
        else 0.0,
        "rule_pack_canary_bucket": rule_resolution.get("canary_bucket")
        if isinstance(rule_resolution, dict)
        else None,
        "metadata_validation_passed": not bool(metadata_final.get("blockers")),
        "metadata_blockers_initial": metadata_initial.get("blockers", []),
        "metadata_blockers_final": metadata_final.get("blockers", []),
        "metadata_warnings_final": metadata_final.get("warnings", []),
        "repair_attempts": repair_attempts,
    }

    if persist and work_dir:
        _write_insight_json(Path(work_dir), payload)
    return payload


def _metadata_fact_sheet_summary(fact_sheet: Dict[str, Any]) -> Dict[str, Any]:
    fact_sheet = fact_sheet if isinstance(fact_sheet, dict) else {}
    return {
        "schema_version": fact_sheet.get("schema_version"),
        "sections_count": len(fact_sheet.get("sections") or []),
        "action_beats": [
            str(x.get("label"))
            for x in (fact_sheet.get("action_beats") or [])[:8]
            if isinstance(x, dict) and x.get("label")
        ],
        "category_candidates": [
            str(x.get("category"))
            for x in (fact_sheet.get("category_candidates") or [])[:12]
            if isinstance(x, dict) and x.get("category")
        ],
        "tag_candidates": [
            str(x.get("tag"))
            for x in (fact_sheet.get("tag_candidates") or [])[:20]
            if isinstance(x, dict) and x.get("tag")
        ],
        "policy_warning_count": len(fact_sheet.get("policy_warnings") or []),
        "ocr_text_count": len(fact_sheet.get("ocr_text") or []),
    }


def _resolve_performers(folder_ctx, title_info: Dict[str, Any]) -> List[str]:
    performers = list(folder_ctx.performers or [])
    if performers:
        return performers
    inferred = list(title_info.get("inferred_performers") or [])
    if inferred:
        return inferred
    regulars = (
        (title_info.get("folder_performers") or [])
        or (((title_info.get("studio_profile") or {}).get("performers") or {}).get("regular", []))
    )
    return list(regulars or [])


def _resolve_contact_sheet(work_dir: Optional[Path]) -> Optional[Path]:
    if not work_dir:
        return None
    sheets = sorted(Path(work_dir).glob("00_*_contact_sheet.jpg"))
    return sheets[0] if sheets else None


def _write_insight_json(work_dir: Path, payload: Dict[str, Any]) -> None:
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        out = work_dir / "insight.json"
        with open(out, "w") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        log.warn("Failed to write insight.json", error=str(e), path=str(work_dir / "insight.json"))


def _primary_title_text(title_payload: Dict[str, Any]) -> str:
    titles = title_payload.get("titles") or []
    if not isinstance(titles, list) or not titles:
        return ""
    first = titles[0] if isinstance(titles[0], dict) else {}
    return str(first.get("text") or "").strip()


def _normalize_text_tokens(values: Any, *, title_case: bool = False) -> List[str]:
    raw = values if isinstance(values, list) else []
    out: List[str] = []
    seen = set()
    for token in raw:
        t = " ".join(str(token or "").strip().split())
        if not t:
            continue
        if title_case:
            t = _platform_title_case(t)
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _platform_title_case(token: str) -> str:
    out = []
    for part in str(token or "").split():
        lower = part.lower()
        if lower in {"hd", "uhd", "pov", "vr", "4k"}:
            out.append(lower.upper())
        else:
            out.append(part.capitalize())
    return " ".join(out)


def _source_resolution(metadata_fact_sheet: Optional[Dict[str, Any]]) -> Optional[str]:
    source = (metadata_fact_sheet or {}).get("source") if isinstance(metadata_fact_sheet, dict) else {}
    resolution = source.get("resolution") if isinstance(source, dict) else None
    resolution = str(resolution or "").strip()
    return resolution or None


def _validate_generated_metadata(
    title_payload: Dict[str, Any],
    target_platforms: List[str],
    *,
    performers: Optional[List[str]] = None,
    source_resolution: Optional[str] = None,
) -> Dict[str, Any]:
    decision_log = {"input": {"resolution": source_resolution}} if source_resolution else None
    return validate_metadata_for_platforms(
        title_text=_primary_title_text(title_payload),
        long_description=str(title_payload.get("long_description") or "").strip(),
        tags=_normalize_text_tokens(title_payload.get("tags", [])),
        categories=_normalize_text_tokens(title_payload.get("categories", []), title_case=True),
        target_platforms=target_platforms,
        performers=performers or [],
        decision_log=decision_log,
        metadata_only=True,
    )


def _deterministic_metadata_repair(
    *,
    title_payload: Dict[str, Any],
    genres: List[str],
    title_text: str,
    target_platforms: List[str],
    performers: List[str],
    studio: Optional[str],
    scene_type: str,
    metadata_fact_sheet: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not target_platforms:
        return {}
    rules = [PLATFORM_REQUIREMENTS.get(p, {}) for p in target_platforms]
    md = [r.get("metadata", {}) if isinstance(r.get("metadata"), dict) else {} for r in rules]
    min_desc = max(int(x.get("description_min_chars", 0)) for x in md) if md else 0
    min_tags = max(int(x.get("min_tags", 0)) for x in md) if md else 0
    min_categories = max(int(x.get("min_categories", 0)) for x in md) if md else 0
    min_title = max(int(x.get("title_min_chars", 0)) for x in md) if md else 0
    max_title = min(
        int(r.get("title_max_chars", 9999))
        for r in rules
        if isinstance(r.get("title_max_chars", 9999), int)
    ) if rules else 9999

    updated: Dict[str, Any] = {}
    repaired_title = _repair_title(
        title_text=title_text,
        performers=performers,
        studio=studio,
        scene_type=scene_type,
        genres=genres,
        min_chars=min_title,
        max_chars=max_title,
    )
    if repaired_title and repaired_title != title_text:
        updated["titles"] = _replace_primary_title(title_payload.get("titles", []), repaired_title)

    desc = str(title_payload.get("long_description") or "").strip()
    if len(desc) < min_desc:
        genre_hint = ", ".join(str(g).lower().replace("_", " ") for g in (genres or [])[:3]) or "explicit action"
        base = desc or f"{title_text or 'This scene'} delivers a focused sequence with consistent pacing."
        while len(base) < min_desc:
            base += f" The scene maintains clear visual progression and searchable cues around {genre_hint}."
        updated["long_description"] = base[:520].strip()

    tags = _normalize_text_tokens(title_payload.get("tags", []))
    for t in _repair_tag_candidates(genres, metadata_fact_sheet, title_text):
        if len(tags) >= min_tags:
            break
        norm = " ".join(t.split()).lower()
        if norm and norm not in {x.lower() for x in tags}:
            tags.append(norm)
    if len(tags) >= min_tags:
        updated["tags"] = tags

    categories = _normalize_text_tokens(title_payload.get("categories", []), title_case=True)
    for c in _repair_category_candidates(genres, metadata_fact_sheet, title_text):
        if len(categories) >= min_categories:
            break
        norm = _platform_title_case(" ".join(c.split()))
        if norm and norm.lower() not in {x.lower() for x in categories}:
            categories.append(norm)
    if len(categories) >= min_categories:
        updated["categories"] = categories

    return updated


def _replace_primary_title(titles: Any, title_text: str) -> List[Dict[str, Any]]:
    rows = list(titles or []) if isinstance(titles, list) else []
    primary = {"text": title_text, "style": "scene_descriptive"}
    if rows and isinstance(rows[0], dict):
        rows[0] = {**rows[0], **primary}
    else:
        rows.insert(0, primary)
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "")
        row["char_count"] = len(text)
        row["platform_fit"] = {
            platform: len(text) <= int(reqs.get("title_max_chars", 100))
            for platform, reqs in PLATFORM_REQUIREMENTS.items()
            if isinstance(reqs, dict)
        }
        row["warnings"] = [w for w in row.get("warnings", []) if not str(w).lower().startswith("very short")]
    return rows


def _repair_title(
    *,
    title_text: str,
    performers: List[str],
    studio: Optional[str],
    scene_type: str,
    genres: List[str],
    min_chars: int,
    max_chars: int,
) -> str:
    current = str(title_text or "").strip()
    if current and len(current) >= min_chars and len(current) <= max_chars and "unknown" not in current.lower():
        return current
    cast = _format_cast_for_title(performers)
    action = _action_phrase(scene_type, genres)
    candidates: List[str] = []
    if cast:
        candidates.extend([
            f"{cast} in a {action}",
            f"{action} with {cast}",
            f"{cast} Lead a {action}",
        ])
    clean_studio = str(studio or "").strip()
    if clean_studio and clean_studio.lower() != "unknown":
        candidates.append(f"{clean_studio} Presents {action}")
    candidates.append(action)
    for candidate in candidates:
        text = " ".join(candidate.split()).strip()
        if len(text) > max_chars:
            text = text[:max_chars].rstrip(" -,")
        if len(text) >= min_chars and "unknown" not in text.lower():
            return text
    return current


def _format_cast_for_title(performers: List[str]) -> str:
    names = [" ".join(str(p or "").split()) for p in (performers or []) if str(p or "").strip()]
    names = list(dict.fromkeys(names))[:3]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} & {names[1]}"
    return f"{', '.join(names[:-1])} & {names[-1]}"


def _action_phrase(scene_type: str, genres: List[str]) -> str:
    values = {str(x or "").upper() for x in (genres or [])}
    primary = str(scene_type or "").upper()
    if "SQUIRT" in values and ("ORGY" in values or primary in {"GANGBANG", "REVERSE_GANGBANG"}):
        return "Hardcore Squirting Orgy"
    if "ORGY" in values:
        return "Hardcore Orgy"
    if primary in {"GANGBANG", "REVERSE_GANGBANG"} or "GANGBANG" in values:
        return "Hardcore Gangbang"
    if "SQUIRT" in values:
        return "Squirting Scene"
    label = primary.replace("_", " ").title() if primary and primary != "STANDARD" else "Hardcore Scene"
    return label


def _repair_category_candidates(
    genres: List[str],
    metadata_fact_sheet: Optional[Dict[str, Any]],
    title_text: str,
) -> List[str]:
    candidates = _fact_sheet_values(metadata_fact_sheet, "category_candidates", "category")
    text = " ".join([title_text or "", " ".join(genres or [])]).lower()
    contextual = [
        ("Gangbang", "gangbang" in text),
        ("Group Sex", any(x in text for x in ("group", "orgy", "gangbang"))),
        ("Squirt", any(x in text for x in ("squirt", "squirting"))),
        ("Orgy", "orgy" in text),
        ("HD Porn", True),
        ("Hardcore", True),
        ("Explicit Sex", True),
        ("Multiple Performers", any(x in text for x in ("group", "orgy", "gangbang"))),
        ("4K", _fact_sheet_resolution(metadata_fact_sheet).startswith("3840x")),
    ]
    for value, enabled in contextual:
        if enabled:
            candidates.append(value)
    return _dedupe(candidates)


def _repair_tag_candidates(
    genres: List[str],
    metadata_fact_sheet: Optional[Dict[str, Any]],
    title_text: str,
) -> List[str]:
    candidates = _fact_sheet_values(metadata_fact_sheet, "tag_candidates", "tag")
    text = " ".join([title_text or "", " ".join(genres or [])]).lower()
    contextual = [
        ("gangbang", "gangbang" in text),
        ("group sex", any(x in text for x in ("group", "orgy", "gangbang"))),
        ("group action", any(x in text for x in ("group", "orgy", "gangbang"))),
        ("orgy", "orgy" in text),
        ("hardcore orgy", "orgy" in text),
        ("squirting", any(x in text for x in ("squirt", "squirting"))),
        ("multiple performers", any(x in text for x in ("group", "orgy", "gangbang"))),
        ("hardcore", True),
        ("explicit sex", True),
        ("hd", True),
        ("4k", _fact_sheet_resolution(metadata_fact_sheet).startswith("3840x")),
        ("4k video", _fact_sheet_resolution(metadata_fact_sheet).startswith("3840x")),
        ("long scene", _fact_sheet_duration(metadata_fact_sheet) >= 1800),
    ]
    for value, enabled in contextual:
        if enabled:
            candidates.append(value)
    return _dedupe(candidates)


def _fact_sheet_values(metadata_fact_sheet: Optional[Dict[str, Any]], section: str, key: str) -> List[str]:
    rows = (metadata_fact_sheet or {}).get(section) if isinstance(metadata_fact_sheet, dict) else []
    out: List[str] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        sources = {str(x).lower() for x in (row.get("sources") or [])}
        confidence = float(row.get("confidence") or 0.0)
        if sources and sources <= {"market_prior"}:
            continue
        if confidence < 0.25 and "base" not in sources:
            continue
        value = str(row.get(key) or "").strip()
        if value:
            out.append(value)
    return out


def _fact_sheet_resolution(metadata_fact_sheet: Optional[Dict[str, Any]]) -> str:
    source = (metadata_fact_sheet or {}).get("source") if isinstance(metadata_fact_sheet, dict) else {}
    return str(source.get("resolution") or "").strip() if isinstance(source, dict) else ""


def _fact_sheet_duration(metadata_fact_sheet: Optional[Dict[str, Any]]) -> float:
    source = (metadata_fact_sheet or {}).get("source") if isinstance(metadata_fact_sheet, dict) else {}
    if not isinstance(source, dict):
        return 0.0
    try:
        return float(source.get("duration_sec") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _dedupe(values: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = " ".join(str(value or "").split())
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _ai_metadata_repair(
    *,
    ai_client: AIClient,
    title_text: str,
    long_description: str,
    tags: List[str],
    categories: List[str],
    blockers: List[str],
) -> Dict[str, Any]:
    if not blockers:
        return {}
    prompt = (
        "Repair this adult VOD metadata so it passes platform constraints.\n\n"
        f"TITLE: {title_text or '(none)'}\n"
        f"LONG_DESCRIPTION: {long_description or '(none)'}\n"
        f"TAGS: {', '.join(_normalize_text_tokens(tags)) or '(none)'}\n"
        f"CATEGORIES: {', '.join(_normalize_text_tokens(categories, title_case=True)) or '(none)'}\n"
        f"BLOCKERS: {', '.join(blockers)}\n\n"
        "Output format (required):\n"
        "LONG_DESCRIPTION: <2-4 factual sentences>\n"
        "CATEGORY_SUGGESTIONS: <comma-separated categories>\n"
        "TAG_SUGGESTIONS: <comma-separated tags>\n"
        "END"
    )
    response = ai_client.generate_text(prompt)
    if not response.success:
        return {}
    raw = response.raw_text or ""
    desc_m = re.search(r"LONG_DESCRIPTION\s*:\s*(.+?)(?=\n[A-Z_]+\s*:|\nEND\b|\Z)", raw, re.I | re.S)
    cat_m = re.search(r"CATEGORY_SUGGESTIONS\s*:\s*(.+?)(?=\n[A-Z_]+\s*:|\nEND\b|\Z)", raw, re.I | re.S)
    tag_m = re.search(r"TAG_SUGGESTIONS\s*:\s*(.+?)(?=\n[A-Z_]+\s*:|\nEND\b|\Z)", raw, re.I | re.S)
    out: Dict[str, Any] = {}
    if desc_m:
        out["long_description"] = " ".join(desc_m.group(1).strip().split())
    if cat_m:
        out["categories"] = _normalize_text_tokens(
            [x.strip() for x in cat_m.group(1).replace(";", ",").split(",") if x.strip()],
            title_case=True,
        )
    if tag_m:
        out["tags"] = _normalize_text_tokens(
            [x.strip().lower() for x in tag_m.group(1).replace(";", ",").split(",") if x.strip()]
        )
    return out
