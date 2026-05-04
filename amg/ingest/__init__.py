"""Ingest: reading scenes, parsing metadata, identifying studios."""
from amg.ingest.folder_context import (
    FolderContext,
    resolve_folder_context,
    is_generic_filename,
)
from amg.ingest.performer_code import (
    parse_performer_code,
    parse_performer_code_with_context,
    get_authoritative_performer_count,
)
from amg.ingest.studio_profiles import (
    detect_studio,
    load_studio_profile,
    save_studio_profile,
    get_or_create_profile,
)
from amg.ingest.title_parser import parse_title, parse_title_with_context, detect_genres
from amg.ingest.inventory import discover_scenes

__all__ = [
    "FolderContext",
    "resolve_folder_context",
    "is_generic_filename",
    "parse_performer_code",
    "parse_performer_code_with_context",
    "get_authoritative_performer_count",
    "detect_studio",
    "load_studio_profile",
    "save_studio_profile",
    "get_or_create_profile",
    "parse_title",
    "parse_title_with_context",
    "detect_genres",
    "discover_scenes",
]
