"""
Ingest ZIPs of approved cover images into structured title-focused seed examples.
"""
from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from amg.config import TRAINING_EXAMPLES_DIR
from amg.learning.training_registry import record_training_artifact

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class VodSeedIngestStats:
    zip_path: Path
    input_entries: int
    image_entries: int
    accepted_rows: int
    rejected_rows: int
    skipped_non_images: int
    output_dir: Optional[Path] = None
    accepted_path: Optional[Path] = None
    rejected_path: Optional[Path] = None
    summary_path: Optional[Path] = None
    appended_to_bank: int = 0


def ingest_vod_cover_seed_zip(
    *,
    zip_path: Path,
    dataset_name: str = "vod_cover_seed",
    min_quality: float = 1.8,
    append_to_bank: bool = True,
) -> VodSeedIngestStats:
    zp = Path(zip_path)
    if not zp.exists():
        raise FileNotFoundError(f"Zip not found: {zp}")

    accepted: List[dict] = []
    rejected: List[dict] = []
    input_entries = 0
    image_entries = 0
    skipped_non_images = 0
    now = datetime.now(timezone.utc).isoformat()

    with zipfile.ZipFile(zp) as zf:
        for name in zf.namelist():
            input_entries += 1
            if name.endswith("/"):
                skipped_non_images += 1
                continue
            ext = Path(name).suffix.lower()
            if ext not in _IMAGE_EXTS:
                skipped_non_images += 1
                continue
            image_entries += 1

            parsed = _parse_cover_filename(Path(name).name)
            if parsed is None:
                rejected.append(
                    {
                        "zip_member": name,
                        "reason_code": "parse_failed",
                        "reason": "could not infer usable title from filename",
                    }
                )
                continue

            score, reasons = _seed_quality_score(parsed)
            if score < float(min_quality):
                rejected.append(
                    {
                        "zip_member": name,
                        "reason_code": "below_quality_threshold",
                        "reason": f"quality {score:.2f} below threshold {min_quality:.2f}",
                        "quality_score": score,
                        "quality_reasons": reasons,
                        "parsed": parsed,
                    }
                )
                continue

            row = {
                "example_id": f"{_safe_name(dataset_name)}:{len(accepted)+1}",
                "scene_id": parsed["scene_id"],
                "source": {
                    "source_type": "vod_cover_seed_zip",
                    "source_file": str(zp),
                    "zip_member": name,
                    "captured_at_utc": now,
                    # Published covers that already cleared operator distribution.
                    "published_success_seed": True,
                    "seed_signal_class": "title_cover_success",
                },
                "scene_features": {
                    "studio": "VOD_COVER_SEED",
                    "scene_type": "STANDARD",
                    "genres": parsed["genres"],
                    "positions": [],
                    "performers": parsed["performers"],
                    "title_tone": None,
                    "rule_pack_id": None,
                    "insight_mood": None,
                    "insight_setting": None,
                },
                "approved_metadata": {
                    "title": parsed["title"],
                    "long_description": parsed["long_description"],
                    "tags": parsed["tags"],
                    "categories": parsed["categories"],
                },
                "quality": {
                    "curation_quality_score": round(score, 3),
                    "curation_quality_reasons": reasons,
                    "curation_status": "accepted",
                    # Not a review-edit metric; acts as retrieval prior only.
                    "metadata_acceptance_score": 0.9,
                },
            }
            accepted.append(row)

    out_dir = TRAINING_EXAMPLES_DIR / "vod_cover_seed"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_name(dataset_name)
    accepted_path = out_dir / f"{stem}_accepted.jsonl"
    rejected_path = out_dir / f"{stem}_rejected.jsonl"
    summary_path = out_dir / f"{stem}_summary.json"
    _write_jsonl(accepted_path, accepted)
    _write_jsonl(rejected_path, rejected)

    appended = 0
    if append_to_bank and accepted:
        appended = _append_to_approved_bank(accepted)

    summary = {
        "generated_at_utc": now,
        "zip_path": str(zp),
        "dataset_name": dataset_name,
        "input_entries": input_entries,
        "image_entries": image_entries,
        "accepted_rows": len(accepted),
        "rejected_rows": len(rejected),
        "skipped_non_images": skipped_non_images,
        "min_quality": min_quality,
        "appended_to_approved_bank": appended,
        "output": {
            "accepted_path": str(accepted_path),
            "rejected_path": str(rejected_path),
        },
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    record_training_artifact(
        "vod_cover_seed_ingest",
        accepted_path,
        metadata={
            "zip_path": str(zp),
            "dataset_name": dataset_name,
            "input_entries": input_entries,
            "image_entries": image_entries,
            "accepted_rows": len(accepted),
            "rejected_rows": len(rejected),
            "appended_to_approved_bank": appended,
            "summary_path": str(summary_path),
        },
    )
    return VodSeedIngestStats(
        zip_path=zp,
        input_entries=input_entries,
        image_entries=image_entries,
        accepted_rows=len(accepted),
        rejected_rows=len(rejected),
        skipped_non_images=skipped_non_images,
        output_dir=out_dir,
        accepted_path=accepted_path,
        rejected_path=rejected_path,
        summary_path=summary_path,
        appended_to_bank=appended,
    )


def _parse_cover_filename(filename: str) -> Optional[dict]:
    stem = Path(filename).stem
    stem = re.sub(r"\(\d+\)$", "", stem).strip()
    stem = re.sub(r"\s+copy$", "", stem, flags=re.I).strip()
    stem = stem.replace("  ", " ").strip()
    parts = [p.strip() for p in stem.split("_") if p.strip()]
    code = ""
    performer = ""
    title = ""
    if parts and re.fullmatch(r"\d{1,4}", parts[0]):
        code = parts[0]
        if len(parts) >= 2:
            performer = _normalize_person_name(parts[1])
        title_parts = parts[2:] if len(parts) > 2 else []
        title = _clean_title_tokens(title_parts)
    else:
        title = _clean_title_tokens(parts)

    if not title:
        # fallback from raw stem with camel-case split
        title = _normalize_free_title(stem)
    # Pattern like "040 Nicole Doshi 2 F" is a filename marker, not a title.
    if performer and re.fullmatch(r"\d{1,4}\s+.+\s+\d+\s+f", title.lower().strip()):
        title = ""
    if title and len(title) < 10:
        title = ""

    # Performer-only filename fallback (e.g., 024_Name_1_F.jpg)
    if not title and performer:
        title = f"{performer} Feature Cover"
    if not title:
        return None

    performers = [performer] if performer else _infer_performers_from_title(title)
    tags = _extract_tags(title)
    categories = _derive_categories_from_tags(tags)
    genres = _derive_genres_from_tags(tags)
    scene_id = _safe_name(f"{code}_{title[:40]}") if code else _safe_name(title[:50])
    long_description = (
        f"{title}. This is a proven operator-approved final-cover title pattern."
    )

    return {
        "scene_id": scene_id,
        "performers": performers,
        "title": title,
        "long_description": long_description,
        "tags": tags,
        "categories": categories,
        "genres": genres,
    }


def _normalize_person_name(raw: str) -> str:
    s = re.sub(r"\d+$", "", str(raw or "")).strip()
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    s = " ".join(s.split())
    return s.title()


def _clean_title_tokens(tokens: List[str]) -> str:
    if not tokens:
        return ""
    filtered: List[str] = []
    for tok in tokens:
        t = str(tok or "").strip()
        if not t:
            continue
        low = t.lower()
        if re.fullmatch(r"\d+", low):
            continue
        if low in {"f", "1_f", "2_f", "3_f", "4_f", "la2"}:
            continue
        filtered.append(t)
    if not filtered:
        return ""
    title = " ".join(filtered)
    title = title.replace("-", " ")
    title = re.sub(r"\s+", " ", title).strip()
    return title


def _normalize_free_title(stem: str) -> str:
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # Trim common numeric/file marker wrappers.
    s = re.sub(r"^\d{1,4}\s+", "", s)
    s = re.sub(r"\s+\d+\s+f$", "", s, flags=re.I)
    return s


def _infer_performers_from_title(title: str) -> List[str]:
    # lightweight heuristic: no reliable split, return empty.
    return []


def _extract_tags(text: str) -> List[str]:
    s = str(text or "").lower()
    rules = [
        ("pov", ["pov"]),
        ("anal", ["anal"]),
        ("blowjob", ["blowjob", "bj", "deep throat", "deepthroat"]),
        ("doggy style", ["doggy", "doggystyle"]),
        ("missionary", ["missionary"]),
        ("cowgirl", ["cowgirl"]),
        ("reverse cowgirl", ["reverse cowgirl"]),
        ("creampie", ["creampie"]),
        ("facial", ["facial"]),
        ("squirting", ["squirt", "squirting"]),
        ("threesome", ["threesome", "3 way", "4 way"]),
        ("milf", ["milf"]),
        ("teen", ["teen"]),
        ("petite", ["petite"]),
        ("asian", ["asian", "thai"]),
        ("amateur", ["amateur", "homemade"]),
        ("rough sex", ["rough"]),
    ]
    out: List[str] = []
    seen = set()
    for tag, keys in rules:
        if any(k in s for k in keys):
            if tag not in seen:
                seen.add(tag)
                out.append(tag)
    # add a few lexical anchors from title tokens
    tokens = [t for t in re.findall(r"[a-z0-9']+", s) if len(t) >= 4]
    for t in tokens:
        if t in {"with", "from", "style", "copy"}:
            continue
        if t not in seen and len(out) < 18:
            seen.add(t)
            out.append(t)
    return out[:24]


def _derive_categories_from_tags(tags: List[str]) -> List[str]:
    mapping = {
        "pov": "POV",
        "anal": "Anal",
        "blowjob": "Blowjob",
        "deepthroat": "Deepthroat",
        "doggy style": "Doggy Style",
        "missionary": "Missionary",
        "cowgirl": "Cowgirl",
        "reverse cowgirl": "Reverse Cowgirl",
        "creampie": "Creampie",
        "facial": "Facial",
        "squirting": "Squirting",
        "threesome": "Threesome",
        "milf": "MILF",
        "teen": "Teen",
        "petite": "Petite",
        "asian": "Asian",
        "amateur": "Amateur",
        "homemade": "Homemade",
    }
    out: List[str] = []
    seen = set()
    for t in tags:
        key = str(t or "").lower()
        if key in mapping:
            cat = mapping[key]
            if cat.lower() not in seen:
                seen.add(cat.lower())
                out.append(cat)
    if "hd porn" not in seen:
        out.append("HD Porn")
    return out[:15]


def _derive_genres_from_tags(tags: List[str]) -> List[str]:
    mapping = {
        "pov": "POV",
        "anal": "ANAL",
        "blowjob": "BLOWJOB",
        "deepthroat": "BLOWJOB",
        "creampie": "CREAMPIE",
        "facial": "FACIAL",
        "squirting": "SQUIRT",
        "threesome": "THREESOME",
        "milf": "MILF",
        "teen": "TEEN",
        "petite": "PETITE",
        "asian": "ASIAN",
        "amateur": "AMATEUR",
        "homemade": "AMATEUR",
    }
    out: List[str] = []
    seen = set()
    for t in tags:
        g = mapping.get(str(t or "").lower())
        if not g:
            continue
        if g in seen:
            continue
        seen.add(g)
        out.append(g)
    return out[:10]


def _seed_quality_score(parsed: dict) -> Tuple[float, List[str]]:
    title = str(parsed.get("title") or "")
    desc = str(parsed.get("long_description") or "")
    tags = parsed.get("tags") if isinstance(parsed.get("tags"), list) else []
    categories = parsed.get("categories") if isinstance(parsed.get("categories"), list) else []
    performers = parsed.get("performers") if isinstance(parsed.get("performers"), list) else []
    # Operator supplied these as published/distributed winners; use a high prior.
    score = 2.4
    reasons: List[str] = ["published_success_seed_prior"]
    if 20 <= len(title) <= 95:
        score += 1.2
        reasons.append("title_length_good")
    elif len(title) >= 12:
        score += 0.7
        reasons.append("title_length_ok")
    if len(desc) >= 80:
        score += 0.4
        reasons.append("description_present")
    if len(tags) >= 6:
        score += 0.8
        reasons.append("tags_rich")
    elif len(tags) >= 3:
        score += 0.4
        reasons.append("tags_ok")
    if len(categories) >= 4:
        score += 0.6
        reasons.append("categories_good")
    elif len(categories) >= 2:
        score += 0.4
        reasons.append("categories_ok")
    if performers:
        score += 0.3
        reasons.append("performer_present")
    return score, reasons


def _append_to_approved_bank(rows: List[dict]) -> int:
    if not rows:
        return 0
    bank_path = TRAINING_EXAMPLES_DIR / "approved_example_bank.jsonl"
    existing: List[dict] = []
    seen = set()
    if bank_path.exists():
        with open(bank_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    existing.append(row)
                    seen.add(_bank_key(row))
    appended = 0
    for row in rows:
        k = _bank_key(row)
        if k in seen:
            continue
        seen.add(k)
        existing.append(row)
        appended += 1
    if appended:
        _write_jsonl(bank_path, existing)
    return appended


def _bank_key(row: dict) -> str:
    md = row.get("approved_metadata") if isinstance(row.get("approved_metadata"), dict) else {}
    sf = row.get("scene_features") if isinstance(row.get("scene_features"), dict) else {}
    return (
        f"{str(sf.get('studio') or '').lower()}|"
        f"{str(md.get('title') or '').strip().lower()}|"
        f"{str(md.get('long_description') or '').strip().lower()}"
    )


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")


def _safe_name(name: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_." else "_" for c in (name or "vod_cover_seed"))
    return out[:120] or "vod_cover_seed"
