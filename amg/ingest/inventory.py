"""
Inventory — discover scenes in folder structures.

A "scene" is a video file (mp4/mov/mkv/etc.) optionally with companion files
(2257 docs, JSON metadata) in the same folder.
"""
from pathlib import Path
from typing import List, Optional, Iterator

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".wmv", ".flv"}

# Files to skip when discovering scenes
SKIP_DIR_NAMES = {"venv", ".venv", "__pycache__", ".git", "node_modules", ".DS_Store"}
SKIP_DIR_PREFIXES = {"_amg_", "."}  # _amg_v10_3, _amg_v11, .hidden


def is_video_file(path: Path) -> bool:
    """Return True if path looks like a video file we can process."""
    if not path.is_file():
        return False
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        return False
    # Skip files smaller than 1MB (probably not real scenes)
    try:
        if path.stat().st_size < 1024 * 1024:
            return False
    except OSError:
        return False
    return True


def is_work_directory(path: Path) -> bool:
    """Return True if path is an AMG work directory (should be skipped)."""
    name = path.name
    if name in SKIP_DIR_NAMES:
        return True
    return any(name.startswith(prefix) for prefix in SKIP_DIR_PREFIXES)


def discover_scenes(root: Path, recursive: bool = True) -> List[Path]:
    """
    Find all video files under root.

    Args:
        root: Folder to search
        recursive: If True, search subdirectories

    Returns:
        Sorted list of video file paths
    """
    root = Path(root).resolve()
    if not root.exists():
        return []

    if root.is_file() and is_video_file(root):
        return [root]

    found = []
    if recursive:
        for path in _walk(root):
            if is_video_file(path):
                found.append(path)
    else:
        for path in root.iterdir():
            if is_video_file(path):
                found.append(path)

    return sorted(found)


def _walk(root: Path) -> Iterator[Path]:
    """Yield all files under root, skipping work directories."""
    try:
        entries = list(root.iterdir())
    except (PermissionError, OSError):
        return

    for entry in entries:
        if entry.is_dir():
            if is_work_directory(entry):
                continue
            yield from _walk(entry)
        else:
            yield entry


def find_companion_files(video_path: Path) -> dict:
    """
    Find companion files alongside a video.

    Returns:
        {
            'doc_2257': Path or None,
            'metadata_json': Path or None,
            'cover_psd': Path or None,
        }
    """
    parent = video_path.parent
    result = {
        "doc_2257": None,
        "metadata_json": None,
        "cover_psd": None,
    }

    # Look for 2257 docs (multiple possible filenames)
    for name in ("2257.pdf", "2257.jpg", "2257.png", "id.pdf", "ID.pdf"):
        candidate = parent / name
        if candidate.exists():
            result["doc_2257"] = candidate
            break

    # Look for metadata JSON
    for name in (f"{video_path.stem}.json", "metadata.json", "scene.json"):
        candidate = parent / name
        if candidate.exists():
            result["metadata_json"] = candidate
            break

    # Look for PSD template
    for ext in (".psd", ".PSD"):
        for candidate in parent.glob(f"*{ext}"):
            result["cover_psd"] = candidate
            break
        if result["cover_psd"]:
            break

    return result


def make_work_dir(video_path: Path, version: str = "v11") -> Path:
    """
    Compute the work directory path for a scene.

    Format: {scene_folder}/{scene_stem}_amg_{version}/
    """
    return video_path.parent / f"{video_path.stem}_amg_{version}"


def make_covers_dir(work_dir: Path) -> Path:
    """Compute the covers subdirectory within a work dir."""
    return work_dir / "covers"
