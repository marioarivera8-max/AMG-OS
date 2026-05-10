"""Disk cleanup utilities for controller/pod AMG data.

The cleanup policy is intentionally conservative. By default it only targets
transient source staging and upload folders that AMG can recreate or no longer
needs after a cloud job completes. Review artifacts under ``work_dirs`` are
reported but not deleted unless the caller explicitly opts in.
"""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from amg.config import DATA_DIR
from amg.utils.logging import get_logger

log = get_logger("maintenance.disk_cleanup")


@dataclass(frozen=True)
class CleanupTarget:
    path: Path
    category: str
    age_seconds: float
    size_bytes: int
    reason: str


@dataclass
class CleanupReport:
    data_dir: Path
    dry_run: bool
    targets: list[CleanupTarget] = field(default_factory=list)
    deleted: list[CleanupTarget] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    free_before_bytes: int = 0
    free_after_bytes: int = 0

    @property
    def reclaimable_bytes(self) -> int:
        return sum(t.size_bytes for t in self.targets)

    @property
    def deleted_bytes(self) -> int:
        return sum(t.size_bytes for t in self.deleted)

    def to_dict(self) -> dict:
        return {
            "data_dir": str(self.data_dir),
            "dry_run": self.dry_run,
            "targets_count": len(self.targets),
            "deleted_count": len(self.deleted),
            "reclaimable_bytes": self.reclaimable_bytes,
            "deleted_bytes": self.deleted_bytes,
            "free_before_bytes": self.free_before_bytes,
            "free_after_bytes": self.free_after_bytes,
            "errors": list(self.errors),
            "targets": [_target_to_dict(t) for t in self.targets],
            "deleted": [_target_to_dict(t) for t in self.deleted],
        }


def parse_age_to_seconds(value: str | int | float) -> int:
    """Parse compact ages like ``2d``, ``12h``, ``45m``, or raw seconds."""
    if isinstance(value, (int, float)):
        return max(0, int(value))
    raw = str(value or "").strip().lower()
    if not raw:
        raise ValueError("age cannot be empty")
    suffix = raw[-1]
    number = raw[:-1] if suffix in {"d", "h", "m", "s"} else raw
    try:
        amount = float(number)
    except ValueError as exc:
        raise ValueError(f"invalid age {value!r}") from exc
    multipliers = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    return max(0, int(amount * multipliers.get(suffix, 1)))


def clean_amg_data(
    *,
    data_dir: Path = DATA_DIR,
    dry_run: bool = True,
    cloud_fallback_older_than: str | int | float = "2d",
    ui_uploads_older_than: str | int | float = "14d",
    pod_uploads_older_than: str | int | float = "2d",
    artifact_tmp_older_than: str | int | float = "6h",
    include_work_dirs: bool = False,
    work_dirs_older_than: str | int | float = "60d",
    now: Optional[float] = None,
) -> CleanupReport:
    """Find and optionally remove safe cleanup targets under AMG data."""
    root = Path(data_dir).expanduser().resolve()
    current_time = float(now if now is not None else time.time())
    report = CleanupReport(data_dir=root, dry_run=bool(dry_run))
    report.free_before_bytes = _free_bytes(root)

    specs = [
        ("cloud_submit_fallback", "cloud_submit_fallback", cloud_fallback_older_than),
        ("ui_uploads", "ui_uploads", ui_uploads_older_than),
        ("pod_uploads", "pod_uploads", pod_uploads_older_than),
        ("tmp", "artifact_tmp", artifact_tmp_older_than),
    ]
    for rel, category, age in specs:
        base = root / rel
        report.targets.extend(
            _collect_children(
                base=base,
                root=root,
                category=category,
                older_than_seconds=parse_age_to_seconds(age),
                now=current_time,
            )
        )

    if include_work_dirs:
        report.targets.extend(
            _collect_children(
                base=root / "work_dirs",
                root=root,
                category="work_dirs",
                older_than_seconds=parse_age_to_seconds(work_dirs_older_than),
                now=current_time,
            )
        )

    report.targets.sort(key=lambda t: (t.category, t.path.name))
    if not dry_run:
        for target in report.targets:
            try:
                _delete_target(target.path, root=root)
                report.deleted.append(target)
            except Exception as exc:  # noqa: BLE001 - continue cleanup
                err = {"path": str(target.path), "category": target.category, "error": str(exc)}
                report.errors.append(err)
                log.warn("Disk cleanup failed", **err)

    report.free_after_bytes = _free_bytes(root)
    return report


def _collect_children(
    *,
    base: Path,
    root: Path,
    category: str,
    older_than_seconds: int,
    now: float,
) -> list[CleanupTarget]:
    if not base.exists() or not base.is_dir():
        return []
    targets: list[CleanupTarget] = []
    for child in _safe_children(base, root=root):
        try:
            stat = child.stat()
        except OSError:
            continue
        age = max(0.0, now - float(stat.st_mtime))
        if age < older_than_seconds:
            continue
        targets.append(
            CleanupTarget(
                path=child,
                category=category,
                age_seconds=age,
                size_bytes=_path_size(child),
                reason=f"older_than_{older_than_seconds}s",
            )
        )
    return targets


def _safe_children(base: Path, *, root: Path) -> Iterable[Path]:
    base_resolved = base.resolve()
    if not _path_within(base_resolved, root):
        return []
    return [p for p in base.iterdir() if _path_within(p.resolve(), root)]


def _delete_target(path: Path, *, root: Path) -> None:
    resolved = path.resolve()
    if not _path_within(resolved, root):
        raise ValueError(f"refusing to delete outside AMG data: {path}")
    if resolved == root:
        raise ValueError("refusing to delete AMG data root")
    if resolved.is_dir():
        shutil.rmtree(resolved)
    else:
        resolved.unlink(missing_ok=True)


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _path_size(path: Path) -> int:
    try:
        if path.is_file():
            return int(path.stat().st_size)
        total = 0
        for child in path.rglob("*"):
            try:
                if child.is_file():
                    total += int(child.stat().st_size)
            except OSError:
                continue
        return total
    except OSError:
        return 0


def _free_bytes(path: Path) -> int:
    try:
        probe = path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        return int(shutil.disk_usage(probe).free)
    except Exception:
        return 0


def _target_to_dict(target: CleanupTarget) -> dict:
    return {
        "path": str(target.path),
        "category": target.category,
        "age_seconds": round(float(target.age_seconds), 1),
        "size_bytes": int(target.size_bytes),
        "reason": target.reason,
    }
