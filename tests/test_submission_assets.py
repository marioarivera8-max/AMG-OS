from pathlib import Path

from PIL import Image


def _big_file(path: Path, size: int = 1024 * 1024 + 1) -> None:
    path.write_bytes(b"0" * size)


def test_submission_asset_scanner_harvests_docs_images_and_secondary_videos(tmp_path, monkeypatch):
    import amg.ingest.submission_assets as sa

    root = tmp_path / "submission"
    root.mkdir()
    selected = root / "main_scene.mp4"
    secondary = root / "bonus_scene.mp4"
    _big_file(selected)
    _big_file(secondary)
    (root / "Jane_Doe_2257.pdf").write_bytes(b"doc")
    (root / "Jane_Doe_model_release.pdf").write_bytes(b"release")
    Image.new("RGB", (80, 60), color=(255, 0, 0)).save(root / "actor_thumb.png")
    (root / "actor_alt.heic").write_bytes(b"not-real-heic")
    work_dir = tmp_path / "work"

    monkeypatch.setattr(
        sa,
        "get_metadata",
        lambda p: {"duration_sec": 120 if Path(p).name == "main_scene.mp4" else 30, "width": 1920, "height": 1080},
    )

    manifest = sa.scan_submission_assets(
        scene_id="scene_a",
        selected_video=selected,
        submission_root=root,
        work_dir=work_dir,
        source_mode="local",
    )

    assert (work_dir / "submission_manifest.json").exists()
    assert manifest["selected_video"]["filename"] == "main_scene.mp4"
    assert [v["filename"] for v in manifest["secondary_videos"]] == ["bonus_scene.mp4"]
    assert {d["document_type"] for d in manifest["compliance_docs"]} >= {"2257", "model_release"}
    assert len(manifest["provided_images"]) == 2
    assert any(img["preview_path"] and Path(img["preview_path"]).exists() for img in manifest["provided_images"])
    assert any("pillow-heif" in w or "normalize" in w.lower() for w in manifest["warnings"])


def test_cloud_file_only_manifest_warns_folder_assets_unavailable(tmp_path, monkeypatch):
    import amg.ingest.submission_assets as sa

    selected = tmp_path / "scene.mp4"
    _big_file(selected)
    work_dir = tmp_path / "work"
    monkeypatch.setattr(sa, "get_metadata", lambda _p: {"duration_sec": 120, "width": 1280, "height": 720})

    manifest = sa.scan_submission_assets(
        scene_id="scene_cloud",
        selected_video=selected,
        submission_root=tmp_path,
        work_dir=work_dir,
        source_mode="cloud",
        cloud_source={"remote": "gdrive_amy", "path": "incoming/scene.mp4"},
    )

    assert any("file-only" in w for w in manifest["warnings"])
    assert manifest["cloud_source"] == {"remote": "gdrive_amy", "path": "incoming/scene.mp4"}
