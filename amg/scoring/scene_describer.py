"""
Vision-aware scene insight + enriched title generator.

Two passes once covers exist:

  describe_scene_from_covers()
      Reads the contact sheet (or top covers) through the local vision model
      and extracts: setting, mood, distinctive features, factual action summary.

  generate_titles_with_insight()
      Runs the existing title-generation prompt enriched with:
        - performer names (from folder-context metadata, when available)
        - position labels rolled up from the saved covers
        - scene-insight setting/mood/features
        - studio-known location hints

The describer is intentionally cheap — one or two AI calls. We don't re-score
frames here; we only run a coarse description prompt.

Both functions degrade safely if the AI is offline: they return ``None``
(describer) or fall back to the existing template-based titles (titler).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2

from amg.scoring.ai_client import AIClient
from amg.config import TITLE_TONE_DEFAULT
from amg.scoring.prompt import build_scene_insight_prompt, build_enriched_title_prompt
from amg.scoring.market_profile import (
    build_seed_taxonomy,
    MARKET_CATEGORY_PRIORITIES,
    MARKET_TAG_PRIORITIES,
)
from amg.scoring.title_generator import _fallback_titles, _check_platform_fit, _check_warnings
from amg.utils.logging import get_logger

log = get_logger("scoring.scene_describer")


@dataclass
class SceneInsight:
    """Vision-derived scene insight."""
    setting: Optional[str] = None
    notable_features: List[str] = field(default_factory=list)
    action_summary: Optional[str] = None
    mood: Optional[str] = None
    location_hint: Optional[str] = None
    raw_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "setting": self.setting,
            "notable_features": self.notable_features,
            "action_summary": self.action_summary,
            "mood": self.mood,
            "location_hint": self.location_hint,
            "raw_text": self.raw_text,
        }


# ---------- vision insight ----------

def describe_scene_from_covers(
    *,
    contact_sheet_path: Optional[Path] = None,
    cover_paths: Optional[List[Path]] = None,
    ai_client: Optional[AIClient] = None,
) -> Optional[SceneInsight]:
    """Read a representative image (preferred: contact sheet) and ask the
    vision model for setting / mood / notable features.

    Returns ``None`` if no usable image or the AI call fails.
    """
    img_path = _pick_representative_image(contact_sheet_path, cover_paths)
    if img_path is None:
        log.warn("No image available for scene description", extra={"contact_sheet": str(contact_sheet_path)})
        return None

    if ai_client is None:
        ai_client = AIClient()

    if not ai_client.is_alive():
        log.warn("AI offline — skipping scene description")
        return None

    frame = cv2.imread(str(img_path))
    if frame is None:
        log.warn("Could not read image for scene description", extra={"path": str(img_path)})
        return None

    prompt = build_scene_insight_prompt()
    response = ai_client.score_frame(frame, prompt)
    if not response.success:
        log.warn("Scene insight call failed", extra={"err": response.error_code})
        return None

    return _parse_scene_insight(response.raw_text)


# ---------- enriched title generation ----------

def generate_titles_with_insight(
    *,
    studio: Optional[str],
    performers: List[str],
    scene_type: str,
    genres: List[str],
    description: str,
    insight: Optional[SceneInsight],
    position_summary: Dict[str, int],
    title_tone: str = TITLE_TONE_DEFAULT,
    n_suggestions: int = 5,
    language: str = "en",
    ai_client: Optional[AIClient] = None,
) -> Dict[str, Any]:
    """Generate richer titles using vision insight + position rollup.

    Returns:
        {
            "titles": [...],
            "long_description": str,
            "categories": [...],
            "tags": [...],
            "ai_used": bool,
        }
    """
    if ai_client is None:
        ai_client = AIClient()

    studio = studio or "Unknown"
    insight_dict = insight.to_dict() if insight else {}

    if not ai_client.is_alive():
        log.warn("AI offline — using template title fallback")
        titles = _annotate(_fallback_titles(studio, performers, scene_type, genres, n_suggestions))
        seed = build_seed_taxonomy(genres, position_summary)
        cats = _prioritize_tokens(seed.get("categories", []), MARKET_CATEGORY_PRIORITIES)
        tags = _prioritize_tokens(seed.get("tags", []), MARKET_TAG_PRIORITIES)
        return {
            "titles": titles,
            "long_description": "",
            "categories": cats,
            "tags": tags,
            "title_tone": title_tone,
            "ai_used": False,
        }

    seed_taxonomy = build_seed_taxonomy(genres, position_summary)

    prompt = build_enriched_title_prompt(
        studio=studio,
        performers=performers,
        scene_type=scene_type,
        genres=genres,
        description=description,
        insight=insight_dict,
        position_summary=position_summary,
        seed_taxonomy=seed_taxonomy,
        title_tone=title_tone,
        language=language,
        n_suggestions=n_suggestions,
    )
    response = ai_client.generate_text(prompt)
    if not response.success:
        log.warn("Enriched title call failed — using fallback", extra={"err": response.error_code})
        titles = _annotate(_fallback_titles(studio, performers, scene_type, genres, n_suggestions))
        cats = _prioritize_tokens(seed_taxonomy.get("categories", []), MARKET_CATEGORY_PRIORITIES)
        tags = _prioritize_tokens(seed_taxonomy.get("tags", []), MARKET_TAG_PRIORITIES)
        return {
            "titles": titles,
            "long_description": "",
            "categories": cats,
            "tags": tags,
            "title_tone": title_tone,
            "ai_used": False,
        }

    parsed = _parse_enriched_response(response.raw_text)
    titles = parsed["titles"] if parsed["titles"] else _fallback_titles(
        studio, performers, scene_type, genres, n_suggestions
    )
    lead = _lead_performer_name(performers)
    titles = _enforce_lead_performer_in_titles(titles, lead)
    long_desc = _enforce_lead_performer_in_description(parsed.get("long_description", ""), lead)
    titles = _annotate(titles)
    categories = _prioritize_tokens(
        parsed.get("categories", []) or seed_taxonomy.get("categories", []),
        MARKET_CATEGORY_PRIORITIES,
    )
    tags = _prioritize_tokens(
        parsed.get("tags", []) or seed_taxonomy.get("tags", []),
        MARKET_TAG_PRIORITIES,
    )
    return {
        "titles": titles[:n_suggestions],
        "long_description": long_desc,
        "categories": categories,
        "tags": tags,
        "title_tone": title_tone,
        "ai_used": True,
    }


def summarize_positions(saved_covers: List[Dict[str, Any]]) -> Dict[str, int]:
    """Roll up position / type labels across the saved covers for the prompt."""
    counter: Counter[str] = Counter()
    for c in saved_covers or []:
        t = (c.get("type") or "").upper()
        if t:
            counter[t] += 1
        pos = (c.get("position_label") or "").upper()
        if pos and pos not in {"OTHER", ""}:
            counter[pos] += 1
        if c.get("penetration_visible"):
            counter["EXPLICIT"] += 1
    return dict(counter)


# ---------- internal helpers ----------

def _pick_representative_image(
    contact_sheet_path: Optional[Path],
    cover_paths: Optional[List[Path]],
) -> Optional[Path]:
    if contact_sheet_path and Path(contact_sheet_path).exists():
        return Path(contact_sheet_path)
    if cover_paths:
        for p in cover_paths:
            if p and Path(p).exists():
                return Path(p)
    return None


_RE_SETTING = re.compile(r"^\s*SETTING\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_RE_FEATURES = re.compile(r"^\s*NOTABLE_FEATURES\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_RE_ACTION = re.compile(
    r"^\s*ACTION_SUMMARY\s*:\s*(.+?)(?=\n\s*[A-Z_]+\s*:|\nEND\b|\Z)",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)
_RE_MOOD = re.compile(r"^\s*MOOD\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_RE_LOCATION = re.compile(r"^\s*LOCATION_HINT\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def _parse_scene_insight(raw: str) -> SceneInsight:
    setting = _first_match(_RE_SETTING, raw)
    features_str = _first_match(_RE_FEATURES, raw) or ""
    features = [f.strip() for f in re.split(r"[,;]", features_str) if f.strip()] if features_str else []
    action = _first_match(_RE_ACTION, raw)
    mood = _first_match(_RE_MOOD, raw)
    location = _first_match(_RE_LOCATION, raw)
    return SceneInsight(
        setting=setting,
        notable_features=features,
        action_summary=_clean_multiline(action),
        mood=mood,
        location_hint=location,
        raw_text=raw.strip(),
    )


def _first_match(pattern: re.Pattern, text: str) -> Optional[str]:
    m = pattern.search(text or "")
    if not m:
        return None
    return m.group(1).strip().strip("\"'")


def _clean_multiline(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    return re.sub(r"\s+", " ", s).strip().strip("\"'")


_RE_TITLE = re.compile(r"TITLE_(\d+)\s*:\s*(.+?)(?=\n|$)", re.IGNORECASE)
_RE_STYLE = re.compile(r"STYLE_(\d+)\s*:\s*(\w+)", re.IGNORECASE)
_RE_LONGDESC = re.compile(r"LONG_DESCRIPTION\s*:\s*(.+?)(?=\nEND\b|\Z)", re.IGNORECASE | re.DOTALL)
_RE_CATEGORY_SUGGESTIONS = re.compile(
    r"CATEGORY_SUGGESTIONS\s*:\s*(.+?)(?=\n\s*[A-Z_]+\s*:|\nEND\b|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_RE_TAG_SUGGESTIONS = re.compile(
    r"TAG_SUGGESTIONS\s*:\s*(.+?)(?=\n\s*[A-Z_]+\s*:|\nEND\b|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def _parse_enriched_response(raw: str) -> Dict[str, Any]:
    title_matches = {int(m.group(1)): m.group(2).strip().strip("\"'") for m in _RE_TITLE.finditer(raw)}
    style_matches = {int(m.group(1)): m.group(2).strip().lower() for m in _RE_STYLE.finditer(raw)}
    titles = []
    for idx in sorted(title_matches.keys()):
        titles.append({
            "text": title_matches[idx],
            "style": style_matches.get(idx, "unknown"),
        })
    desc_match = _RE_LONGDESC.search(raw)
    long_desc = ""
    if desc_match:
        long_desc = re.sub(r"\s+", " ", desc_match.group(1)).strip().strip("\"'")
    categories = _parse_csv_field(_RE_CATEGORY_SUGGESTIONS.search(raw))
    tags = _parse_csv_field(_RE_TAG_SUGGESTIONS.search(raw))
    return {
        "titles": titles,
        "long_description": long_desc,
        "categories": categories,
        "tags": tags,
    }


def _parse_csv_field(match: Optional[re.Match]) -> List[str]:
    if not match:
        return []
    raw = re.sub(r"\s+", " ", match.group(1)).strip().strip("\"'")
    if not raw or raw.upper() == "NONE":
        return []
    out = []
    for token in raw.split(","):
        t = token.strip()
        if t:
            out.append(t)
    return list(dict.fromkeys(out))


def _lead_performer_name(performers: List[str]) -> str:
    """Choose a lead performer token for title/description enforcement."""
    if not performers:
        return ""
    lead = (performers[0] or "").strip()
    if not lead:
        return ""
    # Prefer first token for compact retail titles; keep full in long description.
    first = lead.split()[0].strip()
    return first or lead


def _enforce_lead_performer_in_titles(titles: List[Dict[str, Any]], lead: str) -> List[Dict[str, Any]]:
    if not lead:
        return titles
    out: List[Dict[str, Any]] = []
    for t in titles:
        text = (t.get("text") or "").strip()
        if not text:
            out.append(t)
            continue
        if lead.lower() not in text.lower():
            with_suffix = f"{text} - {lead}"
            if len(with_suffix) <= 80:
                text = with_suffix
            else:
                with_prefix = f"{lead}: {text}"
                if len(with_prefix) <= 80:
                    text = with_prefix
                else:
                    keep = max(10, 80 - len(lead) - 3)
                    text = f"{text[:keep].rstrip()} - {lead}"
        out.append({**t, "text": text})
    return out


def _enforce_lead_performer_in_description(description: str, lead: str) -> str:
    if not lead:
        return description
    desc = (description or "").strip()
    if not desc:
        return desc
    if lead.lower() in desc.lower():
        return desc
    return f"{lead} leads this scene. {desc}"


def _prioritize_tokens(values: List[str], priority: List[str]) -> List[str]:
    """Sort tokens with known market-priority vocabulary first."""
    if not values:
        return []
    prio_index = {p.lower(): i for i, p in enumerate(priority)}
    deduped = list(dict.fromkeys(v.strip() for v in values if v and v.strip()))
    ranked = sorted(
        deduped,
        key=lambda v: (prio_index.get(v.lower(), 10_000), v.lower()),
    )
    return ranked


def _annotate(titles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add char_count, platform_fit, warnings — same shape generate_titles emits."""
    out = []
    for t in titles:
        text = t.get("text", "")
        out.append({
            **t,
            "char_count": len(text),
            "platform_fit": _check_platform_fit(text),
            "warnings": _check_warnings(text),
        })
    return out
