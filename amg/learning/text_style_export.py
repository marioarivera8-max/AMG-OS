"""
Export full canonical dataset into text-training JSONL examples.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from amg.config import TRAINING_DATASETS_DIR, TRAINING_TEXT_DIR
from amg.learning.training_registry import record_training_artifact
from amg.prompts.loader import load_prompt_registry


_TRAINING_MESSAGES_SYSTEM_PROMPT = load_prompt_registry("training_messages_system")
_TRAINING_INSTRUCTION_PROMPT = load_prompt_registry("training_instruction")
_TRAINING_USER_CONTEXT_PROMPT = load_prompt_registry("training_user_context")


@dataclass
class TextExportStats:
    input_rows: int
    exported_rows: int
    skipped_rows: int
    output_path: Path


@dataclass
class TextBundleStats:
    dataset_name: str
    format_type: str
    outputs: Dict[str, str]
    totals: Dict[str, int]
    manifest_path: Path


def export_text_training_dataset(
    dataset_name: str = "scoring_selection_v1",
    split: str = "all",
    output_name: Optional[str] = None,
    format_type: str = "instruction",
) -> TextExportStats:
    safe_dataset = _safe_name(dataset_name)
    safe_split = _safe_name(split)
    fmt = _normalize_format(format_type)

    src_path = TRAINING_DATASETS_DIR / safe_dataset / f"{safe_split}.jsonl"
    if not src_path.exists():
        raise FileNotFoundError(f"Dataset split not found: {src_path}")

    TRAINING_TEXT_DIR.mkdir(parents=True, exist_ok=True)
    out_name = output_name or f"{safe_dataset}_{safe_split}_{fmt}_text_training.jsonl"
    out_path = TRAINING_TEXT_DIR / _safe_name(out_name)
    if out_path.suffix != ".jsonl":
        out_path = out_path.with_suffix(".jsonl")

    input_rows = 0
    exported_rows = 0
    skipped_rows = 0

    with open(src_path) as src, open(out_path, "w") as dst:
        for line in src:
            line = line.strip()
            if not line:
                continue
            input_rows += 1
            try:
                row = json.loads(line)
            except Exception:
                skipped_rows += 1
                continue

            ex = _to_text_example(row, format_type=fmt)
            if ex is None:
                skipped_rows += 1
                continue
            dst.write(json.dumps(ex) + "\n")
            exported_rows += 1

    stats = TextExportStats(
        input_rows=input_rows,
        exported_rows=exported_rows,
        skipped_rows=skipped_rows,
        output_path=out_path,
    )
    _record_single_export(
        dataset_name=dataset_name,
        split=split,
        format_type=fmt,
        stats=stats,
    )
    return stats


def _record_single_export(
    *,
    dataset_name: str,
    split: str,
    format_type: str,
    stats: TextExportStats,
) -> None:
    record_training_artifact(
        "text_export",
        stats.output_path,
        metadata={
            "dataset_name": dataset_name,
            "split": split,
            "format_type": format_type,
            "input_rows": stats.input_rows,
            "exported_rows": stats.exported_rows,
            "skipped_rows": stats.skipped_rows,
        },
    )


def export_text_training_bundle(
    dataset_name: str = "scoring_selection_v1",
    splits: Sequence[str] = ("train", "val", "test", "all"),
    format_type: str = "instruction",
) -> TextBundleStats:
    fmt = _normalize_format(format_type)
    outputs: Dict[str, str] = {}
    totals: Dict[str, int] = {"input_rows": 0, "exported_rows": 0, "skipped_rows": 0}

    for split in splits:
        stats = export_text_training_dataset(
            dataset_name=dataset_name,
            split=split,
            format_type=fmt,
        )
        outputs[split] = str(stats.output_path)
        totals["input_rows"] += stats.input_rows
        totals["exported_rows"] += stats.exported_rows
        totals["skipped_rows"] += stats.skipped_rows

    TRAINING_TEXT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = TRAINING_TEXT_DIR / f"{_safe_name(dataset_name)}_{fmt}_bundle_manifest.json"
    payload: Dict[str, object] = {
        "dataset_name": dataset_name,
        "format_type": fmt,
        "splits": list(splits),
        "outputs": outputs,
        "totals": totals,
    }
    with open(manifest_path, "w") as f:
        json.dump(payload, f, indent=2)
    record_training_artifact(
        "text_export_bundle",
        manifest_path,
        metadata=payload,
    )

    return TextBundleStats(
        dataset_name=dataset_name,
        format_type=fmt,
        outputs=outputs,
        totals=totals,
        manifest_path=manifest_path,
    )


def _to_text_example(
    row: Dict[str, object], format_type: str
) -> Optional[Dict[str, object]]:
    title = _clean(row.get("title"))
    description = _clean(row.get("description"))

    raw_label = row.get("label")
    label: Dict[str, object] = raw_label if isinstance(raw_label, dict) else {}
    categories = _clean_list(label.get("categories")) or []
    tags = _clean_list(label.get("tags")) or []

    if not title and not description and not categories and not tags:
        return None

    studio = _clean(row.get("studio"))
    performers = _clean_list(row.get("performers")) or []
    scene_id = _clean(row.get("scene_id"))
    filename = _clean(row.get("filename"))
    notes = _clean(row.get("notes"))
    source_sheet = _clean(row.get("source_sheet"))
    decision = _clean((label or {}).get("decision"))

    context = {
        "studio": studio,
        "performers": performers,
        "scene_id": scene_id,
        "filename": filename,
        "source_sheet": source_sheet,
        "operator_notes": notes,
        "decision": decision,
    }
    # Keep context compact.
    context = {k: v for k, v in context.items() if v not in (None, "", [], {})}

    target = {
        "title": title or None,
        "description": description or None,
        "categories": categories,
        "tags": tags,
    }
    base_meta = {
        "source_type": row.get("source_type"),
        "source_file": row.get("source_file"),
        "sample_id": row.get("sample_id"),
    }

    if format_type == "messages":
        user_prompt = _build_user_prompt(context)
        assistant_payload = {
            "title": target["title"],
            "description": target["description"],
            "categories": target["categories"],
            "tags": target["tags"],
        }
        return {
            "messages": [
                {
                    "role": "system",
                    "content": _TRAINING_MESSAGES_SYSTEM_PROMPT,
                },
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": json.dumps(assistant_payload, ensure_ascii=True)},
            ],
            "meta": base_meta,
        }

    return {
        "task": "title_description_tags",
        "instruction": _TRAINING_INSTRUCTION_PROMPT,
        "context": context,
        "target": target,
        "meta": base_meta,
    }


def _build_user_prompt(context: Mapping[str, object]) -> str:
    compact = json.dumps(dict(context), ensure_ascii=True)
    return _TRAINING_USER_CONTEXT_PROMPT.format(compact=compact)


def _normalize_format(format_type: str) -> str:
    ft = (format_type or "instruction").strip().lower()
    if ft not in {"instruction", "messages"}:
        raise ValueError(f"Unsupported format_type: {format_type}")
    return ft


def _clean(v: object) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _clean_list(v: object) -> Optional[List[str]]:
    if v is None:
        return None
    if isinstance(v, list):
        out = [str(x).strip() for x in v if str(x).strip()]
        # De-dup while preserving order.
        return list(dict.fromkeys(out))
    s = str(v).strip()
    if not s:
        return None
    items = [x.strip() for x in s.split(",") if x.strip()]
    return list(dict.fromkeys(items))


def _safe_name(s: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_." else "_" for c in (s or "dataset"))
    return out[:120] or "dataset"
