from __future__ import annotations

import re

from amg.prompts.loader import list_registered_prompts, load_prompt_registry


PROMPT_ID_RE = re.compile(r"^[a-z0-9_]+$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def test_registry_contains_prompts() -> None:
    registry = list_registered_prompts()
    assert registry, "Expected at least one registered prompt"


def test_all_registered_prompts_load_and_parse() -> None:
    registry = list_registered_prompts()
    for prompt_id, versions in registry.items():
        assert PROMPT_ID_RE.match(prompt_id), f"Invalid prompt_id: {prompt_id}"
        assert versions, f"No versions for prompt_id: {prompt_id}"
        for version in versions:
            assert SEMVER_RE.match(version), f"Invalid semver: {prompt_id}.{version}"
            body = load_prompt_registry(prompt_id, version=version)
            assert body.strip(), f"Empty body: {prompt_id}.{version}"


def test_latest_version_loads_for_each_prompt_id() -> None:
    registry = list_registered_prompts()
    for prompt_id in registry:
        body = load_prompt_registry(prompt_id)
        assert body.strip()
