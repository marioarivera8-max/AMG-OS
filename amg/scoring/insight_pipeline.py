"""
Shared scene-insight + title generation pipeline.

This module centralizes the logic used by the core pipeline, UI regenerate,
and CLI regenerate so all paths produce the same `insight.json` shape and
prompt inputs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from amg.config import TITLE_TONE_DEFAULT
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
