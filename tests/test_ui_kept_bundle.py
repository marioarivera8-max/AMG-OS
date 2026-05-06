import zipfile
from pathlib import Path


def test_build_kept_covers_package_creates_folder_and_zip(tmp_path):
    from amg.ui.app import _build_kept_covers_package

    work_dir = tmp_path / "scene_amg_v11"
    covers_dir = work_dir / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)
    a = covers_dir / "01_a.jpg"
    b = covers_dir / "02_b.jpg"
    a.write_bytes(b"a")
    b.write_bytes(b"b")

    cover_items = [
        {"filename": "01_a.jpg", "path": a},
        {"filename": "02_b.jpg", "path": b},
    ]

    out = _build_kept_covers_package(
        scene_id="demo_scene",
        work_dir=work_dir,
        cover_items=cover_items,
        kept_filenames=["02_b.jpg", "01_a.jpg"],
    )

    assert out["kept_count"] == 2
    kept_folder = out["kept_folder_path"]
    kept_zip = out["kept_zip_path"]
    assert kept_folder is not None and Path(kept_folder).exists()
    assert kept_zip is not None and Path(kept_zip).exists()

    copied = sorted([p.name for p in Path(kept_folder).glob("*.jpg")])
    assert copied == ["01_02_b.jpg", "02_01_a.jpg"]

    with zipfile.ZipFile(kept_zip, "r") as zf:
        zipped = sorted(zf.namelist())
    assert zipped == copied

