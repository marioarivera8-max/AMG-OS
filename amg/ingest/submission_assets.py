"""Submission folder asset harvesting.

This module builds a scene-local manifest for the original submission folder:
the selected primary video, any secondary videos, likely compliance documents,
and creator-provided image assets. It is intentionally local and deterministic;
no AI scoring or downstream upload behavior happens here.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from PIL import Image, ImageOps, UnidentifiedImageError

from amg.ingest.inventory import VIDEO_EXTENSIONS, is_video_file, is_work_directory
from amg.video.metadata import get_metadata

SUBMISSION_MANIFEST_NAME = "submission_manifest.json"
SUBMISSION_ASSETS_DIRNAME = "submission_assets"

DOC_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".heic",
    ".heif",
    ".txt",
    ".doc",
    ".docx",
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
METADATA_EXTENSIONS = {".json"}

DOC_TYPE_TOKENS = {
    "model_release": ("model_release", "model release", "release_form", "release form"),
    "2257": ("2257",),
    "id": (" id", "_id", "-id", "passport", "license", "licence", "driver"),
    "release": ("release",),
}

SKIP_IMAGE_TOKENS = {
    "2257",
    "release",
    "passport",
    "license",
    "licence",
    "_id",
    "-id",
    "contact_sheet",
}


def manifest_path_for_work_dir(work_dir: Path) -> Path:
    return Path(work_dir) / SUBMISSION_MANIFEST_NAME


def load_submission_manifest(work_dir: Optional[Path]) -> Optional[Dict[str, Any]]:
    if not work_dir:
        return None
    path = manifest_path_for_work_dir(Path(work_dir))
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def scan_submission_assets(
    *,
    scene_id: str,
    selected_video: Path,
    submission_root: Optional[Path],
    work_dir: Path,
    source_mode: str = "local",
    cloud_source: Optional[Dict[str, Any]] = None,
    source_reference: Optional[str] = None,
    max_files: int = 2000,
) -> Dict[str, Any]:
    """Scan a submission root and persist ``submission_manifest.json``.

    Assets are copied under ``work_dir/submission_assets`` so review and
    packaging do not depend on the original folder still being mounted.
    """
    selected_video = Path(selected_video).expanduser().resolve()
    work_dir = Path(work_dir)
    root = Path(submission_root).expanduser().resolve() if submission_root else selected_video.parent
    if not root.exists() or root.is_file():
        root = selected_video.parent
    root = root.resolve()

    assets_root = work_dir / SUBMISSION_ASSETS_DIRNAME
    docs_dir = assets_root / "documents"
    image_originals_dir = assets_root / "provided_images" / "originals"
    image_previews_dir = assets_root / "provided_images" / "previews"
    for directory in (docs_dir, image_originals_dir, image_previews_dir):
        directory.mkdir(parents=True, exist_ok=True)

    warnings: List[str] = []
    if source_mode == "cloud" and cloud_source and not cloud_source.get("download_root"):
        warnings.append("Cloud file-only selection: submission-folder docs/images were unavailable")
    files = list(_iter_submission_files(root, work_dir=work_dir, max_files=max_files, warnings=warnings))
    videos = [p for p in files if is_video_file(p)]
    secondary_videos = [p for p in videos if p.resolve() != selected_video]

    docs: List[Dict[str, Any]] = []
    images: List[Dict[str, Any]] = []
    metadata_files: List[Dict[str, Any]] = []
    doc_counter = 0
    image_counter = 0

    for path in files:
        if path.resolve() == selected_video:
            continue
        suffix = path.suffix.lower()
        if _is_likely_doc(path):
            doc_counter += 1
            docs.append(
                _copy_document(
                    path=path,
                    root=root,
                    target_dir=docs_dir,
                    index=doc_counter,
                    warnings=warnings,
                )
            )
            continue
        if suffix in IMAGE_EXTENSIONS and not _is_likely_doc(path) and not _looks_like_skipped_image(path):
            image_counter += 1
            images.append(
                _copy_image(
                    path=path,
                    root=root,
                    originals_dir=image_originals_dir,
                    previews_dir=image_previews_dir,
                    index=image_counter,
                    warnings=warnings,
                )
            )
            continue
        if suffix in METADATA_EXTENSIONS and path.name.lower() != SUBMISSION_MANIFEST_NAME:
            metadata_files.append(_asset_row(path=path, root=root, asset_id=f"meta_{len(metadata_files) + 1:03d}"))

    selected_meta = _video_row(selected_video, root=root, asset_id="selected_video")
    if source_reference:
        selected_meta["source_reference"] = source_reference
    if cloud_source:
        selected_meta["cloud_path"] = cloud_source.get("path")

    manifest = {
        "schema_version": 1,
        "scene_id": scene_id,
        "created_at": _utc_now_iso(),
        "source_mode": source_mode,
        "submission_root": str(root),
        "selected_video": selected_meta,
        "secondary_videos": [
            _video_row(p, root=root, asset_id=f"video_{idx:03d}")
            for idx, p in enumerate(secondary_videos, start=1)
        ],
        "compliance_docs": docs,
        "provided_images": images,
        "metadata_files": metadata_files,
        "cloud_source": _sanitize_cloud_source(cloud_source),
        "warnings": warnings,
    }

    path = manifest_path_for_work_dir(work_dir)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
    return manifest


def _iter_submission_files(
    root: Path,
    *,
    work_dir: Path,
    max_files: int,
    warnings: List[str],
) -> Iterable[Path]:
    count = 0
    root = root.resolve()
    work_dir = work_dir.resolve()
    for path in _walk(root):
        if count >= max_files:
            warnings.append(f"Submission scan stopped after {max_files} files")
            return
        try:
            resolved = path.resolve()
        except Exception:
            continue
        if resolved == work_dir or work_dir in resolved.parents:
            continue
        if not path.is_file():
            continue
        count += 1
        yield path


def _walk(root: Path) -> Iterable[Path]:
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except (OSError, PermissionError):
        return
    for entry in entries:
        if entry.is_dir():
            if is_work_directory(entry) or entry.name == SUBMISSION_ASSETS_DIRNAME:
                continue
            yield from _walk(entry)
        else:
            yield entry


def _is_likely_doc(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix not in DOC_EXTENSIONS:
        return False
    text = _token_text(path)
    return any(token in text for tokens in DOC_TYPE_TOKENS.values() for token in tokens)


def _looks_like_skipped_image(path: Path) -> bool:
    text = _token_text(path)
    return any(token in text for token in SKIP_IMAGE_TOKENS)


def _classify_doc(path: Path) -> str:
    text = _token_text(path)
    for doc_type, tokens in DOC_TYPE_TOKENS.items():
        if any(token in text for token in tokens):
            return doc_type
    return "other"


def _copy_document(
    *,
    path: Path,
    root: Path,
    target_dir: Path,
    index: int,
    warnings: List[str],
) -> Dict[str, Any]:
    asset_id = f"doc_{index:03d}"
    target = target_dir / f"{asset_id}_{_safe_filename(path.name)}"
    try:
        shutil.copy2(path, target)
    except Exception as exc:
        warnings.append(f"Could not copy document {path.name}: {exc}")
        target = None
    row = _asset_row(path=path, root=root, asset_id=asset_id)
    row.update(
        {
            "document_type": _classify_doc(path),
            "stored_path": str(target) if target else None,
            "verified_default": True,
            "guessed_performer": _guess_performer_from_filename(path),
        }
    )
    return row


def _copy_image(
    *,
    path: Path,
    root: Path,
    originals_dir: Path,
    previews_dir: Path,
    index: int,
    warnings: List[str],
) -> Dict[str, Any]:
    asset_id = f"img_{index:03d}"
    original_target = originals_dir / f"{asset_id}_{_safe_filename(path.name)}"
    preview_target = previews_dir / f"{asset_id}_{_safe_stem(path.stem)}.jpg"
    conversion_warning = None

    try:
        shutil.copy2(path, original_target)
    except Exception as exc:
        warnings.append(f"Could not copy provided image {path.name}: {exc}")
        original_target = None

    try:
        _write_preview_jpg(path, preview_target)
    except Exception as exc:
        conversion_warning = f"Could not normalize {path.name}: {exc}"
        warnings.append(conversion_warning)
        preview_target = None

    row = _asset_row(path=path, root=root, asset_id=asset_id)
    row.update(
        {
            "stored_path": str(original_target) if original_target else None,
            "preview_path": str(preview_target) if preview_target else None,
            "decision_default": "unused",
            "conversion_warning": conversion_warning,
        }
    )
    return row


def _write_preview_jpg(src: Path, dst: Path) -> None:
    if src.suffix.lower() in {".heic", ".heif"}:
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except Exception as exc:
            raise RuntimeError("pillow-heif unavailable") from exc
    try:
        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((1600, 1600))
            if img.mode not in {"RGB", "L"}:
                img = img.convert("RGB")
            elif img.mode == "L":
                img = img.convert("RGB")
            dst.parent.mkdir(parents=True, exist_ok=True)
            img.save(dst, "JPEG", quality=92, optimize=True)
    except UnidentifiedImageError as exc:
        raise RuntimeError("image format unreadable") from exc


def _asset_row(*, path: Path, root: Path, asset_id: str) -> Dict[str, Any]:
    return {
        "id": asset_id,
        "filename": path.name,
        "relative_path": _relative_path(path, root),
        "original_path": str(path),
        "suffix": path.suffix.lower(),
        "size_bytes": _size(path),
    }


def _video_row(path: Path, *, root: Path, asset_id: str) -> Dict[str, Any]:
    row = _asset_row(path=path, root=root, asset_id=asset_id)
    row["duration_sec"] = None
    try:
        meta = get_metadata(path)
        if meta:
            row["duration_sec"] = meta.get("duration_sec")
            row["resolution"] = f"{meta.get('width', 0)}x{meta.get('height', 0)}"
    except Exception:
        pass
    return row


def _sanitize_cloud_source(cloud_source: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(cloud_source, dict):
        return None
    allowed = {}
    for key in ("remote", "path", "scene_id", "download_root", "relative_path"):
        value = cloud_source.get(key)
        if value:
            allowed[key] = str(value)
    return allowed or None


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return path.name


def _size(path: Path) -> Optional[int]:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _token_text(path: Path) -> str:
    text = f" {path.stem} {path.name} ".lower()
    text = text.replace("-", "_").replace(".", "_")
    return text


def _safe_filename(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    return safe[:120] or "asset"


def _safe_stem(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._-")
    return safe[:80] or "image"


def _guess_performer_from_filename(path: Path) -> str:
    stem = path.stem
    for token in ("model_release", "release_form", "release", "2257", "passport", "license", "licence", "id"):
        stem = re.sub(token, " ", stem, flags=re.IGNORECASE)
    stem = " ".join(stem.replace("_", " ").replace("-", " ").split())
    return stem.title()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
