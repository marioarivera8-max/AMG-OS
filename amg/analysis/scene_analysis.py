"""Canonical scene-analysis sidecar.

This module turns AMG's existing scan, scoring, output, and review-warning
signals into a stable JSON sidecar inspired by the useful parts of the NOXO
API shape. It does not call NOXO or any external service.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from amg.utils.logging import get_logger
from amg.video.reader import VideoReader

log = get_logger("analysis.scene_analysis")

SCHEMA_VERSION = "1.0"
SIDE_CAR_NAME = "scene_analysis.json"
_SECTION_MERGE_GAP_SEC = 75.0


def write_scene_analysis(
    *,
    work_dir: Path,
    scene_id: str,
    video_path: Path,
    metadata: Optional[dict],
    phase_results: Optional[dict],
    all_scored: Optional[List[dict]],
    candidates: Optional[List[dict]],
    saved_covers: Optional[List[dict]],
    insight: Optional[dict] = None,
    ocr_results: Optional[List[dict]] = None,
    policy_flags: Optional[List[dict]] = None,
    preview_outputs: Optional[List[dict]] = None,
) -> Tuple[Optional[Path], Dict[str, Any]]:
    """Build and persist ``scene_analysis.json``.

    Returns ``(path, summary)``. On write failure, returns ``(None, summary)`` so
    the pipeline can continue and surface the problem as a warning.
    """
    analysis = build_scene_analysis(
        scene_id=scene_id,
        video_path=video_path,
        metadata=metadata,
        phase_results=phase_results,
        all_scored=all_scored,
        candidates=candidates,
        saved_covers=saved_covers,
        insight=insight,
        ocr_results=ocr_results,
        policy_flags=policy_flags,
        preview_outputs=preview_outputs,
    )
    summary = build_analysis_summary(analysis)
    out = Path(work_dir) / SIDE_CAR_NAME
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2, default=str)
        return out, summary
    except Exception as exc:  # noqa: BLE001 - sidecar failure is non-fatal
        log.warn("Failed to write scene analysis sidecar", path=str(out), error=str(exc))
        return None, summary


def build_scene_analysis(
    *,
    scene_id: str,
    video_path: Path,
    metadata: Optional[dict],
    phase_results: Optional[dict],
    all_scored: Optional[List[dict]],
    candidates: Optional[List[dict]],
    saved_covers: Optional[List[dict]],
    insight: Optional[dict] = None,
    ocr_results: Optional[List[dict]] = None,
    policy_flags: Optional[List[dict]] = None,
    preview_outputs: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    metadata = metadata if isinstance(metadata, dict) else {}
    phase_results = phase_results if isinstance(phase_results, dict) else {}
    saved_covers = list(saved_covers or [])
    entries = _unique_entries(list(all_scored or []) + list(candidates or []))

    evidence: List[Dict[str, Any]] = []
    thumbnail_moments = _thumbnail_moments(saved_covers, evidence)
    content_policy_flags = _policy_flags_from_content(phase_results, evidence)
    validation_policy_flags = _policy_flags_from_cover_validation(entries, evidence)
    extra_policy_flags = _normalize_policy_flags(policy_flags or [])
    all_policy_flags = content_policy_flags + validation_policy_flags + extra_policy_flags
    activity_curve = _activity_curve(entries)
    sections = _semantic_sections(entries, evidence)
    tags = _aggregate_tags(entries, saved_covers)

    analysis = {
        "schema_version": SCHEMA_VERSION,
        "scene_id": scene_id,
        "source": {
            "path": str(video_path),
            "duration_sec": _round_float(metadata.get("duration_sec"), 3, default=0.0),
            "fps": _round_float(metadata.get("fps"), 3, default=0.0),
            "resolution": f"{metadata.get('width', 0)}x{metadata.get('height', 0)}",
            "codec": metadata.get("codec", "unknown"),
        },
        "raw_scenes": _raw_scenes(phase_results),
        "sections": sections,
        "activity_curve": activity_curve,
        "thumbnail_moments": thumbnail_moments,
        "tags": tags,
        "ocr_results": _normalize_ocr_results(ocr_results or []),
        "policy_flags": all_policy_flags,
        "evidence": evidence[:200],
        "preview_outputs": _normalize_preview_outputs(preview_outputs or []),
        "insight": _compact_insight(insight),
    }
    analysis["analysis_summary"] = build_analysis_summary(analysis)
    return analysis


def build_analysis_summary(analysis: Optional[dict]) -> Dict[str, Any]:
    analysis = analysis if isinstance(analysis, dict) else {}
    tags = analysis.get("tags") if isinstance(analysis.get("tags"), list) else []
    sections = analysis.get("sections") if isinstance(analysis.get("sections"), list) else []
    policy_flags = analysis.get("policy_flags") if isinstance(analysis.get("policy_flags"), list) else []
    previews = analysis.get("preview_outputs") if isinstance(analysis.get("preview_outputs"), list) else []
    return {
        "schema_version": analysis.get("schema_version", SCHEMA_VERSION),
        "sections_count": len(sections),
        "top_section_tags": [str(s.get("section_tag")) for s in sections[:8] if isinstance(s, dict)],
        "top_tags": [str(t.get("tag")) for t in tags[:12] if isinstance(t, dict)],
        "policy_flags_count": len(policy_flags),
        "policy_flag_types": sorted(
            {
                str(f.get("flag") or f.get("type") or "").strip()
                for f in policy_flags
                if isinstance(f, dict) and str(f.get("flag") or f.get("type") or "").strip()
            }
        ),
        "ocr_results_count": len(analysis.get("ocr_results") or []),
        "preview_outputs_count": len(previews),
        "evidence_count": len(analysis.get("evidence") or []),
    }


def load_scene_analysis(work_dir: Optional[Path]) -> Optional[dict]:
    if not work_dir:
        return None
    path = Path(work_dir) / SIDE_CAR_NAME
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def compact_prompt_context(analysis: Optional[dict], *, max_chars: int = 900) -> str:
    """Return a small text block for metadata prompts."""
    analysis = analysis if isinstance(analysis, dict) else {}
    parts: List[str] = []

    sections = [
        str(s.get("section_tag"))
        for s in (analysis.get("sections") or [])[:8]
        if isinstance(s, dict) and s.get("section_tag")
    ]
    if sections:
        parts.append("analysis sections: " + ", ".join(sections))

    tags = [
        str(t.get("tag"))
        for t in (analysis.get("tags") or [])[:15]
        if isinstance(t, dict) and t.get("tag")
    ]
    if tags:
        parts.append("analysis tags: " + ", ".join(tags))

    moments = []
    for m in (analysis.get("thumbnail_moments") or [])[:5]:
        if not isinstance(m, dict):
            continue
        ts = _round_float(m.get("timestamp_sec"), 1, default=None)
        score = _round_float(m.get("score"), 1, default=None)
        if ts is not None:
            moments.append(f"{ts}s score {score}" if score is not None else f"{ts}s")
    if moments:
        parts.append("strong moments: " + ", ".join(moments))

    ocr = [
        str(r.get("full_text") or "").strip()
        for r in (analysis.get("ocr_results") or [])[:4]
        if isinstance(r, dict) and str(r.get("full_text") or "").strip()
    ]
    if ocr:
        parts.append("visible text/OCR: " + " | ".join(ocr))

    flags = [
        str(f.get("flag") or f.get("type") or "").strip()
        for f in (analysis.get("policy_flags") or [])[:8]
        if isinstance(f, dict) and str(f.get("flag") or f.get("type") or "").strip()
    ]
    if flags:
        parts.append("review warnings: " + ", ".join(flags))

    out = "; ".join(parts)
    return out[:max_chars].rstrip()


def run_ocr_policy_scan(
    *,
    video_path: Path,
    analysis: dict,
    ai_client: Any,
    max_frames: int = 8,
    system_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Best-effort local vision OCR/policy pass.

    Uses the configured local Ollama vision client. It is intentionally bounded
    and non-fatal; callers should treat empty results as normal.
    """
    if max_frames <= 0 or ai_client is None:
        return {"ocr_results": [], "policy_flags": [], "frames_scanned": 0, "skipped": True}
    if hasattr(ai_client, "is_alive") and not ai_client.is_alive():
        return {"ocr_results": [], "policy_flags": [], "frames_scanned": 0, "skipped": True, "reason": "ai_offline"}

    timestamps = _ocr_sample_timestamps(analysis, max_frames=max_frames)
    if not timestamps:
        return {"ocr_results": [], "policy_flags": [], "frames_scanned": 0, "skipped": True, "reason": "no_timestamps"}

    ocr_results: List[Dict[str, Any]] = []
    policy_flags: List[Dict[str, Any]] = []
    frames_scanned = 0
    prompt = _ocr_policy_prompt()

    try:
        with VideoReader(Path(video_path)) as vr:
            frames = vr.get_frames_at(timestamps)
    except Exception as exc:  # noqa: BLE001
        return {
            "ocr_results": [],
            "policy_flags": [],
            "frames_scanned": 0,
            "error": str(exc),
        }

    for ts, frame in zip(timestamps, frames):
        if frame is None:
            continue
        frames_scanned += 1
        try:
            resp = ai_client.score_frame(frame, prompt, system_prompt=system_prompt)
        except Exception as exc:  # noqa: BLE001
            policy_flags.append(_policy_flag("OCR_POLICY_AI_ERROR", ts, 0.0, str(exc)[:200]))
            continue
        if not getattr(resp, "success", False):
            policy_flags.append(
                _policy_flag(
                    "OCR_POLICY_AI_ERROR",
                    ts,
                    0.0,
                    str(getattr(resp, "error_code", None) or getattr(resp, "error_message", "") or "ai_error")[:200],
                )
            )
            continue
        parsed = _parse_ocr_policy_response(getattr(resp, "raw_text", "") or "")
        text = parsed.get("text", "")
        conf = float(parsed.get("confidence", 0.0) or 0.0)
        if text and text.upper() != "NONE":
            ocr_results.append(
                {
                    "language_code": None,
                    "full_text": text[:500],
                    "time_segment": {"start_seconds": max(0.0, ts - 0.5), "end_seconds": ts + 0.5},
                    "confidence": round(max(0.0, min(1.0, conf)), 3),
                    "source": "local_vision_ocr",
                }
            )
        for flag in parsed.get("flags", []):
            policy_flags.append(_policy_flag(flag, ts, conf, text[:200] if text else "vision_policy"))
        if parsed.get("payment_or_platform"):
            policy_flags.append(_policy_flag("PAYMENT_OR_PLATFORM_TEXT", ts, conf, text[:200] if text else "detected"))

    return {
        "ocr_results": ocr_results,
        "policy_flags": policy_flags,
        "frames_scanned": frames_scanned,
        "sampled_timestamps": timestamps,
    }


def _unique_entries(entries: List[dict]) -> List[dict]:
    seen: set[Tuple[float, str]] = set()
    out: List[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ts = _round_float(entry.get("timestamp_sec"), 3, default=None)
        if ts is None:
            continue
        label = _dominant_label(entry)
        key = (ts, label)
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return sorted(out, key=lambda x: float(x.get("timestamp_sec") or 0.0))


def _raw_scenes(phase_results: dict) -> List[dict]:
    stream = phase_results.get("stream_scan") if isinstance(phase_results.get("stream_scan"), dict) else {}
    raw = []
    for seg in stream.get("segment_stats") or []:
        if not isinstance(seg, dict):
            continue
        start = _round_float(seg.get("start_sec"), 3, default=None)
        end = _round_float(seg.get("end_sec"), 3, default=None)
        if start is None or end is None or end < start:
            continue
        raw.append(
            {
                "start_seconds": start,
                "end_seconds": end,
                "source": "stream_segment",
                "segment_idx": seg.get("segment_idx"),
            }
        )
    return raw


def _activity_curve(entries: List[dict]) -> List[dict]:
    curve = []
    for entry in entries[:1200]:
        scored = entry.get("scored_frame")
        curve.append(
            {
                "timestamp_sec": _round_float(entry.get("timestamp_sec"), 3, default=0.0),
                "motion": _round_float(entry.get("motion"), 3, default=0.0),
                "sharpness": _round_float(entry.get("sharpness"), 2, default=0.0),
                "score": _round_float(_entry_score(entry), 2, default=0.0),
                "section_tag": _dominant_label(entry),
                "position_label": entry.get("position_label") or getattr(scored, "position_label", None),
            }
        )
    return curve


def _thumbnail_moments(saved_covers: List[dict], evidence: List[dict]) -> List[dict]:
    moments = []
    for cover in saved_covers:
        if not isinstance(cover, dict):
            continue
        ts = _round_float(cover.get("timestamp_sec"), 3, default=0.0)
        ev_id = _evidence_id(len(evidence) + 1)
        tags = list(cover.get("genre_tags") or []) + list(cover.get("subgenre_tags") or [])
        notes = [f"saved cover rank {cover.get('rank')}"]
        sharp = _round_float(cover.get("output_sharpness"), 2, default=None)
        if sharp is not None:
            notes.append(f"output sharpness {sharp}")
        rescue = cover.get("blur_rescue") if isinstance(cover.get("blur_rescue"), dict) else {}
        if rescue.get("applied"):
            notes.append("blur rescue applied")
        validation = cover.get("cover_validation") if isinstance(cover.get("cover_validation"), dict) else {}
        if validation.get("reject_codes"):
            notes.append("cover validation reject: " + ",".join(str(x) for x in validation.get("reject_codes") or []))
        elif validation.get("warnings"):
            notes.append("cover validation warning: " + ",".join(str(x) for x in validation.get("warnings") or []))
        evidence.append(
            {
                "id": ev_id,
                "timestamp_sec": ts,
                "kind": "cover_candidate",
                "frame_path": str(cover.get("path") or ""),
                "score": _round_float(cover.get("score"), 2, default=0.0),
                "output_sharpness": sharp,
                "tags": tags,
                "notes": notes,
            }
        )
        moments.append(
            {
                "timestamp_sec": ts,
                "score": _round_float(cover.get("score"), 2, default=0.0),
                "output_sharpness": sharp,
                "reason": "cover_pick",
                "cover_rank": cover.get("rank"),
                "evidence_id": ev_id,
            }
        )
    return moments


def _semantic_sections(entries: List[dict], evidence: List[dict]) -> List[dict]:
    points = []
    for entry in entries:
        label = _dominant_label(entry)
        if not label or label == "UNKNOWN":
            continue
        ts = _round_float(entry.get("timestamp_sec"), 3, default=None)
        if ts is None:
            continue
        points.append(
            {
                "label": label,
                "ts": ts,
                "score": _entry_score(entry),
                "motion": _round_float(entry.get("motion"), 3, default=0.0),
                "confidence": _entry_confidence(entry),
                "entry": entry,
            }
        )
    points.sort(key=lambda x: x["ts"])

    sections: List[dict] = []
    current: Optional[dict] = None
    for point in points:
        if (
            current is not None
            and point["label"] == current["label"]
            and point["ts"] - current["last_ts"] <= _SECTION_MERGE_GAP_SEC
        ):
            current["last_ts"] = point["ts"]
            current["scores"].append(point["score"])
            current["motions"].append(point["motion"])
            current["confidences"].append(point["confidence"])
            current["thumb_ts"] = max(current["thumb_ts"], point["ts"], key=lambda _ts: _score_at_ts(points, _ts))
            continue
        if current is not None:
            sections.append(_finalize_section(current, evidence))
        current = {
            "label": point["label"],
            "first_ts": point["ts"],
            "last_ts": point["ts"],
            "thumb_ts": point["ts"],
            "scores": [point["score"]],
            "motions": [point["motion"]],
            "confidences": [point["confidence"]],
        }
    if current is not None:
        sections.append(_finalize_section(current, evidence))

    sections.sort(key=lambda s: float(s.get("confidence") or 0.0), reverse=True)
    return sections[:80]


def _finalize_section(raw: dict, evidence: List[dict]) -> dict:
    start = max(0.0, float(raw["first_ts"]) - 2.0)
    end = max(start + 1.0, float(raw["last_ts"]) + 2.0)
    score_avg = _avg(raw.get("scores") or [])
    motion_avg = _avg(raw.get("motions") or [])
    confidence = max(_avg(raw.get("confidences") or []), min(1.0, score_avg / 100.0))
    ev_id = _evidence_id(len(evidence) + 1)
    evidence.append(
        {
            "id": ev_id,
            "timestamp_sec": _round_float(raw.get("thumb_ts"), 3, default=0.0),
            "kind": "semantic_section",
            "frame_path": None,
            "score": round(score_avg, 2),
            "tags": [raw["label"]],
            "notes": [f"section {raw['label']}"],
        }
    )
    return {
        "section_tag": raw["label"],
        "time_segments": [{"start_seconds": round(start, 3), "end_seconds": round(end, 3)}],
        "thumbnail_timestamp": _round_float(raw.get("thumb_ts"), 3, default=0.0),
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "is_critical": False,
        "relative_activity": round(score_avg / 10.0 + motion_avg, 3),
        "evidence_ids": [ev_id],
    }


def _aggregate_tags(entries: List[dict], saved_covers: List[dict]) -> List[dict]:
    stats: Dict[str, Dict[str, Any]] = {}
    for entry in list(entries) + list(saved_covers or []):
        if not isinstance(entry, dict):
            continue
        labels = []
        labels.extend([str(x) for x in entry.get("genre_tags") or [] if str(x).strip()])
        labels.extend([str(x) for x in entry.get("subgenre_tags") or [] if str(x).strip()])
        pos = str(entry.get("position_label") or "").strip()
        if pos and pos != "OTHER":
            labels.append(pos)
        for label in labels:
            clean = _clean_label(label)
            if not clean:
                continue
            row = stats.setdefault(clean, {"tag": clean, "count": 0, "probability": 0.0, "source": "ai_frame"})
            row["count"] += 1
            row["probability"] = max(float(row["probability"]), _entry_confidence(entry))
    rows = list(stats.values())
    rows.sort(key=lambda r: (float(r.get("probability") or 0.0), int(r.get("count") or 0)), reverse=True)
    for row in rows:
        row["probability"] = round(float(row.get("probability") or 0.0), 3)
    return rows[:80]


def _policy_flags_from_content(phase_results: dict, evidence: List[dict]) -> List[dict]:
    content = phase_results.get("content_flags") if isinstance(phase_results.get("content_flags"), dict) else {}
    if not content.get("flagged"):
        return []
    out = []
    for item in content.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        ts = _round_float(item.get("timestamp_sec"), 3, default=0.0)
        ev_id = _evidence_id(len(evidence) + 1)
        flags = [str(x) for x in (item.get("flags") or []) if str(x).strip()]
        evidence.append(
            {
                "id": ev_id,
                "timestamp_sec": ts,
                "kind": "policy_flag",
                "frame_path": None,
                "score": _round_float(item.get("score"), 2, default=0.0),
                "tags": flags,
                "notes": ["sensitive content review flag"],
            }
        )
        for flag in flags:
            out.append(
                {
                    "flag": _clean_label(flag),
                    "type": "sensitive_content",
                    "timestamp_sec": ts,
                    "confidence": _round_float(item.get("confidence"), 3, default=0.0),
                    "source": "scoring_prompt",
                    "evidence_id": ev_id,
                    "review_only": True,
                }
            )
    return out


def _policy_flags_from_cover_validation(entries: List[dict], evidence: List[dict]) -> List[dict]:
    out = []
    seen: set[Tuple[float, str]] = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        validation = entry.get("cover_validation")
        if not isinstance(validation, dict):
            continue
        codes = list(validation.get("reject_codes") or []) + list(validation.get("warnings") or [])
        if not codes:
            continue
        ts = _round_float(entry.get("timestamp_sec"), 3, default=0.0)
        for code in codes:
            clean = _clean_label(code)
            if not clean:
                continue
            key = (ts, clean)
            if key in seen:
                continue
            seen.add(key)
            ev_id = _evidence_id(len(evidence) + 1)
            scored = entry.get("scored_frame")
            evidence.append(
                {
                    "id": ev_id,
                    "timestamp_sec": ts,
                    "kind": "cover_validation",
                    "frame_path": None,
                    "score": _round_float(getattr(scored, "score", 0.0), 2, default=0.0) if scored else 0.0,
                    "tags": [clean],
                    "notes": ["cover validation rejected candidate" if validation.get("reject_codes") else "cover validation warning"],
                }
            )
            out.append(
                {
                    "flag": clean,
                    "type": "cover_validation",
                    "timestamp_sec": ts,
                    "confidence": _round_float(validation.get("penetration_confidence"), 3, default=0.0),
                    "source": validation.get("validator_source", "deterministic"),
                    "evidence_id": ev_id,
                    "review_only": True,
                    "eligible": bool(validation.get("eligible")),
                }
            )
    return out


def _normalize_policy_flags(flags: List[dict]) -> List[dict]:
    out = []
    for flag in flags:
        if not isinstance(flag, dict):
            continue
        name = _clean_label(flag.get("flag") or flag.get("type") or "")
        if not name:
            continue
        row = dict(flag)
        row["flag"] = name
        row.setdefault("review_only", True)
        if "confidence" in row:
            row["confidence"] = _round_float(row.get("confidence"), 3, default=0.0)
        if "timestamp_sec" in row:
            row["timestamp_sec"] = _round_float(row.get("timestamp_sec"), 3, default=0.0)
        out.append(row)
    return out


def _normalize_ocr_results(rows: List[dict]) -> List[dict]:
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("full_text") or "").strip()
        if not text:
            continue
        out.append(
            {
                "language_code": row.get("language_code"),
                "full_text": text[:1000],
                "time_segment": row.get("time_segment"),
                "confidence": _round_float(row.get("confidence"), 3, default=None),
                "source": row.get("source", "unknown"),
            }
        )
    return out


def _normalize_preview_outputs(rows: List[dict]) -> List[dict]:
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        clean = dict(row)
        if clean.get("path") is not None:
            clean["path"] = str(clean.get("path"))
        if clean.get("gif_path") is not None:
            clean["gif_path"] = str(clean.get("gif_path"))
        out.append(clean)
    return out


def _compact_insight(insight: Optional[dict]) -> Optional[dict]:
    if not isinstance(insight, dict):
        return None
    keys = [
        "setting",
        "location_hint",
        "notable_features",
        "action_summary",
        "mood",
        "ai_tags",
        "ai_categories",
    ]
    return {k: insight.get(k) for k in keys if k in insight}


def _ocr_sample_timestamps(analysis: dict, *, max_frames: int) -> List[float]:
    values: List[float] = []
    for m in analysis.get("thumbnail_moments") or []:
        if isinstance(m, dict):
            _append_ts(values, m.get("timestamp_sec"))
    for sec in analysis.get("sections") or []:
        if isinstance(sec, dict):
            _append_ts(values, sec.get("thumbnail_timestamp"))
    for point in analysis.get("activity_curve") or []:
        if isinstance(point, dict) and float(point.get("score") or 0.0) >= 70:
            _append_ts(values, point.get("timestamp_sec"))
    deduped = []
    for ts in values:
        if any(abs(ts - old) < 10.0 for old in deduped):
            continue
        deduped.append(ts)
        if len(deduped) >= max_frames:
            break
    return deduped


def _append_ts(values: List[float], raw: Any) -> None:
    ts = _round_float(raw, 3, default=None)
    if ts is not None and ts >= 0:
        values.append(ts)


def _ocr_policy_prompt() -> str:
    return """Inspect this adult VOD frame for visible on-screen text only.

Return OCR text if text is actually visible. Also flag payment handles, URLs,
social handles, QR/payment prompts, or platform-risk text. Do not infer text
that is not visible.

OUTPUT FORMAT:
OCR_TEXT: <visible text, or NONE>
PAYMENT_OR_PLATFORM_TEXT: <yes|no>
POLICY_FLAGS: <PAYMENT_HANDLE, URL, SOCIAL_HANDLE, QR_CODE, WATERMARK, or NONE>
CONFIDENCE: <0.00-1.00>
END
"""


def _parse_ocr_policy_response(raw: str) -> Dict[str, Any]:
    def line(name: str) -> str:
        m = re.search(rf"^\s*{re.escape(name)}:\s*(.+)$", raw or "", re.IGNORECASE | re.MULTILINE)
        return (m.group(1).strip() if m else "")

    text = line("OCR_TEXT")
    payment = line("PAYMENT_OR_PLATFORM_TEXT").lower() in {"yes", "true", "1"}
    flags_raw = line("POLICY_FLAGS")
    flags = []
    for token in re.split(r"[,;/|]", flags_raw):
        clean = _clean_label(token)
        if clean and clean != "NONE":
            flags.append(clean)
    try:
        conf = float(line("CONFIDENCE") or 0.0)
    except ValueError:
        conf = 0.0
    return {
        "text": "" if text.upper() == "NONE" else text,
        "payment_or_platform": payment,
        "flags": flags,
        "confidence": max(0.0, min(1.0, conf)),
    }


def _policy_flag(flag: str, ts: float, confidence: float, detail: str) -> Dict[str, Any]:
    return {
        "flag": _clean_label(flag),
        "type": "ocr_policy",
        "timestamp_sec": _round_float(ts, 3, default=0.0),
        "confidence": round(max(0.0, min(1.0, float(confidence or 0.0))), 3),
        "source": "local_vision_ocr",
        "detail": detail,
        "review_only": True,
    }


def _dominant_label(entry: dict) -> str:
    hint = _clean_label(entry.get("analysis_section_tag") or "")
    if hint:
        return hint
    pos = _clean_label(entry.get("position_label") or "")
    if pos and pos != "OTHER":
        return pos
    for key in ("genre_tags", "subgenre_tags", "zone_tags"):
        vals = entry.get(key) or []
        if isinstance(vals, list):
            for val in vals:
                clean = _clean_label(val)
                if clean:
                    return clean
    scored = entry.get("scored_frame")
    type_ = _clean_label(getattr(scored, "type_", "") if scored is not None else entry.get("type", ""))
    return type_ or "UNKNOWN"


def _entry_score(entry: dict) -> float:
    if "score" in entry:
        return float(_round_float(entry.get("score"), 3, default=0.0) or 0.0)
    scored = entry.get("scored_frame")
    return float(_round_float(getattr(scored, "score", 0.0), 3, default=0.0) or 0.0)


def _entry_confidence(entry: dict) -> float:
    raw = entry.get("position_label_confidence")
    scored = entry.get("scored_frame")
    if raw is None and scored is not None:
        raw = getattr(scored, "position_confidence", None)
    conf = _round_float(raw, 3, default=None)
    if conf is not None and conf > 0:
        return max(0.0, min(1.0, conf))
    return max(0.0, min(1.0, _entry_score(entry) / 100.0))


def _score_at_ts(points: Iterable[dict], ts: float) -> float:
    for point in points:
        if abs(float(point.get("ts") or -1) - float(ts)) < 0.001:
            return float(point.get("score") or 0.0)
    return 0.0


def _clean_label(raw: Any) -> str:
    label = str(raw or "").strip().upper().replace("-", "_").replace(" ", "_")
    label = re.sub(r"[^A-Z0-9_]+", "", label)
    label = re.sub(r"_+", "_", label).strip("_")
    return "" if label in {"", "NONE", "NULL"} else label


def _round_float(raw: Any, ndigits: int, *, default: Any) -> Any:
    try:
        if raw is None:
            return default
        return round(float(raw), ndigits)
    except (TypeError, ValueError):
        return default


def _avg(values: List[float]) -> float:
    nums = [float(v) for v in values if v is not None]
    return sum(nums) / len(nums) if nums else 0.0


def _evidence_id(n: int) -> str:
    return f"ev_{n:03d}"
