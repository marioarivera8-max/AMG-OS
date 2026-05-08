"""
Curate operator-provided text/docx metadata examples into accepted/rejected sets.
"""
from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree as ET

from amg.config import TRAINING_EXAMPLES_DIR
from amg.learning.training_registry import record_training_artifact


@dataclass
class DocCuratorStats:
    input_records: int = 0
    accepted_rows: int = 0
    rejected_rows: int = 0
    duplicate_rows: int = 0
    output_dir: Optional[Path] = None
    accepted_path: Optional[Path] = None
    rejected_path: Optional[Path] = None
    summary_path: Optional[Path] = None
    appended_to_bank: int = 0
    dry_run: bool = False


def curate_examples_document(
    *,
    input_path: Path,
    dataset_name: str = "word_doc_examples",
    min_quality: float = 3.0,
    dedupe_threshold: float = 0.86,
    append_to_bank: bool = False,
    dry_run: bool = False,
) -> DocCuratorStats:
    lines = _extract_lines(Path(input_path))
    raw_records = _records_from_lines(lines)
    stats = DocCuratorStats(input_records=len(raw_records), dry_run=bool(dry_run))

    accepted: List[dict] = []
    rejected: List[dict] = []
    accepted_index: List[Tuple[float, str]] = []
    now = datetime.now(timezone.utc).isoformat()
    for idx, record in enumerate(raw_records, start=1):
        normalized = _normalize_record(record)
        if normalized is None:
            rejected.append(
                {
                    "row_num": idx,
                    "reason_code": "missing_required_fields",
                    "reason": "record missing title/description/tags/categories",
                    "raw_record": record,
                }
            )
            continue

        quality_score, quality_reasons = _quality_score(normalized)
        if quality_score < float(min_quality):
            rejected.append(
                {
                    "row_num": idx,
                    "reason_code": "below_quality_threshold",
                    "reason": f"quality {quality_score:.2f} below threshold {min_quality:.2f}",
                    "quality_score": quality_score,
                    "quality_reasons": quality_reasons,
                    "normalized": normalized,
                }
            )
            continue

        dedupe_text = _dedupe_text(normalized)
        dup_idx, dup_score = _best_duplicate(dedupe_text, accepted_index, dedupe_threshold=float(dedupe_threshold))
        if dup_idx >= 0:
            stats.duplicate_rows += 1
            rejected.append(
                {
                    "row_num": idx,
                    "reason_code": "near_duplicate",
                    "reason": f"near duplicate of accepted row {dup_idx + 1} (similarity={dup_score:.3f})",
                    "quality_score": quality_score,
                    "quality_reasons": quality_reasons,
                    "normalized": normalized,
                }
            )
            continue

        accepted_index.append((quality_score, dedupe_text))
        accepted.append(
            {
                "example_id": f"{_safe_name(dataset_name)}:{idx}",
                "scene_id": normalized.get("scene_id"),
                "source": {
                    "source_type": "operator_word_doc",
                    "source_file": str(Path(input_path)),
                    "source_row_num": idx,
                    "captured_at_utc": now,
                    "source_url": normalized.get("source_url"),
                    "platform": normalized.get("platform"),
                    "posted_title": normalized.get("posted_title"),
                    "timestamp_hint": normalized.get("timestamp_hint"),
                },
                "scene_features": normalized.get("scene_features"),
                "approved_metadata": normalized.get("approved_metadata"),
                "quality": {
                    "curation_quality_score": round(quality_score, 3),
                    "curation_quality_reasons": quality_reasons,
                    "curation_status": "accepted",
                },
            }
        )

    stats.accepted_rows = len(accepted)
    stats.rejected_rows = len(rejected)
    if dry_run:
        return stats

    out_dir = TRAINING_EXAMPLES_DIR / "doc_curation"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_name(dataset_name)
    accepted_path = out_dir / f"{stem}_accepted.jsonl"
    rejected_path = out_dir / f"{stem}_rejected.jsonl"
    summary_path = out_dir / f"{stem}_summary.json"
    _write_jsonl(accepted_path, accepted)
    _write_jsonl(rejected_path, rejected)

    appended = 0
    if append_to_bank:
        appended = _append_to_approved_bank(accepted)

    summary = {
        "generated_at_utc": now,
        "input_path": str(Path(input_path)),
        "dataset_name": dataset_name,
        "input_records": stats.input_records,
        "accepted_rows": stats.accepted_rows,
        "rejected_rows": stats.rejected_rows,
        "duplicate_rows": stats.duplicate_rows,
        "min_quality": min_quality,
        "dedupe_threshold": dedupe_threshold,
        "appended_to_approved_bank": appended,
        "output": {
            "accepted_path": str(accepted_path),
            "rejected_path": str(rejected_path),
        },
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    record_training_artifact(
        "doc_example_curation",
        accepted_path,
        metadata={
            "dataset_name": dataset_name,
            "input_records": stats.input_records,
            "accepted_rows": stats.accepted_rows,
            "rejected_rows": stats.rejected_rows,
            "duplicate_rows": stats.duplicate_rows,
            "appended_to_approved_bank": appended,
            "summary_path": str(summary_path),
        },
    )
    stats.output_dir = out_dir
    stats.accepted_path = accepted_path
    stats.rejected_path = rejected_path
    stats.summary_path = summary_path
    stats.appended_to_bank = appended
    return stats


def _append_to_approved_bank(rows: List[dict]) -> int:
    if not rows:
        return 0
    bank_path = TRAINING_EXAMPLES_DIR / "approved_example_bank.jsonl"
    existing: List[dict] = []
    seen_keys: set[str] = set()
    if bank_path.exists():
        with open(bank_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    existing.append(obj)
                    seen_keys.add(_bank_key(obj))
    appended = 0
    for row in rows:
        k = _bank_key(row)
        if k in seen_keys:
            continue
        seen_keys.add(k)
        existing.append(row)
        appended += 1
    if appended > 0:
        _write_jsonl(bank_path, existing)
    return appended


def _bank_key(row: dict) -> str:
    md = row.get("approved_metadata") if isinstance(row.get("approved_metadata"), dict) else {}
    title = str(md.get("title") or "").strip().lower()
    desc = str(md.get("long_description") or "").strip().lower()
    studio = str(((row.get("scene_features") or {}).get("studio")) or "").strip().lower()
    return f"{studio}|{title}|{desc}"


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")


def _extract_lines(path: Path) -> List[str]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input not found: {p}")
    ext = p.suffix.lower()
    if ext == ".docx":
        return _read_docx_lines(p)
    if ext in {".txt", ".md"}:
        return p.read_text(encoding="utf-8", errors="ignore").splitlines()
    raise ValueError(f"Unsupported input extension: {ext} (expected .docx/.txt/.md)")


def _read_docx_lines(path: Path) -> List[str]:
    with zipfile.ZipFile(path) as zf:
        try:
            raw = zf.read("word/document.xml")
        except KeyError as e:
            raise ValueError("Invalid .docx: missing word/document.xml") from e
    root = ET.fromstring(raw)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    lines: List[str] = []
    for p in root.findall(".//w:p", ns):
        texts = [t.text or "" for t in p.findall(".//w:t", ns)]
        line = "".join(texts).strip()
        lines.append(line)
    return lines


def _records_from_lines(lines: List[str]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    last_key: Optional[str] = None

    for raw in lines + [""]:
        line = str(raw or "").strip()
        if not line:
            if current:
                records.append(current)
                current = {}
                last_key = None
            continue
        if line.lower().startswith(("http://", "https://")):
            if current:
                records.append(current)
                current = {}
            _ingest_url_line(current, line)
            last_key = "source_url"
            continue
        if re.match(r"^(example|record)\s+\d+\b", line.lower()) and current:
            records.append(current)
            current = {}
            last_key = None
            continue
        m = re.match(r"^([A-Za-z0-9 _/()'&-]{2,40})\s*:\s*(.*)$", line)
        if m:
            key = _canonical_key(m.group(1))
            value = m.group(2).strip()
            key = _alias_key(key)
            if key in current and value:
                # Keep additional value lines for repeated keys.
                prev = str(current.get(key) or "").strip()
                current[key] = f"{prev}; {value}" if prev else value
            else:
                current[key] = value
            last_key = key
            continue

        if line.lower().startswith("tags:"):
            tags_value = line.split(":", 1)[1].strip() if ":" in line else ""
            if tags_value:
                current["winning_tags"] = tags_value
                last_key = "winning_tags"
                continue
        if _looks_like_tag_blob(line) and not current.get("winning_tags"):
            current["winning_tags"] = line
            last_key = "winning_tags"
            continue
        if _looks_like_video_meta_line(line):
            continue
        if not current.get("winning_title") and len(line) <= 130:
            current["winning_title"] = line
            last_key = "winning_title"
            continue
        if not current.get("winning_long_description"):
            current["winning_long_description"] = line
            last_key = "winning_long_description"
            continue

        # Continuation line for the last parsed key.
        if last_key:
            prev = str(current.get(last_key) or "").strip()
            current[last_key] = f"{prev} {line}".strip()

    return [r for r in records if r]


def _normalize_record(row: Dict[str, Any]) -> Optional[dict]:
    title = _first(row, "winning_title", "title", "approved_title", "posted_title")
    desc = _first(row, "winning_long_description", "long_description", "description", "approved_description")
    raw_tags = _first(row, "winning_tags", "tags")
    raw_categories = _first(row, "winning_categories", "categories")
    tags = _normalize_list(raw_tags)
    if _needs_tag_repair(tags):
        repaired = _extract_keyword_tags(" ".join([str(raw_tags or ""), str(title or ""), str(desc or "")]))
        if repaired:
            tags = repaired
    categories = _normalize_list(raw_categories)
    if not categories:
        categories = _derive_categories_from_tags(tags)
    if not title or not desc or not tags or not categories:
        return None

    studio = _first(row, "studio")
    scene_type = _first(row, "scene_type", "type") or "STANDARD"
    genres = _normalize_list(_first(row, "genres"))
    performers = _normalize_list(_first(row, "performers", "cast"))
    why = _first(row, "why_it_worked", "reason", "notes")
    scene_id = _first(row, "scene_id", "scene") or _safe_name(f"{studio or 'scene'}_{title[:30]}")
    source_url = _first(row, "source_url", "source_link", "video_url", "url")
    platform = _infer_platform(source_url)
    posted_title = _first(row, "posted_title")
    timestamp_hint = _first(row, "timestamp_hint", "source_clip_timestamp")

    return {
        "scene_id": scene_id,
        "source_url": source_url,
        "platform": platform,
        "posted_title": posted_title,
        "timestamp_hint": timestamp_hint,
        "scene_features": {
            "studio": studio or None,
            "scene_type": scene_type.upper().replace(" ", "_"),
            "genres": [g.upper().replace(" ", "_") for g in genres],
            "positions": [],
            "performers": performers,
            "title_tone": None,
            "rule_pack_id": None,
            "insight_mood": None,
            "insight_setting": None,
        },
        "approved_metadata": {
            "title": _clean_spaces(title),
            "long_description": _clean_spaces(desc),
            "tags": [t.lower() for t in tags],
            "categories": [c.title() for c in categories],
        },
        "why_it_worked": _clean_spaces(why or ""),
    }


def _quality_score(row: dict) -> Tuple[float, List[str]]:
    md = row.get("approved_metadata") if isinstance(row.get("approved_metadata"), dict) else {}
    sf = row.get("scene_features") if isinstance(row.get("scene_features"), dict) else {}
    title = str(md.get("title") or "")
    desc = str(md.get("long_description") or "")
    tags = md.get("tags") if isinstance(md.get("tags"), list) else []
    cats = md.get("categories") if isinstance(md.get("categories"), list) else []
    score = 0.0
    reasons: List[str] = []

    if 28 <= len(title) <= 90:
        score += 1.2
        reasons.append("title_length_good")
    elif len(title) >= 18:
        score += 0.6
        reasons.append("title_length_ok")

    if len(desc) >= 170:
        score += 1.3
        reasons.append("description_depth_good")
    elif len(desc) >= 120:
        score += 0.8
        reasons.append("description_depth_ok")

    if len(tags) >= 15:
        score += 1.0
        reasons.append("tag_count_good")
    elif len(tags) >= 8:
        score += 0.6
        reasons.append("tag_count_ok")

    if len(cats) >= 8:
        score += 0.9
        reasons.append("category_count_good")
    elif len(cats) >= 4:
        score += 0.5
        reasons.append("category_count_ok")

    if sf.get("studio") and (sf.get("genres") or sf.get("performers")):
        score += 0.6
        reasons.append("scene_context_present")

    # Penalize generic titles.
    generic = {"hot", "sexy", "naughty", "wild", "amazing"}
    words = {w.lower() for w in re.findall(r"[A-Za-z0-9']+", title)}
    overlap = len(words & generic)
    if overlap >= 2:
        score -= 0.7
        reasons.append("generic_title_penalty")
    elif overlap == 1:
        score -= 0.3
        reasons.append("minor_generic_title_penalty")

    return max(0.0, min(5.0, score)), reasons


def _dedupe_text(row: dict) -> str:
    md = row.get("approved_metadata") if isinstance(row.get("approved_metadata"), dict) else {}
    return f"{md.get('title','')} :: {md.get('long_description','')}".strip().lower()


def _best_duplicate(candidate: str, accepted: List[Tuple[float, str]], dedupe_threshold: float) -> Tuple[int, float]:
    best_idx = -1
    best = 0.0
    for idx, (_score, text) in enumerate(accepted):
        sim = SequenceMatcher(a=candidate, b=text).ratio()
        if sim > best:
            best = sim
            best_idx = idx
    if best >= dedupe_threshold:
        return best_idx, best
    return -1, best


def _first(row: Dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        s = str(value).strip()
        if s:
            return s
    return None


def _normalize_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    out: List[str] = []
    seen = set()
    for token in re.split(r"[,;\n|]", str(raw)):
        t = _clean_spaces(token).strip()
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _needs_tag_repair(tags: List[str]) -> bool:
    if not tags:
        return True
    if len(tags) == 1 and len(tags[0]) > 24 and " " not in tags[0]:
        return True
    return False


def _extract_keyword_tags(text: str) -> List[str]:
    s = str(text or "").lower()
    keywords = [
        "anal", "pov", "blowjob", "deepthroat", "doggy style", "doggystyle", "missionary",
        "cowgirl", "reverse cowgirl", "creampie", "facial", "squirting", "asian", "teen",
        "milf", "petite", "big tits", "small tits", "amateur", "homemade", "threesome",
        "step sister", "stepbrother", "stepdad", "stepdaughter", "rimming", "cum in mouth",
        "tattoo", "latina", "brunette", "blonde",
    ]
    found: List[str] = []
    seen = set()
    compact = s.replace(" ", "")
    for kw in keywords:
        key = kw.lower()
        key_compact = key.replace(" ", "")
        if key in s or key_compact in compact:
            norm = key.replace("doggystyle", "doggy style")
            if norm not in seen:
                seen.add(norm)
                found.append(norm)
    return found[:30]


def _derive_categories_from_tags(tags: List[str]) -> List[str]:
    mapping = {
        "pov": "POV",
        "blowjob": "Blowjob",
        "deepthroat": "Deepthroat",
        "anal": "Anal",
        "creampie": "Creampie",
        "facial": "Facial",
        "squirting": "Squirting",
        "threesome": "Threesome",
        "amateur": "Amateur",
        "homemade": "Homemade",
        "milf": "MILF",
        "teen": "Teen",
        "petite": "Petite",
        "asian": "Asian",
    }
    cats: List[str] = []
    seen = set()
    for tag in tags:
        t = str(tag or "").lower()
        for key, cat in mapping.items():
            if key in t and cat.lower() not in seen:
                seen.add(cat.lower())
                cats.append(cat)
    if "HD Porn".lower() not in seen:
        cats.append("HD Porn")
    return cats[:15]


def _canonical_key(key: str) -> str:
    k = str(key or "").strip().lower()
    k = k.replace("&", " and ")
    k = re.sub(r"[^a-z0-9]+", "_", k)
    k = re.sub(r"_+", "_", k)
    return k.strip("_")


def _alias_key(key: str) -> str:
    aliases = {
        "winning_title": "winning_title",
        "title": "title",
        "approved_title": "approved_title",
        "winning_description": "winning_long_description",
        "winning_long_description": "winning_long_description",
        "long_description": "long_description",
        "description": "description",
        "approved_description": "approved_description",
        "winning_tags": "winning_tags",
        "tags": "tags",
        "winning_categories": "winning_categories",
        "categories": "categories",
        "studio": "studio",
        "scene_type": "scene_type",
        "type": "type",
        "genres": "genres",
        "performers": "performers",
        "cast": "cast",
        "why_it_worked": "why_it_worked",
        "reason": "reason",
        "notes": "notes",
        "scene_id": "scene_id",
        "scene": "scene",
        "source_url": "source_url",
        "source_link": "source_url",
        "video_link": "source_url",
        "video_url": "source_url",
        "url": "source_url",
        "platform": "platform",
        "posted_title": "posted_title",
        "source_clip_timestamp": "timestamp_hint",
        "timestamp_hint": "timestamp_hint",
    }
    return aliases.get(key, key)


def _clean_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _safe_name(name: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_." else "_" for c in (name or "doc_examples"))
    return out[:100] or "doc_examples"


def _ingest_url_line(record: Dict[str, Any], line: str) -> None:
    parts = [p.strip() for p in line.split(" - ") if p.strip()]
    if not parts:
        return
    url = parts[0]
    record["source_url"] = url
    # Heuristic mapping:
    # [url, title, description] or [url, title]
    if len(parts) >= 2 and not record.get("winning_title"):
        record["winning_title"] = parts[1]
    if len(parts) >= 3 and not record.get("winning_long_description"):
        record["winning_long_description"] = " - ".join(parts[2:])


def _looks_like_video_meta_line(line: str) -> bool:
    s = line.lower()
    if re.search(r"\b\d+\s*min\b", s) and re.search(r"\b\d{3,4}p\b", s):
        return True
    if re.search(r"\b\d{1,3}(,\d{3})+\b", s):
        return True
    return False


def _looks_like_tag_blob(line: str) -> bool:
    if ":" in line:
        return False
    s = line.strip().lower()
    if not s:
        return False
    marker_words = {"pov", "blowjob", "doggystyle", "doggy", "cowgirl", "creampie", "teen", "milf", "anal"}
    return any(m in s for m in marker_words) and len(s) >= 20


def _infer_platform(source_url: Optional[str]) -> Optional[str]:
    url = str(source_url or "").lower()
    if "xnxx.com" in url:
        return "XNXX"
    if "adultempire" in url:
        return "ADE"
    if "aebn" in url:
        return "AEBN"
    if "sexlikereal" in url:
        return "SLR"
    return None
