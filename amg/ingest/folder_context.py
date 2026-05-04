"""
Folder-context fallback for ingestion.

Content creators sometimes submit a folder where the *folder* carries the
metadata (title, description, performer code, 2257 docs, JSON manifest) and
the individual scene files inside have generic filenames like
``Screen Recording 2025-10-24 at 12.47.05 PM.mov`` or ``2025-07-10 19.59.08.mov``.

This module walks UP from the video file looking for the first ancestor that
actually has parseable info — performer code in the folder name, a metadata
JSON sibling, or a 2257 doc. The result is a unified context object that the
rest of the ingest pipeline (title parser, performer code parser) can consult
when the immediate filename is empty.

Design goals:
- Pure helpers, no side effects, no AI calls.
- Safe defaults: if anything is missing, return empty fields rather than raising.
- Bounded ancestor walk (MAX_ANCESTOR_DEPTH) so we never escape the user's tree.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# We also import this from performer_code at usage time (avoid circular import).
PERFORMER_CODE_PATTERN_RE = re.compile(r'^\s*\d+\s+([BG]{1,12})\s+-\s')

# Filenames considered "generic" (no useful description embedded). Anything
# matching these patterns triggers folder-context fallback.
GENERIC_FILENAME_PATTERNS = [
    r"^screen\s*recording",                       # "Screen Recording 2024-01-01..."
    r"^\d{4}[-_.]\d{2}[-_.]\d{2}[\s_T-]+\d",      # 2025-07-10 19.59.08
    r"^img[_-]?\d+",                              # IMG_4523
    r"^mvi[_-]?\d+",                              # MVI_0001
    r"^vid[_-]?\d{6,}",                           # VID_20250710_195908
    r"^gh\d{6,}",                                 # GoPro GH010001
    r"^dji[_-]?\d+",                              # DJI_0001
    r"^clip[_-]?\d+",                             # clip_1
    r"^untitled",                                 # untitled
    r"^\d{10,}$",                                 # unix timestamp
    r"^output[_-]?\d*",                           # output, output_1
    r"^new[\s_-]+(file|recording|video)",         # new file
    r"^scene\s*\d+$",                             # "scene 1" with no description
    r"^\.",                                       # hidden files
]
GENERIC_FILENAME_RE = re.compile("|".join(GENERIC_FILENAME_PATTERNS), re.IGNORECASE)

# Metadata JSON filenames creators commonly drop next to scenes.
METADATA_FILENAMES = (
    "metadata.json",
    "scene.json",
    "info.json",
    "release.json",
    "release_form.json",
    "details.json",
    "submission.json",
    "manifest.json",
    "scene_info.json",
)

# Filenames that signal "this folder is the submission package".
SUBMISSION_MARKERS = (
    "2257.pdf", "2257.jpg", "2257.png", "id.pdf", "ID.pdf",
    "release.pdf", "model_release.pdf", "release.json",
)

# Ancestor walk depth cap (video → 5 levels up). This is plenty for normal
# layouts and avoids escaping into unrelated directories on shared volumes.
MAX_ANCESTOR_DEPTH = 5

# Field aliases inside metadata JSON files. We accept several common spellings
# from different studios' release templates rather than forcing a schema.
TITLE_KEYS = ("title", "scene_title", "name", "scene_name", "release_title")
DESCRIPTION_KEYS = ("description", "synopsis", "summary", "scene_description", "notes")
PERFORMER_KEYS = ("performers", "talent", "models", "cast", "actors")
STUDIO_KEYS = ("studio", "studio_name", "label", "brand", "production_company")
LOCATION_KEYS = ("location", "setting", "venue", "shoot_location")
TAGS_KEYS = ("tags", "genres", "categories", "keywords")
PERFORMER_CODE_KEYS = ("performer_code", "code", "scene_code")


@dataclass
class FolderContext:
    """Unified context derived from walking up the scene's ancestors."""
    video_path: Path
    is_generic_filename: bool = False
    # Best-effort merged values pulled from JSON metadata + folder names:
    title: Optional[str] = None
    description: Optional[str] = None
    performers: List[str] = field(default_factory=list)
    studio: Optional[str] = None
    location: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    performer_code: Optional[str] = None
    # All folder names walked, deepest first. Used as text sources for genre
    # detection and performer-code extraction by the existing parsers.
    ancestor_names: List[str] = field(default_factory=list)
    # The actual ancestor folder we considered "the source of truth" — usually
    # the first one with a metadata.json or a parseable performer code.
    source_folder: Optional[Path] = None
    # Raw metadata JSON contents we found (deepest first), preserved so other
    # callers can grab anything we didn't model explicitly.
    metadata_documents: List[Dict[str, Any]] = field(default_factory=list)

    def text_sources(self) -> List[str]:
        """Return all text strings the rest of ingest should scan for genres /
        performer codes — folder names + metadata title/description fields.

        Deepest folder first (so earlier matches win in regex scans).
        """
        out = list(self.ancestor_names)
        if self.title:
            out.append(self.title)
        if self.description:
            out.append(self.description)
        return out


def is_generic_filename(stem: str) -> bool:
    """Return True if the file stem looks like a generic recorder filename."""
    if not stem or not stem.strip():
        return True
    return bool(GENERIC_FILENAME_RE.search(stem))


def resolve_folder_context(
    video_path: Path,
    *,
    max_depth: int = MAX_ANCESTOR_DEPTH,
    boundary_dirs: Optional[List[Path]] = None,
) -> FolderContext:
    """
    Walk up from ``video_path`` collecting whatever folder/JSON metadata exists.

    Always returns a ``FolderContext`` (never raises). Safe to call even when
    the immediate filename is informative — in that case ``is_generic_filename``
    will be False and the parsers can still use this object as a richer source.
    """
    video_path = video_path.expanduser().resolve()
    ctx = FolderContext(video_path=video_path)
    ctx.is_generic_filename = is_generic_filename(video_path.stem)

    if boundary_dirs is None:
        # Stop walking at user home boundaries to avoid escaping into
        # unrelated folders on shared volumes.
        boundary_dirs = []
        try:
            home = Path.home().resolve()
            boundary_dirs.append(home)
        except Exception:
            pass

    visited: List[Path] = []
    cur = video_path.parent
    for _ in range(max_depth):
        if not cur or not cur.exists():
            break
        visited.append(cur)
        ctx.ancestor_names.append(cur.name)

        # Collect metadata JSONs from this ancestor.
        for name in METADATA_FILENAMES:
            doc_path = cur / name
            if doc_path.exists():
                doc = _safe_read_json(doc_path)
                if doc is not None:
                    ctx.metadata_documents.append(doc)
                    if ctx.source_folder is None:
                        ctx.source_folder = cur

        # If this folder name has a performer code, treat it as the source.
        if ctx.source_folder is None and PERFORMER_CODE_PATTERN_RE.match(cur.name):
            ctx.source_folder = cur

        # If the folder has a 2257/release marker, treat it as the source.
        if ctx.source_folder is None and any((cur / m).exists() for m in SUBMISSION_MARKERS):
            ctx.source_folder = cur

        # Stop walking once we cross a boundary directory.
        if any(cur == b for b in boundary_dirs):
            break
        nxt = cur.parent
        if nxt == cur:
            break
        cur = nxt

    # Merge metadata documents (deepest first wins on conflicts).
    for doc in ctx.metadata_documents:
        _merge_metadata_into(ctx, doc)

    # If we still don't have a performer code, scan ancestor names for one.
    if not ctx.performer_code:
        for name in ctx.ancestor_names:
            m = PERFORMER_CODE_PATTERN_RE.match(name)
            if m:
                ctx.performer_code = m.group(1)
                break

    return ctx


def _safe_read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _first_present(doc: Dict[str, Any], keys) -> Optional[Any]:
    for k in keys:
        if k in doc and doc[k] not in (None, "", [], {}):
            return doc[k]
    return None


def _normalize_performer_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        # Allow "Yasmina Khan, Tabatha Lust" or "Yasmina; Tabatha"
        parts = re.split(r"[,;/&]| and | with ", value)
        return [p.strip() for p in parts if p and p.strip()]
    if isinstance(value, list):
        out: List[str] = []
        for v in value:
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
            elif isinstance(v, dict):
                # Common shapes: {"name": "..."} or {"first": ..., "last": ...}
                name = v.get("name") or v.get("display_name") or v.get("stage_name")
                if not name and v.get("first") and v.get("last"):
                    name = f"{v['first']} {v['last']}"
                if name and isinstance(name, str):
                    out.append(name.strip())
        return out
    return []


def _normalize_tags(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [t.strip() for t in re.split(r"[,;]", value) if t.strip()]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _merge_metadata_into(ctx: FolderContext, doc: Dict[str, Any]) -> None:
    """Populate empty FolderContext fields from a metadata document. Earlier
    documents (deeper folders) win because they're processed first."""
    if not ctx.title:
        v = _first_present(doc, TITLE_KEYS)
        if isinstance(v, str) and v.strip():
            ctx.title = v.strip()
    if not ctx.description:
        v = _first_present(doc, DESCRIPTION_KEYS)
        if isinstance(v, str) and v.strip():
            ctx.description = v.strip()
    if not ctx.performers:
        ctx.performers = _normalize_performer_list(_first_present(doc, PERFORMER_KEYS))
    if not ctx.studio:
        v = _first_present(doc, STUDIO_KEYS)
        if isinstance(v, str) and v.strip():
            ctx.studio = v.strip()
    if not ctx.location:
        v = _first_present(doc, LOCATION_KEYS)
        if isinstance(v, str) and v.strip():
            ctx.location = v.strip()
    if not ctx.tags:
        ctx.tags = _normalize_tags(_first_present(doc, TAGS_KEYS))
    if not ctx.performer_code:
        v = _first_present(doc, PERFORMER_CODE_KEYS)
        if isinstance(v, str) and v.strip():
            ctx.performer_code = v.strip().upper()


def best_text_for_parsing(ctx: FolderContext) -> str:
    """
    Build a single text blob the existing genre/title parsers can scan.

    The order matters: generic filename last so its noise doesn't hijack regexes.
    """
    parts: List[str] = []
    if ctx.title:
        parts.append(ctx.title)
    if ctx.description:
        parts.append(ctx.description)
    parts.extend(ctx.ancestor_names)
    if not ctx.is_generic_filename:
        parts.append(ctx.video_path.stem)
    return " | ".join(parts)
