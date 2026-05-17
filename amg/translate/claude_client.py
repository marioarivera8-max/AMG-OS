"""Claude API polish layer (optional)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from . import config
from . import spend_tracker
from .translate_log import translate_logger

logger = logging.getLogger(__name__)

_batches_this_run = 0
_logged_api_cap = False
_logged_spend_cap = False


def reset_polish_limits_for_run() -> None:
    global _batches_this_run, _logged_api_cap, _logged_spend_cap
    _batches_this_run = 0
    _logged_api_cap = False
    _logged_spend_cap = False


@dataclass
class PolishRequest:
    scene_code: str
    source_title: str
    filename: str
    studio: str
    archetype: str
    draft_title: str
    draft_description: str
    tags: list[str]
    risk: str


@dataclass
class PolishResult:
    scene_code: str
    title: str
    description: str
    tags: list[str]
    confidence: str


SYSTEM_PROMPT = """You polish adult-content scene titles and descriptions for AMG.

Title: 5-10 words, Title Case, no emoji.
Description: 8-14 words, punchy, sensory.
Tags: 3-6, ordered Genre, Archetype, Action, Modifier, Geo.

FORBIDDEN: teen, schoolgirl, barely legal, forced, raped, drugged, choked, strangled, real sister/brother/mom/dad, biological, incest.

Return a JSON array of objects with scene_code, title, description, tags, confidence (high/medium/low)."""


def polish_batch(requests: list[PolishRequest]) -> list[PolishResult]:
    global _batches_this_run, _logged_api_cap, _logged_spend_cap

    if not requests or not config.USE_CLAUDE_POLISH or not config.CLAUDE_API_KEY:
        return []

    if not spend_tracker.can_charge_batch():
        if not _logged_spend_cap:
            translate_logger().warning(
                "Daily spend cap $%.2f reached, falling back to rules-only.",
                config.DAILY_SPEND_CAP_USD,
            )
            _logged_spend_cap = True
        return []

    if _batches_this_run >= config.MAX_API_BATCHES_PER_RUN:
        if not _logged_api_cap:
            translate_logger().warning("API cap reached, falling back to rules-only for remaining rows.")
            _logged_api_cap = True
        return []

    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic SDK not installed")
        return []

    client = anthropic.Anthropic(api_key=config.CLAUDE_API_KEY)
    rows_json = [
        {
            "scene_code": r.scene_code,
            "source_title": r.source_title,
            "filename": r.filename,
            "studio": r.studio,
            "archetype": r.archetype,
            "draft_title": r.draft_title,
            "draft_description": r.draft_description,
            "tags": r.tags,
            "risk": r.risk,
        }
        for r in requests
    ]
    user_msg = f"Polish these scene rows:\n\n{json.dumps(rows_json, ensure_ascii=False, indent=2)}"

    try:
        resp = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=config.CLAUDE_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as e:
        logger.error("Claude API call failed: %s", e)
        return []

    spend_tracker.record_batch_charge()
    _batches_this_run += 1

    text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    results: list[PolishResult] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        results.append(
            PolishResult(
                scene_code=item.get("scene_code", ""),
                title=item.get("title", "").strip(),
                description=item.get("description", "").strip(),
                tags=[t.strip() for t in item.get("tags", []) if t and t.strip()],
                confidence=item.get("confidence", "medium").lower(),
            )
        )
    return results
