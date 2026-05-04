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
from amg.scoring.prompt import build_scene_insight_prompt, build_enriched_title_prompt
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
    n_suggestions: int = 5,
    language: str = "en",
    ai_client: Optional[AIClient] = None,
) -> Dict[str, Any]:
    """Generate richer titles using vision insight + position rollup.

    Returns:
        {
            "titles": [...],
            "long_description": str,
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
        return {"titles": titles, "long_description": "", "ai_used": False}

    prompt = build_enriched_title_prompt(
        studio=studio,
        performers=performers,
        scene_type=scene_type,
        genres=genres,
        description=description,
        insight=insight_dict,
        position_summary=position_summary,
        language=language,
        n_suggestions=n_suggestions,
    )
    response = ai_client.generate_text(prompt)
    if not response.success:
        log.warn("Enriched title call failed — using fallback", extra={"err": response.error_code})
        titles = _annotate(_fallback_titles(studio, performers, scene_type, genres, n_suggestions))
        return {"titles": titles, "long_description": "", "ai_used": False}

    parsed = _parse_enriched_response(response.raw_text)
    titles = _annotate(parsed["titles"]) if parsed["titles"] else _annotate(
        _fallback_titles(studio, performers, scene_type, genres, n_suggestions)
    )
    return {
        "titles": titles[:n_suggestions],
        "long_description": parsed.get("long_description", ""),
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
    return {"titles": titles, "long_description": long_desc}


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
