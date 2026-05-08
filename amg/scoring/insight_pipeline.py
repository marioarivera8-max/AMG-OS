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

    insight_obj = describe_scene_from_covers(
        contact_sheet_path=contact_sheet,
        cover_paths=cover_paths,
        ai_client=ai_client,
    )
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
    )
    target_platforms = list(PLATFORM_REQUIREMENTS.keys())
    metadata_initial = _validate_generated_metadata(
        title_payload=title_payload,
        target_platforms=target_platforms,
    )
    repair_attempts = 0
    metadata_final = metadata_initial
    if metadata_initial.get("blockers"):
        repaired = _deterministic_metadata_repair(
            title_payload=title_payload,
            genres=title_info.get("detected_genres", []),
            title_text=_primary_title_text(title_payload),
            target_platforms=target_platforms,
        )
        if repaired:
            repair_attempts += 1
            title_payload.update(repaired)
            metadata_final = _validate_generated_metadata(
                title_payload=title_payload,
                target_platforms=target_platforms,
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
        "insight": insight_obj.to_dict() if insight_obj else None,
        "position_summary": position_summary,
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


def _resolve_performers(folder_ctx, title_info: Dict[str, Any]) -> List[str]:
    performers = list(folder_ctx.performers or [])
    if performers:
        return performers
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
            t = " ".join(part.capitalize() for part in t.split())
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _validate_generated_metadata(title_payload: Dict[str, Any], target_platforms: List[str]) -> Dict[str, Any]:
    return validate_metadata_for_platforms(
        title_text=_primary_title_text(title_payload),
        long_description=str(title_payload.get("long_description") or "").strip(),
        tags=_normalize_text_tokens(title_payload.get("tags", [])),
        categories=_normalize_text_tokens(title_payload.get("categories", []), title_case=True),
        target_platforms=target_platforms,
    )


def _deterministic_metadata_repair(
    *,
    title_payload: Dict[str, Any],
    genres: List[str],
    title_text: str,
    target_platforms: List[str],
) -> Dict[str, Any]:
    if not target_platforms:
        return {}
    rules = [PLATFORM_REQUIREMENTS.get(p, {}) for p in target_platforms]
    md = [r.get("metadata", {}) if isinstance(r.get("metadata"), dict) else {} for r in rules]
    min_desc = max(int(x.get("description_min_chars", 0)) for x in md) if md else 0
    min_tags = max(int(x.get("min_tags", 0)) for x in md) if md else 0
    min_categories = max(int(x.get("min_categories", 0)) for x in md) if md else 0

    updated: Dict[str, Any] = {}
    desc = str(title_payload.get("long_description") or "").strip()
    if len(desc) < min_desc:
        genre_hint = ", ".join(str(g).lower().replace("_", " ") for g in (genres or [])[:3]) or "explicit action"
        base = desc or f"{title_text or 'This scene'} delivers a focused sequence with consistent pacing."
        while len(base) < min_desc:
            base += f" The scene maintains clear visual progression and searchable cues around {genre_hint}."
        updated["long_description"] = base[:520].strip()

    tags = _normalize_text_tokens(title_payload.get("tags", []))
    seed_tags = [str(g).lower().replace("_", " ") for g in (genres or []) if str(g).strip()]
    fallback_tags = [
        "pov",
        "eye contact",
        "blowjob",
        "deepthroat",
        "doggy style",
        "missionary",
        "cum in mouth",
        "creampie",
        "squirting",
        "spit",
        "rough sex",
        "hard sex",
        "big ass",
        "big tits",
        "natural tits",
        "shaved pussy",
    ]
    for t in seed_tags + fallback_tags:
        if len(tags) >= min_tags:
            break
        norm = " ".join(t.split()).lower()
        if norm and norm not in {x.lower() for x in tags}:
            tags.append(norm)
    if len(tags) >= min_tags:
        updated["tags"] = tags

    categories = _normalize_text_tokens(title_payload.get("categories", []), title_case=True)
    seed_cats = [" ".join(str(g).split("_")).title() for g in (genres or []) if str(g).strip()]
    fallback_cats = ["HD Porn", "POV", "Blowjob", "Deepthroat", "Anal", "Cumshot", "Toys", "Big Ass"]
    for c in seed_cats + fallback_cats:
        if len(categories) >= min_categories:
            break
        norm = " ".join(c.split())
        if norm and norm.lower() not in {x.lower() for x in categories}:
            categories.append(norm)
    if len(categories) >= min_categories:
        updated["categories"] = categories

    return updated


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
