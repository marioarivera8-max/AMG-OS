"""Build operator-reviewed publish packages for downstream handoff."""
import csv
import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from amg.compliance.registry import compliance_status_for_scene
from amg.config import (
    DATA_DIR,
    DECISION_LOGS_DIR,
    DISTRIBUTION_STATUS_DIR,
    PLATFORM_REQUIREMENTS,
    PUBLISH_PACKAGES_DIR,
    REVIEWED_DIR,
)
from amg.publication.ledger import record_publication_event
from amg.review.distribution_gate import check_distribution_ready

PACKAGE_SCHEMA_VERSION = "1.0"


class PackageError(RuntimeError):
    """Raised when a scene cannot be packaged for publication yet."""


def package_eligibility(scene_id: str, platforms: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Return whether a scene can be packaged, with blockers per platform."""
    decision_log = _load_json(DECISION_LOGS_DIR, scene_id)
    review = _load_json(REVIEWED_DIR, scene_id)
    readiness = check_distribution_ready(scene_id, verbose=False)
    requested = _target_platforms(review, platforms)
    blockers: List[str] = []
    warnings: List[str] = []
    per_platform: Dict[str, Any] = {}

    if not decision_log:
        blockers.append("No decision log")
    if not review:
        blockers.append("No human review")
    elif not review.get("finalized_thumbnails"):
        blockers.append("Thumbnails are not finalized")

    meta = _review_metadata(review or {})
    if not meta["hero_cover"]:
        blockers.append("No hero/kept cover selected")

    for platform in requested:
        status = (readiness.get("per_platform") or {}).get(platform, {})
        platform_blockers = list(status.get("blockers") or [])
        if not status.get("ready"):
            platform_blockers.append("Distribution gate is not ready")
        per_platform[platform] = {
            "ready": bool(status.get("ready")) and not blockers and not platform_blockers,
            "blockers": platform_blockers,
            "warnings": list(status.get("warnings") or []),
        }
        warnings.extend(f"{platform}: {w}" for w in status.get("warnings") or [])

    return {
        "scene_id": scene_id,
        "target_platforms": requested,
        "overall_ready": not blockers and any(p.get("ready") for p in per_platform.values()),
        "blockers": blockers,
        "warnings": warnings,
        "per_platform": per_platform,
        "readiness": readiness,
    }


def build_publish_package(
    scene_id: str,
    *,
    platforms: Optional[Iterable[str]] = None,
    force: bool = False,
    operator: Optional[str] = None,
) -> Dict[str, Any]:
    """Create platform-specific publish packages for a reviewed scene."""
    decision_log = _load_json(DECISION_LOGS_DIR, scene_id)
    review = _load_json(REVIEWED_DIR, scene_id)
    if not decision_log:
        raise PackageError(f"No decision log found for scene '{scene_id}'")
    if not review:
        raise PackageError(f"No review record found for scene '{scene_id}'")
    if not review.get("finalized_thumbnails"):
        raise PackageError("Finalize thumbnails before building a publish package")

    requested = _target_platforms(review, platforms)
    if not requested:
        raise PackageError("No target platforms selected")

    eligibility = package_eligibility(scene_id, requested)
    if eligibility["blockers"]:
        raise PackageError("; ".join(eligibility["blockers"]))
    blocked_platforms = [
        f"{platform}: {', '.join(status.get('blockers') or ['not ready'])}"
        for platform, status in eligibility["per_platform"].items()
        if not status.get("ready")
    ]
    if blocked_platforms:
        raise PackageError("; ".join(blocked_platforms))

    work_dir = _resolve_work_dir(scene_id, decision_log)
    meta = _review_metadata(review)
    cover_paths = _collect_cover_paths(work_dir, decision_log, review, meta)
    if not cover_paths:
        raise PackageError("No selected cover files could be found")

    result = {
        "scene_id": scene_id,
        "created_at": _utc_now_iso(),
        "platforms": requested,
        "packages": [],
    }
    safe_scene = _safe_scene_id(scene_id)

    for platform in requested:
        package_id = f"{safe_scene}_{platform}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
        final_dir = PUBLISH_PACKAGES_DIR / safe_scene / platform / package_id
        if final_dir.exists() and not force:
            raise PackageError(f"Package already exists: {final_dir}")
        tmp_dir = final_dir.parent / f".incoming_{package_id}"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            package_row = _build_one_package(
                scene_id=scene_id,
                platform=platform,
                package_id=package_id,
                package_dir=tmp_dir,
                work_dir=work_dir,
                decision_log=decision_log,
                review=review,
                metadata=meta,
                cover_paths=cover_paths,
                readiness=eligibility["readiness"],
            )
            if final_dir.exists() and force:
                shutil.rmtree(final_dir)
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            tmp_dir.replace(final_dir)
            zip_path = shutil.make_archive(str(final_dir), "zip", root_dir=final_dir)
            package_row["package_path"] = str(final_dir)
            package_row["package_zip_path"] = str(zip_path)
            package_row["manifest_path"] = str(final_dir / "package_manifest.json")
            result["packages"].append(package_row)
            record_publication_event(
                scene_id,
                platform,
                "packaged",
                operator=operator,
                package_path=final_dir,
                metadata={"package_id": package_id, "package_zip_path": str(zip_path)},
            )
        except Exception:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            raise

    return result


def _build_one_package(
    *,
    scene_id: str,
    platform: str,
    package_id: str,
    package_dir: Path,
    work_dir: Optional[Path],
    decision_log: Dict[str, Any],
    review: Dict[str, Any],
    metadata: Dict[str, Any],
    cover_paths: List[Path],
    readiness: Dict[str, Any],
) -> Dict[str, Any]:
    assets_dir = package_dir / "assets"
    records_dir = package_dir / "source_records"
    assets_dir.mkdir(parents=True, exist_ok=True)
    records_dir.mkdir(parents=True, exist_ok=True)

    copied_covers = []
    hero_name = metadata["hero_cover"]
    hero_source = next((p for p in cover_paths if p.name == hero_name), cover_paths[0])
    hero_target = assets_dir / f"hero_cover{hero_source.suffix.lower() or '.jpg'}"
    shutil.copy2(hero_source, hero_target)

    kept_dir = assets_dir / "kept_covers"
    kept_dir.mkdir(parents=True, exist_ok=True)
    for idx, src in enumerate(cover_paths, start=1):
        target = kept_dir / f"{idx:02d}_{src.name}"
        shutil.copy2(src, target)
        copied_covers.append(str(target.relative_to(package_dir)))

    if work_dir and work_dir.exists():
        _copy_optional(work_dir / "00_soft_thumbnail.jpg", assets_dir / "00_soft_thumbnail.jpg")
        _copy_optional(work_dir / "soft_thumbnail.json", records_dir / "soft_thumbnail.json")
        previews_dir = work_dir / "previews"
        if previews_dir.is_dir():
            shutil.copytree(previews_dir, assets_dir / "previews")

    _write_json(records_dir / "decision_log.json", decision_log)
    _write_json(records_dir / "review.json", review)
    dist_path = DISTRIBUTION_STATUS_DIR / f"{_safe_scene_id(scene_id)}.json"
    if dist_path.exists():
        shutil.copy2(dist_path, records_dir / "distribution_status.json")

    platform_metadata = _platform_metadata(platform, metadata, review)
    _write_json(package_dir / "metadata.json", platform_metadata)
    _write_metadata_txt(package_dir / "metadata.txt", platform_metadata)
    _write_metadata_csv(package_dir / "metadata.csv", platform_metadata)

    compliance = compliance_status_for_scene(
        scene_id,
        performers=metadata.get("performers") or [],
        target_platforms=[platform],
    )
    _write_json(package_dir / "compliance_manifest.json", compliance)

    manifest = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "package_id": package_id,
        "scene_id": scene_id,
        "platform": platform,
        "created_at": _utc_now_iso(),
        "human_review_required": True,
        "auto_upload": False,
        "hero_cover": str(hero_target.relative_to(package_dir)),
        "kept_covers": copied_covers,
        "readiness": {
            "overall_ready": readiness.get("overall_ready"),
            "platform": (readiness.get("per_platform") or {}).get(platform, {}),
        },
    }
    manifest["checksums_path"] = "checksums.sha256"
    manifest["file_count"] = _count_checksum_files(package_dir) + 1
    _write_json(package_dir / "package_manifest.json", manifest)
    checksums = _write_checksums(package_dir)
    return {
        "package_id": package_id,
        "platform": platform,
        "manifest_path": str(package_dir / "package_manifest.json"),
        "hero_cover": str(hero_target.relative_to(package_dir)),
        "file_count": len(checksums),
    }


def _platform_metadata(platform: str, metadata: Dict[str, Any], review: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "platform": platform,
        "title": metadata["title"],
        "long_description": metadata["long_description"],
        "tags": metadata["tags"],
        "categories": metadata["categories"],
        "performers": metadata["performers"],
        "target_platforms": metadata["target_platforms"],
        "hero_cover": metadata["hero_cover"],
        "kept_covers": metadata["kept_covers"],
        "operator_notes": review.get("notes") or "",
        "metadata_reason_codes": review.get("metadata_reason_codes") or [],
    }


def _review_metadata(review: Dict[str, Any]) -> Dict[str, Any]:
    title = ((review.get("title") or {}).get("text")) or review.get("title_override") or ""
    kept = review.get("kept_covers") or []
    selected = review.get("selected_covers") or []
    hero = ((review.get("cover_pick") or {}).get("filename") or "").strip()
    if not hero:
        hero = str(kept[0]) if isinstance(kept, list) and kept else str(selected[0]) if isinstance(selected, list) and selected else ""
    return {
        "title": str(title).strip(),
        "long_description": str(review.get("long_description") or "").strip(),
        "tags": _tokens(review.get("tags_csv")),
        "categories": _tokens(review.get("categories_csv"), title_case=True),
        "performers": [str(x).strip() for x in (review.get("performers_confirmed") or []) if str(x).strip()],
        "target_platforms": _target_platforms(review, None),
        "hero_cover": hero,
        "kept_covers": [str(x) for x in kept] if isinstance(kept, list) else [],
        "selected_covers": [str(x) for x in selected] if isinstance(selected, list) else [],
    }


def _target_platforms(review: Optional[Dict[str, Any]], platforms: Optional[Iterable[str]]) -> List[str]:
    if platforms:
        raw = list(platforms)
        if len(raw) == 1 and str(raw[0]).strip().lower() == "all":
            raw = (review or {}).get("target_platforms") or list(PLATFORM_REQUIREMENTS.keys())
    else:
        raw = (review or {}).get("target_platforms") or list(PLATFORM_REQUIREMENTS.keys())
    allowed = set(PLATFORM_REQUIREMENTS.keys())
    out = []
    for item in raw:
        p = str(item).strip().upper()
        if p and p in allowed and p not in out:
            out.append(p)
    return out


def _collect_cover_paths(
    work_dir: Optional[Path],
    decision_log: Dict[str, Any],
    review: Dict[str, Any],
    metadata: Dict[str, Any],
) -> List[Path]:
    names = []
    for name in [metadata.get("hero_cover")] + metadata.get("kept_covers", []) + metadata.get("selected_covers", []):
        if name and name not in names:
            names.append(str(name))
    out: List[Path] = []
    roots: List[Path] = []
    if work_dir:
        roots.extend([work_dir / "covers", work_dir])
    for cover in (decision_log.get("outcomes", {}) or {}).get("saved_covers", []) or []:
        for key in ("path", "output_path", "filename"):
            value = cover.get(key) if isinstance(cover, dict) else None
            if not value:
                continue
            candidate = Path(value)
            if candidate.exists() and candidate.is_file():
                roots.append(candidate.parent)
    for name in names:
        found = _find_named_file(name, roots)
        if found and found not in out:
            out.append(found)
    return out


def _find_named_file(name: str, roots: Iterable[Path]) -> Optional[Path]:
    for root in roots:
        if not root:
            continue
        root = Path(root)
        candidate = root / name
        if candidate.exists() and candidate.is_file():
            return candidate
    for root in roots:
        if not root or not Path(root).is_dir():
            continue
        matches = list(Path(root).rglob(name))
        for match in matches:
            if match.is_file():
                return match
    return None


def _resolve_work_dir(scene_id: str, decision_log: Dict[str, Any]) -> Optional[Path]:
    analysis_path = decision_log.get("analysis_path")
    if analysis_path:
        p = Path(analysis_path)
        if p.exists():
            return p.parent
    scene_path = decision_log.get("scene_path")
    if scene_path:
        video = Path(scene_path)
        candidate = video.parent / f"{video.stem}_amg_v11"
        if candidate.exists():
            return candidate
    cloud_dir = DATA_DIR / "work_dirs" / _safe_scene_id(scene_id)
    if cloud_dir.exists():
        return cloud_dir
    return None


def _tokens(value: Any, *, title_case: bool = False) -> List[str]:
    if isinstance(value, list):
        raw = [str(x).strip() for x in value]
    else:
        raw = [x.strip() for x in str(value or "").replace(";", ",").split(",")]
    out = []
    seen = set()
    for token in raw:
        if not token:
            continue
        clean = " ".join(token.split())
        if title_case:
            clean = " ".join(part.capitalize() for part in clean.split())
        lower = clean.lower()
        if lower in seen:
            continue
        seen.add(lower)
        out.append(clean)
    return out


def _load_json(directory: Path, scene_id: str) -> Optional[Dict[str, Any]]:
    path = directory / f"{_safe_scene_id(scene_id)}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str), encoding="utf-8")


def _write_metadata_txt(path: Path, metadata: Dict[str, Any]) -> None:
    lines = [
        f"Platform: {metadata.get('platform', '')}",
        f"Title: {metadata.get('title', '')}",
        "",
        "Description:",
        metadata.get("long_description", ""),
        "",
        "Categories:",
        ", ".join(metadata.get("categories") or []),
        "",
        "Tags:",
        ", ".join(metadata.get("tags") or []),
        "",
        "Performers:",
        ", ".join(metadata.get("performers") or []),
    ]
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _write_metadata_csv(path: Path, metadata: Dict[str, Any]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["platform", "title", "long_description", "categories", "tags", "performers", "hero_cover"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "platform": metadata.get("platform", ""),
                "title": metadata.get("title", ""),
                "long_description": metadata.get("long_description", ""),
                "categories": ", ".join(metadata.get("categories") or []),
                "tags": ", ".join(metadata.get("tags") or []),
                "performers": ", ".join(metadata.get("performers") or []),
                "hero_cover": metadata.get("hero_cover", ""),
            }
        )


def _write_checksums(package_dir: Path) -> List[str]:
    rows = []
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file() or path.name == "checksums.sha256":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rel = path.relative_to(package_dir).as_posix()
        rows.append(f"{digest}  {rel}")
    (package_dir / "checksums.sha256").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return rows


def _count_checksum_files(package_dir: Path) -> int:
    return sum(1 for path in package_dir.rglob("*") if path.is_file() and path.name != "checksums.sha256")


def _copy_optional(src: Path, dst: Path) -> None:
    if src.exists() and src.is_file():
        shutil.copy2(src, dst)


def _safe_scene_id(scene_id: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in str(scene_id or ""))[:120]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
