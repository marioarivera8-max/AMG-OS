#!/usr/bin/env python3
"""Host-side disk maintenance for the AMG controller VM.

Run this on the Hetzner controller host, not inside the web container. It can
prune old AMG Docker image tags and AMG transient data. It never removes the
image used by a running container, and it defaults to dry-run mode.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


AMG_REPOS = (
    "ghcr.io/marioarivera8-max/amg-controller",
    "ghcr.io/marioarivera8-max/amg-pod",
)


@dataclass(frozen=True)
class ImageTag:
    repo: str
    tag: str
    image_id: str

    @property
    def ref(self) -> str:
        return f"{self.repo}:{self.tag}"


def main() -> int:
    parser = argparse.ArgumentParser(description="AMG controller host disk maintenance")
    parser.add_argument("--data-dir", type=Path, default=Path("/var/lib/amg/data"))
    parser.add_argument("--controller-env", type=Path, default=Path("/etc/amg/controller.env"))
    parser.add_argument("--keep-docker-tags", type=int, default=2)
    parser.add_argument("--protect-image", action="append", default=[])
    parser.add_argument("--cloud-fallback-older-than", default="2d")
    parser.add_argument("--ui-uploads-older-than", default="14d")
    parser.add_argument("--pod-uploads-older-than", default="2d")
    parser.add_argument("--artifact-tmp-older-than", default="6h")
    parser.add_argument("--include-work-dirs", action="store_true")
    parser.add_argument("--work-dirs-older-than", default="60d")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    dry_run = not args.apply
    protected_images = _running_images()
    protected_images.update(_configured_images(args.controller_env))
    protected_images.update(args.protect_image or [])
    image_targets = _old_amg_images(keep_tags=max(1, args.keep_docker_tags), protected=protected_images)

    data_report = _run_data_cleanup(args, dry_run=dry_run)

    removed_images: list[str] = []
    image_errors: list[dict] = []
    if not dry_run:
        for image in image_targets:
            try:
                subprocess.run(["docker", "image", "rm", image.ref], check=True)
                removed_images.append(image.ref)
            except Exception as exc:  # noqa: BLE001
                image_errors.append({"image": image.ref, "error": str(exc)})

    report = {
        "dry_run": dry_run,
        "disk": _disk_report(Path("/")),
        "docker": {
            "protected": sorted(protected_images),
            "targets": [img.ref for img in image_targets],
            "removed": removed_images,
            "errors": image_errors,
        },
        "amg_data": data_report,
    }
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_report(report)
    return 0 if not image_errors and not data_report.get("errors") else 1


def _run_data_cleanup(args: argparse.Namespace, *, dry_run: bool) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from amg.maintenance.disk_cleanup import clean_amg_data

    return clean_amg_data(
        data_dir=args.data_dir,
        dry_run=dry_run,
        cloud_fallback_older_than=args.cloud_fallback_older_than,
        ui_uploads_older_than=args.ui_uploads_older_than,
        pod_uploads_older_than=args.pod_uploads_older_than,
        artifact_tmp_older_than=args.artifact_tmp_older_than,
        include_work_dirs=args.include_work_dirs,
        work_dirs_older_than=args.work_dirs_older_than,
    ).to_dict()


def _old_amg_images(*, keep_tags: int, protected: set[str]) -> list[ImageTag]:
    targets: list[ImageTag] = []
    for repo in AMG_REPOS:
        rows = _docker_image_rows(repo)
        kept = 0
        for image in rows:
            if image.ref in protected:
                kept += 1
                continue
            if kept < keep_tags:
                kept += 1
                continue
            targets.append(image)
    return targets


def _docker_image_rows(repo: str) -> list[ImageTag]:
    try:
        proc = subprocess.run(
            ["docker", "image", "ls", repo, "--format", "{{.Repository}}\t{{.Tag}}\t{{.ID}}"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception:
        return []
    rows = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        image = ImageTag(repo=parts[0], tag=parts[1], image_id=parts[2])
        if image.tag and image.tag != "<none>":
            rows.append(image)
    return rows


def _running_images() -> set[str]:
    try:
        proc = subprocess.run(
            ["docker", "ps", "--format", "{{.Image}}"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception:
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def _configured_images(controller_env: Path) -> set[str]:
    images: set[str] = set()
    values = _read_env_file(controller_env)
    for key in ("AMG_RUNPOD_IMAGE",):
        value = values.get(key, "").strip()
        if value:
            images.add(value)
    return images


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = _strip_env_quotes(os.path.expandvars(value.strip()))
    return values


def _strip_env_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _disk_report(path: Path) -> dict:
    usage = shutil.disk_usage(path)
    return {
        "path": str(path),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_pct": round(usage.used / max(1, usage.total) * 100.0, 1),
    }


def _print_report(report: dict) -> None:
    mode = "dry-run" if report.get("dry_run") else "apply"
    disk = report.get("disk") or {}
    docker = report.get("docker") or {}
    data = report.get("amg_data") or {}
    print("=" * 72)
    print(f"AMG controller disk maintenance ({mode})")
    print("=" * 72)
    print(
        f"Root disk: {disk.get('used_pct')}% used, "
        f"{_fmt_bytes(disk.get('free_bytes', 0))} free"
    )
    print(f"Docker image targets: {len(docker.get('targets') or [])}")
    for ref in (docker.get("targets") or [])[:20]:
        print(f"  - {ref}")
    if len(docker.get("targets") or []) > 20:
        print(f"  ... {len(docker.get('targets') or []) - 20} more")
    print(f"AMG data targets: {data.get('targets_count', 0)}")
    print(f"AMG data reclaimable: {_fmt_bytes(data.get('reclaimable_bytes', 0))}")
    print("=" * 72)


def _fmt_bytes(num: int) -> str:
    value = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


if __name__ == "__main__":
    raise SystemExit(main())
