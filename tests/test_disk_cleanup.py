from __future__ import annotations

import os
import importlib.util
import sys
import time
from pathlib import Path


def _touch_old(path: Path, *, age_days: int = 3) -> None:
    old = time.time() - age_days * 86400
    os.utime(path, (old, old))


def test_clean_amg_data_reports_and_deletes_transients(tmp_path):
    from amg.maintenance.disk_cleanup import clean_amg_data

    stale = tmp_path / "cloud_submit_fallback" / "old_job" / "scene.mov"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x" * 10)
    _touch_old(stale)
    _touch_old(stale.parent)

    fresh = tmp_path / "cloud_submit_fallback" / "fresh_job" / "scene.mov"
    fresh.parent.mkdir(parents=True)
    fresh.write_bytes(b"fresh")

    report = clean_amg_data(data_dir=tmp_path, dry_run=True, cloud_fallback_older_than="2d")
    assert [target.path.name for target in report.targets] == ["old_job"]
    assert stale.exists()

    applied = clean_amg_data(data_dir=tmp_path, dry_run=False, cloud_fallback_older_than="2d")
    assert len(applied.deleted) == 1
    assert not stale.parent.exists()
    assert fresh.exists()


def test_work_dirs_are_opt_in(tmp_path):
    from amg.maintenance.disk_cleanup import clean_amg_data

    work = tmp_path / "work_dirs" / "scene_a" / "cover.jpg"
    work.parent.mkdir(parents=True)
    work.write_bytes(b"cover")
    _touch_old(work, age_days=90)
    _touch_old(work.parent, age_days=90)

    report = clean_amg_data(data_dir=tmp_path, dry_run=True, work_dirs_older_than="60d")
    assert not report.targets

    report = clean_amg_data(
        data_dir=tmp_path,
        dry_run=True,
        include_work_dirs=True,
        work_dirs_older_than="60d",
    )
    assert [target.category for target in report.targets] == ["work_dirs"]


def test_clean_amg_data_dry_run_does_not_create_missing_root(tmp_path):
    from amg.maintenance.disk_cleanup import clean_amg_data

    missing = tmp_path / "missing" / "data"
    report = clean_amg_data(data_dir=missing, dry_run=True)

    assert report.targets == []
    assert not missing.exists()


def test_parse_age_to_seconds_accepts_common_units():
    from amg.maintenance.disk_cleanup import parse_age_to_seconds

    assert parse_age_to_seconds("2d") == 172800
    assert parse_age_to_seconds("6h") == 21600
    assert parse_age_to_seconds("30m") == 1800
    assert parse_age_to_seconds("45") == 45


def test_host_maintenance_protects_configured_pod_image(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "controller_disk_maintenance.py"
    spec = importlib.util.spec_from_file_location("controller_disk_maintenance", script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    env_path = tmp_path / "controller.env"
    env_path.write_text(
        "AMG_RUNPOD_IMAGE='ghcr.io/marioarivera8-max/amg-pod:main-abc1234'\n",
        encoding="utf-8",
    )

    assert module._configured_images(env_path) == {
        "ghcr.io/marioarivera8-max/amg-pod:main-abc1234"
    }
