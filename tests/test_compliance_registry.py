def test_compliance_registry_model_release_satisfies_platform(monkeypatch, tmp_path):
    import amg.compliance.registry as registry

    docs_dir = tmp_path / "performer_documents"
    docs_dir.mkdir()
    release_path = docs_dir / "jane_doe_release.pdf"
    release_path.write_bytes(b"release")

    monkeypatch.setattr(registry, "PERFORMER_DOCS_DIR", docs_dir)
    monkeypatch.setattr(registry, "PERFORMER_DOCS_REGISTRY_PATH", docs_dir / "registry.json")
    monkeypatch.setattr(
        registry,
        "PLATFORM_REQUIREMENTS",
        {
            "AEBN": {
                "requires_2257": False,
                "requires_individual_releases": True,
            }
        },
    )

    row = registry.upsert_document("Jane Doe", "model_release", release_path)
    assert row["document_type"] == "model_release"

    performer = registry.performer_status("Jane Doe")
    assert performer["has_model_release"] is True

    status = registry.compliance_status_for_scene(
        "scene_ready",
        performers=["Jane Doe"],
        target_platforms=["AEBN"],
    )
    assert status["overall_ready"] is True
    assert status["per_platform"]["AEBN"]["blockers"] == []


def test_compliance_registry_expired_doc_blocks(monkeypatch, tmp_path):
    import amg.compliance.registry as registry

    docs_dir = tmp_path / "performer_documents"
    docs_dir.mkdir()
    release_path = docs_dir / "jane_doe_release.pdf"
    release_path.write_bytes(b"release")

    monkeypatch.setattr(registry, "PERFORMER_DOCS_REGISTRY_PATH", docs_dir / "registry.json")
    monkeypatch.setattr(
        registry,
        "PLATFORM_REQUIREMENTS",
        {
            "AEBN": {
                "requires_2257": False,
                "requires_individual_releases": True,
            }
        },
    )
    registry.upsert_document("Jane Doe", "model_release", release_path, expiry_date="2000-01-01")

    status = registry.compliance_status_for_scene(
        "scene_blocked",
        performers=["Jane Doe"],
        target_platforms=["AEBN"],
    )
    assert status["overall_ready"] is False
    assert any("Expired model_release" in b for b in status["per_platform"]["AEBN"]["blockers"])
