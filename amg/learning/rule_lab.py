"""
Rule Lab research + candidate generation.

This module mines local reviewed/feedback corpora to produce candidate metadata
rule packs without needing downstream revenue or platform analytics.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from amg.config import (
    DECISION_LOGS_DIR,
    REVIEWED_DIR,
    TRAINING_RULE_RUNS_DIR,
)
from amg.learning.rule_packs import save_rule_pack
from amg.learning.training_registry import record_training_artifact


# Keep a local avoid-list to prevent importing the heavy scoring package tree
# during lightweight rule-lab CLI operations.
DEFAULT_MARKET_TERMS_TO_AVOID = [
    "teen",
    "illegal",
    "incest",
    "rape",
    "forced",
]


@dataclass
class RuleResearchResult:
    rows_reviewed: int
    rows_with_titles: int
    rows_with_description: int
    suggested_constraints: Dict[str, Any]
    by_studio: Dict[str, int]


@dataclass
class RuleCandidateResult:
    run_id: str
    rule_pack_id: str
    rule_pack_path: Path
    research_manifest_path: Path
    research_result: RuleResearchResult


def run_rule_research(
    *,
    days_back: int = 60,
    studio: Optional[str] = None,
    min_rows: int = 10,
) -> RuleResearchResult:
    reviewed_rows = _load_reviewed_rows(days_back=days_back)
    title_prefix = Counter()
    title_suffix = Counter()
    title_tokens = Counter()
    description_phrases = Counter()
    tag_counts = Counter()
    category_counts = Counter()
    style_counts = Counter()
    by_studio = Counter()
    n_titles = 0
    n_desc = 0

    for row in reviewed_rows:
        sid = str(row.get("scene_id") or "").strip()
        if not sid:
            continue
        dlog = _load_decision_log(sid)
        row_studio = str(((dlog or {}).get("input") or {}).get("studio") or "").strip()
        if studio and row_studio.lower() != studio.lower():
            continue
        if row_studio:
            by_studio[row_studio] += 1

        title = str(row.get("title_override") or "").strip()
        if title:
            n_titles += 1
            words = _title_words(title)
            if words:
                for w in words:
                    title_tokens[w] += 1
                if len(words) >= 2:
                    title_prefix[" ".join(words[:2])] += 1
                    title_suffix[" ".join(words[-2:])] += 1
            tone = str(row.get("title_tone") or "").strip().lower()
            if tone:
                style_counts[tone] += 1

        desc = str(row.get("long_description") or "").strip()
        if desc:
            n_desc += 1
            for phrase in _description_phrases(desc):
                description_phrases[phrase] += 1

        for tag in _parse_csv_tokens(row.get("tags_csv"), lowercase=True):
            tag_counts[tag] += 1
        for cat in _parse_csv_tokens(row.get("categories_csv"), lowercase=False, title_case=True):
            category_counts[cat] += 1

    rows_count = sum(by_studio.values()) if by_studio else len(reviewed_rows)
    constraints = _build_constraints(
        title_prefix=title_prefix,
        title_suffix=title_suffix,
        title_tokens=title_tokens,
        description_phrases=description_phrases,
        tag_counts=tag_counts,
        category_counts=category_counts,
        style_counts=style_counts,
        min_rows=min_rows,
        rows_count=rows_count,
    )
    return RuleResearchResult(
        rows_reviewed=rows_count,
        rows_with_titles=n_titles,
        rows_with_description=n_desc,
        suggested_constraints=constraints,
        by_studio=dict(by_studio),
    )


def generate_candidate_rule_pack(
    *,
    rule_pack_id: str,
    description: str = "",
    days_back: int = 60,
    studio: Optional[str] = None,
    min_rows: int = 10,
) -> RuleCandidateResult:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = TRAINING_RULE_RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    research = run_rule_research(days_back=days_back, studio=studio, min_rows=min_rows)
    constraints = research.suggested_constraints
    rule_pack_path = save_rule_pack(
        rule_pack_id=rule_pack_id,
        constraints=constraints,
        description=description or f"Rule lab candidate from last {days_back} days",
        source="rule_lab_candidate",
        metrics={
            "rows_reviewed": research.rows_reviewed,
            "rows_with_titles": research.rows_with_titles,
            "rows_with_description": research.rows_with_description,
        },
    )
    manifest_path = run_dir / "research_manifest.json"
    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "rule_pack_id": rule_pack_id,
        "rule_pack_path": str(rule_pack_path),
        "input": {
            "days_back": int(days_back),
            "studio": studio,
            "min_rows": int(min_rows),
        },
        "research": {
            "rows_reviewed": research.rows_reviewed,
            "rows_with_titles": research.rows_with_titles,
            "rows_with_description": research.rows_with_description,
            "by_studio": research.by_studio,
        },
        "constraints": constraints,
    }
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    record_training_artifact(
        "rule_lab_candidate_manifest",
        manifest_path,
        metadata={
            "run_id": run_id,
            "rule_pack_id": rule_pack_id,
            "rows_reviewed": research.rows_reviewed,
        },
    )
    return RuleCandidateResult(
        run_id=run_id,
        rule_pack_id=rule_pack_id,
        rule_pack_path=rule_pack_path,
        research_manifest_path=manifest_path,
        research_result=research,
    )


def _load_reviewed_rows(*, days_back: int) -> List[dict]:
    if not REVIEWED_DIR.exists():
        return []
    out: List[dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days_back)))
    for p in REVIEWED_DIR.glob("*.json"):
        try:
            with open(p) as f:
                row = json.load(f)
        except Exception:
            continue
        ts_raw = str((row or {}).get("timestamp") or "")
        ts = _parse_ts(ts_raw)
        if ts and ts < cutoff:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _load_decision_log(scene_id: str) -> Optional[dict]:
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in str(scene_id))[:120]
    p = DECISION_LOGS_DIR / f"{safe}.json"
    if not p.exists():
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def _parse_ts(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _title_words(title: str) -> List[str]:
    words = re.findall(r"[A-Za-z0-9']+", str(title or "").lower())
    return [w for w in words if len(w) >= 3]


def _description_phrases(desc: str) -> List[str]:
    parts = [x.strip() for x in re.split(r"[.!?]", str(desc or "")) if x.strip()]
    out: List[str] = []
    for phrase in parts[:3]:
        cleaned = re.sub(r"\s+", " ", phrase).strip().lower()
        if 12 <= len(cleaned) <= 72:
            out.append(cleaned)
    return out


def _parse_csv_tokens(value: Any, *, lowercase: bool = False, title_case: bool = False) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw = [str(x).strip() for x in value]
    else:
        raw = [x.strip() for x in re.split(r"[,;\n|]", str(value))]
    out: List[str] = []
    seen = set()
    for token in raw:
        t = " ".join(token.split())
        if not t:
            continue
        if lowercase:
            t = t.lower()
        if title_case:
            t = " ".join(part.capitalize() for part in t.split())
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def _build_constraints(
    *,
    title_prefix: Counter,
    title_suffix: Counter,
    title_tokens: Counter,
    description_phrases: Counter,
    tag_counts: Counter,
    category_counts: Counter,
    style_counts: Counter,
    min_rows: int,
    rows_count: int,
) -> Dict[str, Any]:
    # Conservative defaults when corpus is small.
    if rows_count < max(1, int(min_rows)):
        return {
            "banned_title_terms": sorted({str(t).lower() for t in DEFAULT_MARKET_TERMS_TO_AVOID}),
            "title_prefixes": [],
            "title_suffixes": [],
            "required_title_tokens": [],
            "preferred_title_styles": [],
            "description_phrase_boost": [],
            "category_boost": [],
            "tag_boost": [],
            "description_min_chars": 160,
            "description_max_chars": 420,
            "retrieval_stage": "titles",
            "retrieval_top_k": 3,
        }

    banned = {str(t).lower() for t in DEFAULT_MARKET_TERMS_TO_AVOID}
    # Candidate banned terms: very rare/title-noise tokens
    for token, count in title_tokens.items():
        if count <= 1 and len(token) >= 8 and token.endswith("ly"):
            banned.add(token)

    return {
        "banned_title_terms": sorted(banned),
        "title_prefixes": [k for k, _ in title_prefix.most_common(3)],
        "title_suffixes": [k for k, _ in title_suffix.most_common(3)],
        "required_title_tokens": [k for k, _ in title_tokens.most_common(8) if k not in {"scene", "with"}][:3],
        "preferred_title_styles": [k for k, _ in style_counts.most_common(3)],
        "description_phrase_boost": [k for k, _ in description_phrases.most_common(4)],
        "category_boost": [k for k, _ in category_counts.most_common(10)],
        "tag_boost": [k for k, _ in tag_counts.most_common(20)],
        "description_min_chars": 160,
        "description_max_chars": 420,
        "retrieval_stage": "titles",
        "retrieval_top_k": 3,
    }
