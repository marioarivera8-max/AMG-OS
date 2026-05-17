"""Versioned prompt registry loader.

Rule 1 from CLAUDE.md section 19 requires prompt bodies to live as files and
be loaded through this utility.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
from typing import Dict, List, Tuple


_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_PROMPT_FILE_RE = re.compile(
    r"^(?P<prompt_id>[a-z0-9_]+)\.(?P<version>\d+\.\d+\.\d+)\.prompt\.md$"
)

_REGISTRY_DIR = Path(__file__).resolve().parent / "registry"


def _parse_semver(version: str) -> Tuple[int, int, int]:
    if not _SEMVER_RE.match(version):
        raise ValueError(f"Invalid semantic version: {version}")
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def _parse_filename(path: Path) -> Tuple[str, str]:
    match = _PROMPT_FILE_RE.match(path.name)
    if not match:
        raise ValueError(f"Invalid prompt filename: {path.name}")
    return match.group("prompt_id"), match.group("version")


def list_registered_prompts() -> Dict[str, List[str]]:
    """Return ``{prompt_id: [versions...]}`` sorted by semantic version."""
    registry: Dict[str, List[str]] = {}
    if not _REGISTRY_DIR.exists():
        return registry

    for path in _REGISTRY_DIR.glob("*.prompt.md"):
        prompt_id, version = _parse_filename(path)
        registry.setdefault(prompt_id, []).append(version)

    for prompt_id, versions in registry.items():
        registry[prompt_id] = sorted(versions, key=_parse_semver)
    return dict(sorted(registry.items()))


@lru_cache(maxsize=256)
def load_prompt_registry(prompt_id: str, version: str | None = None) -> str:
    """Load a prompt body from ``amg/prompts/registry``.

    When ``version`` is omitted, the latest semantic version is selected.
    """
    if not prompt_id or not re.fullmatch(r"[a-z0-9_]+", prompt_id):
        raise ValueError(f"Invalid prompt_id: {prompt_id!r}")

    registry = list_registered_prompts()
    versions = registry.get(prompt_id)
    if not versions:
        raise FileNotFoundError(f"Prompt not found in registry: {prompt_id}")

    target_version = version or versions[-1]
    if target_version not in versions:
        raise FileNotFoundError(
            f"Prompt version not found: {prompt_id}.{target_version}"
        )

    filename = f"{prompt_id}.{target_version}.prompt.md"
    path = _REGISTRY_DIR / filename
    return path.read_text(encoding="utf-8")
