"""Operator-maintained compliance document registry."""
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from amg.config import (
    PERFORMER_DOCS_DIR,
    PERFORMER_DOCS_REGISTRY_PATH,
    PLATFORM_REQUIREMENTS,
)

DOC_TYPES = {
    "model_release",
    "2257",
    "id",
    "w9",
    "other",
}


def load_registry() -> Dict[str, Any]:
    if not PERFORMER_DOCS_REGISTRY_PATH.exists():
        return {"schema_version": 1, "performers": {}, "scene_documents": {}}
    try:
        payload = json.loads(PERFORMER_DOCS_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    payload.setdefault("schema_version", 1)
    payload.setdefault("performers", {})
    payload.setdefault("scene_documents", {})
    return payload


def save_registry(registry: Dict[str, Any]) -> Path:
    PERFORMER_DOCS_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PERFORMER_DOCS_REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
    tmp.replace(PERFORMER_DOCS_REGISTRY_PATH)
    return PERFORMER_DOCS_REGISTRY_PATH


def upsert_document(
    performer_name: str,
    document_type: str,
    path: Path | str,
    *,
    platforms: Optional[List[str]] = None,
    issue_date: Optional[str] = None,
    expiry_date: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Add or update a performer compliance document."""
    performer_name = " ".join(str(performer_name or "").split())
    if not performer_name:
        raise ValueError("performer_name is required")
    document_type = str(document_type or "").strip().lower()
    if document_type not in DOC_TYPES:
        document_type = "other"
    doc_path = str(Path(path).expanduser())
    registry = load_registry()
    performer_key = _performer_key(performer_name)
    performer = registry.setdefault("performers", {}).setdefault(
        performer_key,
        {"name": performer_name, "documents": []},
    )
    performer["name"] = performer_name
    docs = performer.setdefault("documents", [])
    now = _utc_now_iso()
    row = {
        "document_type": document_type,
        "path": doc_path,
        "platforms": [str(p).upper().strip() for p in (platforms or []) if str(p).strip()],
        "issue_date": issue_date,
        "expiry_date": expiry_date,
        "notes": str(notes).strip() if notes else None,
        "updated_at": now,
    }
    for idx, existing in enumerate(docs):
        if existing.get("document_type") == document_type and existing.get("path") == doc_path:
            docs[idx] = {**existing, **row}
            save_registry(registry)
            return docs[idx]
    row["created_at"] = now
    docs.append(row)
    save_registry(registry)
    return row


def performer_status(performer_name: str) -> Dict[str, Any]:
    registry = load_registry()
    key = _performer_key(performer_name)
    performer = registry.get("performers", {}).get(key) or {
        "name": performer_name,
        "documents": [],
    }
    docs = list(performer.get("documents") or [])
    return {
        "name": performer.get("name") or performer_name,
        "documents": docs,
        "has_model_release": any(_doc_matches(d, "model_release") and not _is_expired(d) for d in docs),
        "has_2257": any(_doc_matches(d, "2257") and not _is_expired(d) for d in docs),
        "expired_documents": [d for d in docs if _is_expired(d)],
    }


def compliance_status_for_scene(
    scene_id: str,
    *,
    performers: Optional[List[str]] = None,
    target_platforms: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Evaluate registry coverage for a scene/platform handoff."""
    platforms = [str(p).upper().strip() for p in (target_platforms or []) if str(p).strip()]
    if not platforms:
        platforms = list(PLATFORM_REQUIREMENTS.keys())
    performer_names = [" ".join(str(p).split()) for p in (performers or []) if str(p).strip()]
    per_performer = {name: performer_status(name) for name in performer_names}
    per_platform: Dict[str, Any] = {}
    blockers: List[str] = []
    warnings: List[str] = []

    for platform in platforms:
        reqs = PLATFORM_REQUIREMENTS.get(platform, {}) if isinstance(PLATFORM_REQUIREMENTS, dict) else {}
        platform_blockers: List[str] = []
        platform_warnings: List[str] = []
        if reqs.get("requires_individual_releases", False):
            for name, status in per_performer.items():
                if not status["has_model_release"]:
                    platform_blockers.append(f"Missing model release: {name}")
        if reqs.get("requires_2257", True):
            if not performer_names:
                platform_warnings.append("No performers confirmed for registry match")
            elif not any(status["has_2257"] for status in per_performer.values()):
                platform_warnings.append("No 2257 registry document linked to confirmed performers")
        for name, status in per_performer.items():
            for doc in status["expired_documents"]:
                platform_blockers.append(
                    f"Expired {doc.get('document_type', 'document')} for {name}: {doc.get('expiry_date')}"
                )
        per_platform[platform] = {
            "ready": not platform_blockers,
            "blockers": platform_blockers,
            "warnings": platform_warnings,
        }
        blockers.extend(f"{platform}: {b}" for b in platform_blockers)
        warnings.extend(f"{platform}: {w}" for w in platform_warnings)

    return {
        "scene_id": scene_id,
        "performers": performer_names,
        "target_platforms": platforms,
        "per_performer": per_performer,
        "per_platform": per_platform,
        "blockers": blockers,
        "warnings": warnings,
        "overall_ready": len(blockers) == 0,
    }


def scan_document_directory() -> Dict[str, Any]:
    """Index obvious performer document files from ``PERFORMER_DOCS_DIR``."""
    PERFORMER_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    added = 0
    scanned = 0
    for path in PERFORMER_DOCS_DIR.iterdir():
        if not path.is_file():
            continue
        if path.name == PERFORMER_DOCS_REGISTRY_PATH.name:
            continue
        scanned += 1
        lower = path.stem.lower()
        doc_type = "model_release" if "release" in lower else "2257" if "2257" in lower else "other"
        if doc_type == "other":
            continue
        performer = _guess_performer_from_filename(path)
        upsert_document(performer, doc_type, path)
        added += 1
    return {
        "registry_path": str(PERFORMER_DOCS_REGISTRY_PATH),
        "scanned": scanned,
        "indexed": added,
    }


def _performer_key(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in str(name).lower()).strip("_")


def _guess_performer_from_filename(path: Path) -> str:
    stem = path.stem
    for token in ("model_release", "release", "2257", "id"):
        stem = stem.replace(token, " ")
        stem = stem.replace(token.upper(), " ")
    cleaned = " ".join(stem.replace("_", " ").replace("-", " ").split())
    return cleaned.title() or "Unknown"


def _doc_matches(doc: Dict[str, Any], doc_type: str) -> bool:
    return str(doc.get("document_type") or "").lower() == doc_type


def _is_expired(doc: Dict[str, Any]) -> bool:
    raw = doc.get("expiry_date")
    if not raw:
        return False
    try:
        return date.fromisoformat(str(raw)[:10]) < date.today()
    except ValueError:
        return False


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
