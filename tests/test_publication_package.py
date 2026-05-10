import json
from pathlib import Path


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_build_publish_package_creates_manifest_and_ledger(monkeypatch, tmp_path):
    import amg.compliance.registry as registry
    import amg.publication.ledger as ledger
    import amg.publication.packages as packages
    import amg.review.distribution_gate as gate

    scene_id = "scene_publishable"
    data_dir = tmp_path / "data"
    decision_dir = data_dir / "decision_logs"
    reviewed_dir = data_dir / "reviewed"
    distribution_dir = data_dir / "distribution_status"
    package_dir = data_dir / "publish_packages"
    publication_dir = data_dir / "publication_records"
    performer_docs_dir = data_dir / "performer_documents"
    work_dir = data_dir / "work_dirs" / scene_id
    covers_dir = work_dir / "covers"
    covers_dir.mkdir(parents=True)
    (covers_dir / "01.jpg").write_bytes(b"cover-one")
    (covers_dir / "02.jpg").write_bytes(b"cover-two")
    (work_dir / "scene_analysis.json").write_text("{}", encoding="utf-8")

    platform_requirements = {
        "AEBN": {
            "title_max_chars": 80,
            "requires_2257": False,
            "requires_individual_releases": False,
            "banned_terms": [],
            "preferred_resolution_min": (1280, 720),
            "metadata": {
                "title_min_chars": 20,
                "description_min_chars": 80,
                "description_max_chars": 500,
                "min_tags": 4,
                "max_tags": 30,
                "min_categories": 2,
                "max_categories": 10,
            },
        }
    }

    for module in (packages, gate):
        monkeypatch.setattr(module, "DECISION_LOGS_DIR", decision_dir)
        monkeypatch.setattr(module, "REVIEWED_DIR", reviewed_dir)
        monkeypatch.setattr(module, "DISTRIBUTION_STATUS_DIR", distribution_dir)
        monkeypatch.setattr(module, "PLATFORM_REQUIREMENTS", platform_requirements)
    monkeypatch.setattr(gate, "PERFORMER_DOCS_DIR", performer_docs_dir)
    monkeypatch.setattr(gate, "COVER_FLOOR", 1)
    monkeypatch.setattr(packages, "DATA_DIR", data_dir)
    monkeypatch.setattr(packages, "PUBLISH_PACKAGES_DIR", package_dir)
    monkeypatch.setattr(ledger, "PUBLICATION_EVENTS_PATH", publication_dir / "events.jsonl")
    monkeypatch.setattr(ledger, "PUBLICATION_STATUS_DIR", publication_dir / "status")
    monkeypatch.setattr(registry, "PERFORMER_DOCS_REGISTRY_PATH", performer_docs_dir / "registry.json")
    monkeypatch.setattr(registry, "PLATFORM_REQUIREMENTS", platform_requirements)

    _write_json(
        decision_dir / f"{scene_id}.json",
        {
            "scene_id": scene_id,
            "scene_path": str(data_dir / "source.mp4"),
            "analysis_path": str(work_dir / "scene_analysis.json"),
            "outcomes": {
                "covers_delivered": 2,
                "saved_covers": [{"filename": "01.jpg"}, {"filename": "02.jpg"}],
            },
            "input": {"resolution": "1920x1080"},
            "execution": {"error_codes": []},
        },
    )
    _write_json(
        reviewed_dir / f"{scene_id}.json",
        {
            "scene_id": scene_id,
            "finalized_thumbnails": True,
            "title_override": "Jane Doe Studio Scene With Strong Retail Appeal",
            "long_description": (
                "Jane Doe leads a clear studio scene with strong visual continuity, "
                "clean action progression, and platform-ready metadata for review."
            ),
            "tags_csv": "pov,studio,solo,feature",
            "categories_csv": "POV,Studio",
            "kept_covers": ["01.jpg", "02.jpg"],
            "cover_pick": {"filename": "01.jpg"},
            "target_platforms": ["AEBN"],
            "performers_confirmed": ["Jane Doe"],
        },
    )

    result = packages.build_publish_package(scene_id, platforms=["AEBN"], operator="tester")

    package_path = result["packages"][0]["package_path"]
    package_zip_path = result["packages"][0]["package_zip_path"]
    manifest_path = result["packages"][0]["manifest_path"]
    assert (package_dir / scene_id / "AEBN").exists()
    assert "AEBN" in package_path

    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    assert manifest["auto_upload"] is False
    assert manifest["human_review_required"] is True
    assert manifest["platform"] == "AEBN"
    assert (Path(package_path) / "metadata.json").exists()
    assert (Path(package_path) / "checksums.sha256").exists()
    assert Path(package_zip_path).exists()

    status = ledger.load_publication_status(scene_id)
    assert status["platforms"]["AEBN"]["status"] == "packaged"
    assert status["platforms"]["AEBN"]["metadata"]["package_zip_path"] == package_zip_path
