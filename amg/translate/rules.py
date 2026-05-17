"""YAML-backed rules: archetypes, studios, compliance, style."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

DATA_DIR = Path(__file__).resolve().parent / "data"
ARCHETYPES_DIR = DATA_DIR / "archetypes"
UNIVERSAL_STEM = "_universal"
SUPPORTED_LANGUAGES = ["de", "es", "it", "pt", "fr", "ja", "ko", "zh"]

_BARE_FILENAME = re.compile(r"(?i)^Produce_\d+\.(mp4|mov|mkv|m4v)$")


@lru_cache(maxsize=1)
def _forbidden_raw() -> dict:
    return yaml.safe_load((DATA_DIR / "forbidden_terms.yaml").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _language_pack(lang_code: str) -> dict:
    path = ARCHETYPES_DIR / f"{lang_code}.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=1)
def list_available_languages() -> tuple[str, ...]:
    if not ARCHETYPES_DIR.exists():
        return ()
    return tuple(
        sorted(f.stem for f in ARCHETYPES_DIR.glob("*.yaml") if not f.stem.startswith("_"))
    )


@lru_cache(maxsize=1)
def _style() -> dict:
    return yaml.safe_load((DATA_DIR / "style_rules.yaml").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _studios() -> dict:
    return yaml.safe_load((DATA_DIR / "studios.yaml").read_text(encoding="utf-8"))


@dataclass
class ArchetypeMatch:
    archetype: str
    tags: list[str]
    risk: str
    priority: int
    matched_keyword: str
    pack: str


@dataclass
class ComplianceResult:
    is_clean: bool
    hard_banned: list[str] = field(default_factory=list)
    softened_hits: list[str] = field(default_factory=list)
    watch_hits: list[str] = field(default_factory=list)
    risk_level: str = "LOW"


@dataclass
class StudioConfig:
    name: str
    geo_identifier: str
    default_archetype: str
    code_prefix: str
    voice_notes: str
    source_language: str = ""


def is_bare_produce_filename(filename: str) -> bool:
    fn = (filename or "").strip().split("/")[-1]
    return bool(_BARE_FILENAME.match(fn))


def check_compliance(text: str) -> ComplianceResult:
    if not text:
        return ComplianceResult(is_clean=True)
    text_lower = text.lower()
    fb = _forbidden_raw()
    result = ComplianceResult(is_clean=True)
    for term in fb.get("hard_ban", []):
        if _whole_word_or_phrase_match(term, text_lower):
            result.hard_banned.append(term)
            result.is_clean = False
    watch = fb.get("watch", {})
    for terms in watch.values():
        for term in terms:
            if _whole_word_or_phrase_match(term, text_lower):
                result.watch_hits.append(term)
    if result.hard_banned:
        result.risk_level = "HIGH"
    elif result.watch_hits:
        result.risk_level = "MED"
    return result


def apply_soften_title(title: str) -> tuple[str, list[str]]:
    """Apply soften dictionary to title only (case-aware substring replace)."""
    if not title:
        return title, []
    fb = _forbidden_raw()
    soften = fb.get("soften", {})
    hits: list[str] = []
    out = title
    # Longer keys first to prefer multi-word replacements
    for src in sorted(soften.keys(), key=len, reverse=True):
        dst = soften[src]
        pattern = re.compile(re.escape(src), re.IGNORECASE)

        def _sub(m: re.Match[str]) -> str:
            hits.append(src)
            raw = m.group(0)
            if raw.isupper():
                return dst.upper()
            if raw[:1].isupper():
                return dst[:1].upper() + dst[1:] if len(dst) > 1 else dst.upper()
            return dst.lower()

        out = pattern.sub(_sub, out)
    if out and out[0].isalpha() and out[0].islower():
        out = out[0].upper() + out[1:]
    return out, hits


def _whole_word_or_phrase_match(needle: str, haystack: str) -> bool:
    n = needle.lower().strip()
    if " " in n:
        return n in haystack
    pattern = r"(?<![a-z])" + re.escape(n) + r"(?![a-z])"
    return re.search(pattern, haystack) is not None


def scan_pack(source_lower: str, pack_stem: str) -> list[ArchetypeMatch]:
    pack = _language_pack(pack_stem)
    found: list[ArchetypeMatch] = []
    for entry in pack.get("mappings", []):
        for keyword in entry["match"]:
            kw = keyword.lower()
            if kw in source_lower:
                found.append(
                    ArchetypeMatch(
                        archetype=entry["archetype"],
                        tags=list(entry["tags"]),
                        risk=str(entry.get("risk", "LOW")),
                        priority=int(entry.get("priority", 0)),
                        matched_keyword=keyword,
                        pack=pack_stem,
                    )
                )
                break
    return found


def detect_archetypes(source_text: str, language: Optional[str]) -> list[ArchetypeMatch]:
    """Universal pack always scanned; language pack added when known."""
    if not source_text:
        return []
    src = source_text.lower()
    matches: list[ArchetypeMatch] = []
    matches.extend(scan_pack(src, UNIVERSAL_STEM))
    lang = language if language in list_available_languages() else None
    if lang:
        matches.extend(scan_pack(src, lang))
    matches.sort(key=lambda m: (m.priority, m.risk == "MED", len(m.matched_keyword)), reverse=True)
    return matches


def detect_language(source_text: str) -> Optional[str]:
    if not source_text:
        return None
    src = source_text.lower()
    scores: dict[str, int] = {}
    for lang in list_available_languages():
        pack = _language_pack(lang)
        score = 0
        max_pri = 0
        for entry in pack.get("mappings", []):
            hit = False
            for keyword in entry["match"]:
                if keyword.lower() in src:
                    hit = True
                    max_pri = max(max_pri, int(entry.get("priority", 0)))
                    break
            if hit:
                score += 1 + max_pri
        if score > 0:
            scores[lang] = score
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
        return ranked[0][0]
    return None


def resolve_source_language(row_lang: str, studio_lang: str, combined_text: str) -> str:
    rl = (row_lang or "").strip().lower()
    if rl in SUPPORTED_LANGUAGES:
        return rl
    sl = (studio_lang or "").strip().lower()
    if sl in SUPPORTED_LANGUAGES:
        return sl
    detected = detect_language(combined_text)
    if detected:
        return detected
    return sl if sl else "de"


def pick_primary_archetype(matches: list[ArchetypeMatch], studio_default: str) -> ArchetypeMatch:
    if matches:
        return matches[0]
    return ArchetypeMatch(
        archetype=studio_default or "milf_solo",
        tags=["MILF", "Solo"],
        risk="LOW",
        priority=0,
        matched_keyword="<default>",
        pack="",
    )


def style_entry_for(archetype: str) -> dict:
    arch = _style().get("archetypes", {})
    if archetype in arch:
        return arch[archetype]
    return arch.get("milf_solo", {})


def title_patterns_for(archetype: str) -> list[str]:
    entry = style_entry_for(archetype)
    return list(entry.get("title_patterns", []))


def description_templates_for(archetype: str) -> list[str]:
    entry = style_entry_for(archetype)
    return list(entry.get("description_templates", []))


def stable_pick(scene_code: str, archetype: str, slot: str, options: list[str]) -> str:
    if not options:
        return ""
    payload = f"{scene_code}|{archetype}|{slot}".encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    idx = int.from_bytes(digest[:4], "big") % len(options)
    return options[idx]


def pick_description(scene_code: str, archetype: str, geo: str) -> str:
    """Choose description template from hash(scene_code) for stable variants."""
    templates = description_templates_for(archetype)
    if not templates:
        return ""
    digest = hashlib.sha256(scene_code.encode("utf-8")).digest()
    idx = int.from_bytes(digest[:4], "big") % len(templates)
    raw = templates[idx]
    out = raw.replace("{geo}", geo or "").strip()
    out = re.sub(r"\s+", " ", out)
    return out


def pick_title(scene_code: str, archetype: str, geo: str) -> str:
    patterns = title_patterns_for(archetype)
    raw = stable_pick(scene_code, archetype, "title", patterns)
    out = raw.replace("{geo}", geo or "").strip()
    out = re.sub(r"\s+", " ", out)
    return out


def get_studio(name: Optional[str]) -> StudioConfig:
    studios = _studios().get("studios", {})
    if name and name in studios:
        s = studios[name]
        return StudioConfig(
            name=name,
            geo_identifier=str(s.get("geo_identifier", "")),
            source_language=str(s.get("source_language", "")),
            default_archetype=str(s.get("default_archetype", "milf_solo")),
            code_prefix=str(s.get("code_prefix", "")),
            voice_notes=str(s.get("voice_notes", "")),
        )
    d = _studios().get("default", {})
    return StudioConfig(
        name=name or "default",
        geo_identifier=str(d.get("geo_identifier", "")),
        source_language=str(d.get("source_language", "")),
        default_archetype=str(d.get("default_archetype", "milf_solo")),
        code_prefix=str(d.get("code_prefix", "")),
        voice_notes=str(d.get("voice_notes", "")),
    )


def detect_studio_from_code(code: str) -> Optional[str]:
    if not code:
        return None
    studios = _studios().get("studios", {})
    cu = code.upper().strip()
    best: tuple[int, str] | None = None
    for name, cfg in studios.items():
        prefix = str(cfg.get("code_prefix", "")).upper()
        if not prefix:
            continue
        token = prefix + "_"
        if cu.startswith(token) or cu.startswith(prefix):
            # Prefer longest matching prefix
            key = len(prefix)
            if best is None or key > best[0]:
                best = (key, name)
    return best[1] if best else None


def studio_from_row(scene_code: str, studio_cell: Optional[str]) -> StudioConfig:
    name = (studio_cell or "").strip() or None
    if not name:
        name = detect_studio_from_code(scene_code)
    return get_studio(name)


def word_count(s: str) -> int:
    return len([w for w in (s or "").split() if w])


def merge_tags_ordered(
    *,
    genre: list[str],
    archetype_style: list[str],
    match_tags: list[str],
    action: list[str],
    modifier: list[str],
    geo: str,
    limit: int = 6,
) -> list[str]:
    geo_tags = [geo] if geo and geo.strip() else []
    buckets = [genre, archetype_style, match_tags, action, modifier, geo_tags]
    seen: dict[str, None] = {}
    out: list[str] = []
    for bucket in buckets:
        for t in bucket:
            clean = str(t).strip()
            if not clean:
                continue
            key = clean.casefold()
            if key in seen:
                continue
            seen[key] = None
            out.append(clean)
            if len(out) >= limit:
                return out
    return out


def merge_risk(*levels: str) -> str:
    if any(x == "HIGH" for x in levels):
        return "HIGH"
    if any(x == "MED" for x in levels):
        return "MED"
    return "LOW"
