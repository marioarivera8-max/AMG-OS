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
from amg.learning.example_bank import retrieve_top_k_examples
from amg.scoring.prompt import build_scene_insight_prompt, build_enriched_title_prompt
from amg.scoring.market_profile import (
    build_seed_taxonomy,
    MARKET_CATEGORY_PRIORITIES,
    MARKET_TAG_PRIORITIES,
    MARKET_TERMS_TO_AVOID,
    CATEGORY_COUNT_MIN,
    CATEGORY_COUNT_MAX,
    TAG_COUNT_MIN,
    TAG_COUNT_MAX,
    CATEGORY_ALIASES,
    TAG_ALIASES,
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
    rule_pack: Optional[Dict[str, Any]] = None,
    analysis_context: str = "",
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
        cats = _normalize_categories([], seed.get("categories", []))
        tags = _normalize_tags([], seed.get("tags", []))
        long_desc = _normalize_long_description(
            "",
            performers=performers,
            studio=studio,
            scene_type=scene_type,
            genres=genres,
            insight=insight_dict,
            position_summary=position_summary,
        )
        return {
            "titles": titles,
            "long_description": long_desc,
            "categories": cats,
            "tags": tags,
            "title_tone": title_tone,
            "ai_used": False,
            "text_model_effective": None,
            "text_model_fallback_used": False,
            "text_model_fallback_model": None,
        }

    seed_taxonomy = build_seed_taxonomy(genres, position_summary)
    retrieval_stage = _resolve_retrieval_stage(rule_pack)
    retrieval_scope = _retrieval_scope_for_stage(retrieval_stage)
    retrieval_top_k = _resolve_retrieval_top_k(rule_pack)
    top_examples = retrieve_top_k_examples(
        studio=studio,
        scene_type=scene_type,
        genres=genres,
        position_summary=position_summary,
        performers=performers,
        top_k=retrieval_top_k,
    ) if retrieval_scope != "off" else []

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
        top_examples=top_examples,
        retrieval_scope=retrieval_scope if retrieval_scope != "off" else "titles",
        analysis_context=analysis_context,
    )
    response = ai_client.generate_text(prompt)
    if not response.success:
        log.warn("Enriched title call failed — using fallback", extra={"err": response.error_code})
        titles = _annotate(_fallback_titles(studio, performers, scene_type, genres, n_suggestions))
        cats = _normalize_categories([], seed_taxonomy.get("categories", []))
        tags = _normalize_tags([], seed_taxonomy.get("tags", []))
        long_desc = _normalize_long_description(
            "",
            performers=performers,
            studio=studio,
            scene_type=scene_type,
            genres=genres,
            insight=insight_dict,
            position_summary=position_summary,
        )
        return {
            "titles": titles,
            "long_description": long_desc,
            "categories": cats,
            "tags": tags,
            "title_tone": title_tone,
            "ai_used": False,
            "text_model_effective": None,
            "text_model_fallback_used": False,
            "text_model_fallback_model": None,
        }

    response_meta = response.extras or {}
    parsed = _parse_enriched_response(response.raw_text)
    titles = parsed["titles"] if parsed["titles"] else _fallback_titles(
        studio, performers, scene_type, genres, n_suggestions
    )
    titles = _sanitize_title_candidates(titles)
    if not titles:
        log.warn("All AI titles filtered as low-quality, using fallback templates")
        titles = _fallback_titles(studio, performers, scene_type, genres, n_suggestions)
    lead = _lead_performer_name(performers)
    titles = _enforce_lead_performer_in_titles(titles, lead)
    titles = _sanitize_title_candidates(titles)
    titles = _rank_and_balance_titles(
        titles,
        lead=lead,
        insight=insight_dict,
        n_suggestions=n_suggestions,
    )
    if not titles:
        titles = _fallback_titles(studio, performers, scene_type, genres, n_suggestions)
    long_desc = _normalize_long_description(
        parsed.get("long_description", ""),
        performers=performers,
        studio=studio,
        scene_type=scene_type,
        genres=genres,
        insight=insight_dict,
        position_summary=position_summary,
    )
    long_desc = _enforce_lead_performer_in_description(long_desc, lead)
    titles = _annotate(titles)
    categories = _normalize_categories(parsed.get("categories", []), seed_taxonomy.get("categories", []))
    tags = _normalize_tags(parsed.get("tags", []), seed_taxonomy.get("tags", []))
    constraints = ((rule_pack or {}).get("constraints") or {}) if isinstance(rule_pack, dict) else {}
    titles = _apply_rule_pack_to_titles(titles, constraints)
    long_desc = _apply_rule_pack_to_description(long_desc, constraints)
    categories = _apply_rule_pack_priority(categories, constraints.get("category_boost"))
    tags = _apply_rule_pack_priority(tags, constraints.get("tag_boost"))
    if constraints.get("description_min_chars"):
        min_chars = int(constraints.get("description_min_chars") or 0)
        while min_chars > 0 and len(long_desc) < min_chars:
            long_desc = (long_desc + " " + "Optimized for shelf clarity and searchable scene context.").strip()
    if constraints.get("description_max_chars"):
        max_chars = int(constraints.get("description_max_chars") or 0)
        if max_chars > 0 and len(long_desc) > max_chars:
            long_desc = long_desc[:max_chars].rstrip()
    return {
        "titles": titles[:n_suggestions],
        "long_description": long_desc,
        "categories": categories,
        "tags": tags,
        "title_tone": title_tone,
        "ai_used": True,
        "text_model_effective": response_meta.get("model_used") or getattr(ai_client, "text_model", None),
        "text_model_fallback_used": bool(response_meta.get("fallback_model_used")),
        "text_model_fallback_model": response_meta.get("fallback_model_used"),
        "rule_pack_id": (rule_pack or {}).get("rule_pack_id") if isinstance(rule_pack, dict) else None,
        "retrieval_stage": retrieval_stage,
        "retrieval_scope": retrieval_scope,
        "retrieved_examples_count": len(top_examples),
    }


def _sanitize_title_candidates(titles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Remove malformed/repetitive title candidates (common model failure mode).
    """
    out: List[Dict[str, Any]] = []
    seen = set()
    seen_word_sets: List[set[str]] = []
    seen_prefixes: set[str] = set()
    for t in titles or []:
        raw = (t.get("text") or "").strip()
        text = re.sub(r"\s+", " ", raw).strip(" \"'")
        text = _strip_avoid_terms(text)
        text = re.sub(r"\s+", " ", text).strip(" \"'")
        if not text:
            continue
        lower = text.lower()
        if lower in seen:
            continue
        # Reject highly repetitive gibberish-like outputs.
        words = re.findall(r"[A-Za-z0-9']+", lower)
        if words:
            uniq_ratio = len(set(words)) / max(1, len(words))
            if len(words) >= 10 and uniq_ratio < 0.35:
                continue
            max_run = 1
            run = 1
            for i in range(1, len(words)):
                if words[i] == words[i - 1]:
                    run += 1
                    max_run = max(max_run, run)
                else:
                    run = 1
            if max_run >= 4:
                continue
        if len(text) < 18:
            continue
        if len(text) > 110:
            text = text[:110].rstrip()

        # Reject near-duplicate variants ("same title with one extra word").
        prefix = " ".join(words[:4]) if words else ""
        if prefix and prefix in seen_prefixes:
            continue
        current_words = set(words)
        is_near_dup = False
        for prev_words in seen_word_sets:
            union = current_words | prev_words
            if len(union) < 4:
                continue
            overlap = len(current_words & prev_words) / max(1, len(union))
            if overlap >= 0.72:
                is_near_dup = True
                break
        if is_near_dup:
            continue

        seen.add(lower)
        if prefix:
            seen_prefixes.add(prefix)
        if current_words:
            seen_word_sets.append(current_words)
        out.append({**t, "text": text})
    return out


def _rank_and_balance_titles(
    titles: List[Dict[str, Any]],
    *,
    lead: str,
    insight: Dict[str, Any],
    n_suggestions: int,
) -> List[Dict[str, Any]]:
    if not titles:
        return []
    scored: List[tuple[int, Dict[str, Any], set[str]]] = []
    for t in titles:
        text = str((t or {}).get("text") or "").strip()
        words = _title_wordset(text)
        if not words:
            continue
        score = 0
        if lead and lead.lower() in text.lower():
            score += 3
        if words & _ACTION_HINT_WORDS:
            score += 2
        if words & _insight_hint_words(insight):
            score += 2
        if words & _GENERIC_TITLE_WORDS:
            score -= 2
        score += max(0, min(2, len(words) // 6))
        scored.append((score, t, words))
    scored.sort(key=lambda x: x[0], reverse=True)

    picked: List[Dict[str, Any]] = []
    picked_words: List[set[str]] = []
    for _score, t, words in scored:
        too_close = False
        for prev in picked_words:
            union = words | prev
            if not union:
                continue
            overlap = len(words & prev) / max(1, len(union))
            if overlap >= 0.68:
                too_close = True
                break
        if too_close:
            continue
        picked.append(t)
        picked_words.append(words)
        if len(picked) >= n_suggestions:
            break

    if len(picked) < n_suggestions:
        for _score, t, _words in scored:
            if t in picked:
                continue
            picked.append(t)
            if len(picked) >= n_suggestions:
                break
    return picked


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
    for token in re.split(r"[,;\n|]", raw):
        t = token.strip()
        if t:
            out.append(t)
    return list(dict.fromkeys(out))


def _normalize_categories(values: List[str], seed_values: List[str]) -> List[str]:
    merged = list(values or []) + list(seed_values or [])
    cleaned: List[str] = []
    for token in merged:
        t = re.sub(r"\s+", " ", str(token or "").strip())
        if not t:
            continue
        t = re.sub(r"[^\w\s&+\-]", "", t)
        if not t:
            continue
        alias = CATEGORY_ALIASES.get(t.lower())
        if alias:
            t = alias
        if t.lower() == "pov":
            t = "POV"
        else:
            t = " ".join(part.capitalize() for part in t.split())
        cleaned.append(t)
    prioritized = _prioritize_tokens(cleaned, MARKET_CATEGORY_PRIORITIES)
    if len(prioritized) < CATEGORY_COUNT_MIN:
        for c in MARKET_CATEGORY_PRIORITIES:
            if c not in prioritized:
                prioritized.append(c)
            if len(prioritized) >= CATEGORY_COUNT_MIN:
                break
    return prioritized[:CATEGORY_COUNT_MAX]


def _normalize_tags(values: List[str], seed_values: List[str]) -> List[str]:
    merged = list(values or []) + list(seed_values or [])
    cleaned: List[str] = []
    avoid = {t.lower() for t in MARKET_TERMS_TO_AVOID}
    generic_noise = {
        "porn", "sex", "video", "scene", "adult", "hot", "sexy",
        "beautiful", "amazing", "intense", "hardcore",
    }
    for token in merged:
        t = re.sub(r"\s+", " ", str(token or "").strip().lower())
        if not t:
            continue
        t = t.replace("_", " ").replace("-", " ")
        t = re.sub(r"[^\w\s]", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        if not t or t in avoid:
            continue
        t = TAG_ALIASES.get(t, t)
        if len(t) < 3 or len(t) > 32:
            continue
        if t in generic_noise:
            continue
        cleaned.append(t)
    prioritized = _prioritize_tokens(cleaned, MARKET_TAG_PRIORITIES)
    if len(prioritized) < TAG_COUNT_MIN:
        for t in MARKET_TAG_PRIORITIES:
            if t not in prioritized:
                prioritized.append(t)
            if len(prioritized) >= TAG_COUNT_MIN:
                break
    return prioritized[:TAG_COUNT_MAX]


def _normalize_long_description(
    raw: str,
    *,
    performers: List[str],
    studio: str,
    scene_type: str,
    genres: List[str],
    insight: Dict[str, Any],
    position_summary: Optional[Dict[str, int]] = None,
) -> str:
    text = re.sub(r"\s+", " ", (raw or "").strip())
    text = _strip_avoid_terms(text)
    text = _dedupe_description_sentences(text)
    if text:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        if len(sentences) > 4:
            text = " ".join(sentences[:4])
        elif len(sentences) < 2:
            text = text.rstrip(".!?") + "."
    if not text:
        lead = (performers[0] if performers else "The lead performer").strip() or "The lead performer"
        setting = (insight.get("setting") or "a private setting").strip()
        mood = (insight.get("mood") or "intense").strip()
        action = (insight.get("action_summary") or "").strip()
        genre_hint = ", ".join(genres[:3]) if genres else scene_type.lower()
        text = (
            f"{lead} headlines this {studio} scene set in {setting}. "
            f"The tone stays {mood}, with {genre_hint} action throughout."
        )
        if action:
            text += f" {action}"
    action_hint = _description_action_hint(genres, position_summary or {})
    if action_hint and action_hint.lower() not in text.lower():
        text = (text.rstrip(".!?") + f". {action_hint}.").strip()
    minimum_chars = 170
    if len(text) < minimum_chars:
        lead = (performers[0] if performers else "The lead performer").strip() or "The lead performer"
        setting = (insight.get("setting") or "a private setting").strip()
        mood = (insight.get("mood") or "confident").strip()
        filler = (
            f" {lead} keeps the pace {mood} in {setting}, with clean retail framing and clear action continuity."
        )
        while len(text) < minimum_chars:
            text = (text + filler).strip()
    text = _strip_avoid_terms(text)
    text = _dedupe_description_sentences(text)
    return text[:520].strip()


def _strip_avoid_terms(text: str) -> str:
    out = str(text or "")
    for term in MARKET_TERMS_TO_AVOID:
        pat = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
        out = pat.sub("", out)
    out = re.sub(r"\s+", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    return out.strip()


def _dedupe_description_sentences(text: str) -> str:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", str(text or "")) if s.strip()]
    out: List[str] = []
    seen: set[str] = set()
    for s in sentences:
        key = re.sub(r"[^a-z0-9 ]+", "", s.lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(s)
    return " ".join(out).strip()


def _description_action_hint(genres: List[str], position_summary: Dict[str, int]) -> str:
    g = {str(x).upper() for x in (genres or [])}
    p = {str(k).upper() for k in (position_summary or {}).keys()}
    if "ANAL" in g:
        return "Action focus includes explicit anal beats and sustained POV readability"
    if "SQUIRT" in g:
        return "Action focus includes squirting cues with strong close-up continuity"
    if "POV" in g:
        return "Action focus includes direct POV framing and eye-contact-forward moments"
    if "DOGGY" in p:
        return "Action focus includes doggy-style sequences with clear composition"
    if "MISSIONARY" in p:
        return "Action focus includes missionary sequences with readable framing"
    return "Action focus stays explicit, varied, and commercially clear across the scene"


def _title_wordset(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", str(text or "").lower()) if w}


def _insight_hint_words(insight: Dict[str, Any]) -> set[str]:
    bits: List[str] = []
    for key in ("setting", "location_hint", "action_summary", "mood"):
        bits.append(str((insight or {}).get(key) or ""))
    for f in (insight or {}).get("notable_features") or []:
        bits.append(str(f))
    return _title_wordset(" ".join(bits))


_ACTION_HINT_WORDS = {
    "pov", "anal", "blowjob", "deepthroat", "doggy", "cowgirl",
    "missionary", "rimjob", "creampie", "squirting", "threesome", "solo",
}

_GENERIC_TITLE_WORDS = {
    "hot", "sexy", "amazing", "intense", "wild", "crazy", "naughty",
}


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


def _apply_rule_pack_to_titles(titles: List[Dict[str, Any]], constraints: Dict[str, Any]) -> List[Dict[str, Any]]:
    banned = {str(x).strip().lower() for x in (constraints.get("banned_title_terms") or []) if str(x).strip()}
    prefixes = [str(x).strip() for x in (constraints.get("title_prefixes") or []) if str(x).strip()]
    suffixes = [str(x).strip() for x in (constraints.get("title_suffixes") or []) if str(x).strip()]
    required_tokens = [str(x).strip() for x in (constraints.get("required_title_tokens") or []) if str(x).strip()]
    out: List[Dict[str, Any]] = []
    for t in titles or []:
        text = str((t or {}).get("text") or "").strip()
        if not text:
            continue
        low = text.lower()
        if any(term in low for term in banned):
            continue
        if required_tokens and not any(tok.lower() in low for tok in required_tokens):
            # nudge, don't replace: append first required token if room exists
            add = required_tokens[0]
            if add.lower() not in low and len(text) + len(add) + 3 <= 110:
                text = f"{text} - {add}"
        if prefixes:
            pref = prefixes[0]
            if pref.lower() not in low and len(pref) + len(text) + 2 <= 110:
                text = f"{pref}: {text}"
        if suffixes:
            suf = suffixes[0]
            if suf.lower() not in text.lower() and len(text) + len(suf) + 3 <= 110:
                text = f"{text} - {suf}"
        out.append({**t, "text": text})
    return _annotate(_sanitize_title_candidates(out))


def _apply_rule_pack_to_description(description: str, constraints: Dict[str, Any]) -> str:
    text = str(description or "").strip()
    boost = [str(x).strip() for x in (constraints.get("description_phrase_boost") or []) if str(x).strip()]
    for phrase in boost[:2]:
        if phrase.lower() not in text.lower():
            text = (text + " " + phrase).strip()
    return text


def _apply_rule_pack_priority(values: List[str], priority_tokens: Any) -> List[str]:
    priority = [str(x).strip() for x in (priority_tokens or []) if str(x).strip()]
    if not priority:
        return values
    return _prioritize_tokens(values, priority)


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


def _resolve_retrieval_stage(rule_pack: Optional[Dict[str, Any]]) -> str:
    constraints = ((rule_pack or {}).get("constraints") or {}) if isinstance(rule_pack, dict) else {}
    stage = str(constraints.get("retrieval_stage") or "").strip().lower()
    if stage in {"off", "titles", "titles_description", "full"}:
        return stage
    if rule_pack:
        return "titles"
    return "off"


def _resolve_retrieval_top_k(rule_pack: Optional[Dict[str, Any]]) -> int:
    constraints = ((rule_pack or {}).get("constraints") or {}) if isinstance(rule_pack, dict) else {}
    try:
        value = int(constraints.get("retrieval_top_k", 3))
    except (TypeError, ValueError):
        value = 3
    return max(1, min(value, 5))


def _retrieval_scope_for_stage(stage: str) -> str:
    s = str(stage or "off").strip().lower()
    if s in {"off", "titles", "titles_description", "full"}:
        return s
    return "off"
