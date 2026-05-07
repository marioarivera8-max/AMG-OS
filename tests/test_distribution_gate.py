import json


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_distribution_gate_blocks_incomplete_metadata(monkeypatch, tmp_path):
    import amg.review.distribution_gate as gate

    monkeypatch.setattr(gate, "DECISION_LOGS_DIR", tmp_path / "decision_logs")
    monkeypatch.setattr(gate, "REVIEWED_DIR", tmp_path / "reviewed")
    monkeypatch.setattr(gate, "DISTRIBUTION_STATUS_DIR", tmp_path / "distribution_status")
    monkeypatch.setattr(gate, "PERFORMER_DOCS_DIR", tmp_path / "performer_docs")
    monkeypatch.setattr(gate, "COVER_FLOOR", 15)
    monkeypatch.setattr(
        gate,
        "PLATFORM_REQUIREMENTS",
        {
            "AEBN": {
                "title_max_chars": 60,
                "requires_2257": False,
                "requires_individual_releases": False,
                "banned_terms": [],
                "preferred_resolution_min": (1280, 720),
                "metadata": {
                    "title_min_chars": 30,
                    "description_min_chars": 140,
                    "description_max_chars": 500,
                    "min_tags": 15,
                    "max_tags": 30,
                    "min_categories": 8,
                    "max_categories": 15,
                },
            }
        },
    )

    scene_id = "scene_meta_fail"
    safe = scene_id
    _write_json(
        (tmp_path / "decision_logs" / f"{safe}.json"),
        {
            "scene_id": scene_id,
            "outcomes": {"covers_delivered": 15},
            "input": {"resolution": "1920x1080"},
            "execution": {"error_codes": []},
        },
    )
    _write_json(
        (tmp_path / "reviewed" / f"{safe}.json"),
        {
            "scene_id": scene_id,
            "title_override": "A decent but short title",
            "long_description": "too short",
            "tags_csv": "tag1,tag2",
            "categories_csv": "category1",
            "kept_covers": ["01.jpg"],
            "target_platforms": ["AEBN"],
        },
    )

    result = gate.check_distribution_ready(scene_id, verbose=False)
    aebn = result["per_platform"]["AEBN"]
    assert result["overall_ready"] is False
    assert aebn["ready"] is False
    assert any("Description too short" in b for b in aebn["blockers"])
    assert any("Too few tags" in b for b in aebn["blockers"])
    assert any("Too few categories" in b for b in aebn["blockers"])


def test_distribution_gate_passes_publishable_metadata(monkeypatch, tmp_path):
    import amg.review.distribution_gate as gate

    monkeypatch.setattr(gate, "DECISION_LOGS_DIR", tmp_path / "decision_logs")
    monkeypatch.setattr(gate, "REVIEWED_DIR", tmp_path / "reviewed")
    monkeypatch.setattr(gate, "DISTRIBUTION_STATUS_DIR", tmp_path / "distribution_status")
    monkeypatch.setattr(gate, "PERFORMER_DOCS_DIR", tmp_path / "performer_docs")
    monkeypatch.setattr(gate, "COVER_FLOOR", 15)
    monkeypatch.setattr(
        gate,
        "PLATFORM_REQUIREMENTS",
        {
            "AEBN": {
                "title_max_chars": 80,
                "requires_2257": False,
                "requires_individual_releases": False,
                "banned_terms": [],
                "preferred_resolution_min": (1280, 720),
                "metadata": {
                    "title_min_chars": 20,
                    "description_min_chars": 120,
                    "description_max_chars": 500,
                    "min_tags": 10,
                    "max_tags": 30,
                    "min_categories": 5,
                    "max_categories": 15,
                },
            }
        },
    )

    scene_id = "scene_meta_pass"
    safe = scene_id
    _write_json(
        (tmp_path / "decision_logs" / f"{safe}.json"),
        {
            "scene_id": scene_id,
            "outcomes": {"covers_delivered": 18},
            "input": {"resolution": "1920x1080"},
            "execution": {"error_codes": []},
        },
    )
    _write_json(
        (tmp_path / "reviewed" / f"{safe}.json"),
        {
            "scene_id": scene_id,
            "title_override": "Yasmina Bath Tease and POV Build",
            "long_description": (
                "Yasmina leads a bath tease sequence with sustained POV interaction, "
                "clear action progression, and strong visual continuity across key beats."
            ),
            "tags_csv": ",".join([f"tag{i}" for i in range(1, 13)]),
            "categories_csv": "Category One,Category Two,Category Three,Category Four,Category Five",
            "kept_covers": ["01.jpg", "02.jpg"],
            "target_platforms": ["AEBN"],
        },
    )

    result = gate.check_distribution_ready(scene_id, verbose=False)
    assert result["overall_ready"] is True
    assert result["per_platform"]["AEBN"]["ready"] is True
    assert result["per_platform"]["AEBN"]["blockers"] == []


def test_shared_metadata_validator_marks_skipped_platforms(monkeypatch):
    import amg.review.distribution_gate as gate

    monkeypatch.setattr(
        gate,
        "PLATFORM_REQUIREMENTS",
        {
            "AEBN": {
                "title_max_chars": 80,
                "requires_2257": False,
                "requires_individual_releases": False,
                "banned_terms": [],
                "preferred_resolution_min": (1280, 720),
                "metadata": {
                    "title_min_chars": 20,
                    "description_min_chars": 80,
                    "description_max_chars": 400,
                    "min_tags": 4,
                    "max_tags": 30,
                    "min_categories": 2,
                    "max_categories": 10,
                },
            },
            "SLR": {
                "title_max_chars": 80,
                "requires_2257": False,
                "requires_individual_releases": False,
                "banned_terms": [],
                "preferred_resolution_min": (1280, 720),
                "metadata": {
                    "title_min_chars": 20,
                    "description_min_chars": 80,
                    "description_max_chars": 400,
                    "min_tags": 4,
                    "max_tags": 30,
                    "min_categories": 2,
                    "max_categories": 10,
                },
            },
        },
    )

    out = gate.validate_metadata_for_platforms(
        title_text="A strong title that fits",
        long_description="This description is long enough for the platform checks and clearly explains the scene flow.",
        tags=["pov", "blowjob", "doggy style", "missionary"],
        categories=["POV", "Blowjob"],
        target_platforms=["AEBN"],
    )
    assert out["per_platform"]["AEBN"]["ready"] is True
    assert out["per_platform"]["SLR"]["skipped"] is True
