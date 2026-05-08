import json


def test_export_example_bank_and_retrieval(monkeypatch, tmp_path):
    import amg.learning.example_bank as eb

    reviewed_dir = tmp_path / "reviewed"
    logs_dir = tmp_path / "decision_logs"
    feedback_path = tmp_path / "operator_feedback" / "feedback.jsonl"
    train_examples = tmp_path / "training" / "examples"
    train_text = tmp_path / "training" / "text"
    data_dir = tmp_path / "data"
    work_dir = data_dir / "work_dirs" / "scene_a"
    work_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(eb, "REVIEWED_DIR", reviewed_dir)
    monkeypatch.setattr(eb, "DECISION_LOGS_DIR", logs_dir)
    monkeypatch.setattr(eb, "OPERATOR_FEEDBACK_PATH", feedback_path)
    monkeypatch.setattr(eb, "TRAINING_EXAMPLES_DIR", train_examples)
    monkeypatch.setattr(eb, "TRAINING_TEXT_DIR", train_text)
    monkeypatch.setattr(eb, "DATA_DIR", data_dir)
    monkeypatch.setattr(eb, "record_training_artifact", lambda *_, **__: None)

    reviewed_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    (reviewed_dir / "scene_a.json").write_text(
        json.dumps(
            {
                "scene_id": "scene_a",
                "timestamp": "2026-05-07T12:00:00Z",
                "title_override": "Alice POV Bedroom Session",
                "long_description": "Alice leads a POV bedroom sequence with close action framing.",
                "tags_csv": "pov, blowjob, eye contact",
                "categories_csv": "POV, Blowjob, HD Porn",
                "metadata_validation": {"overall_ready": True, "blockers": []},
                "metadata_acceptance_label": "edited",
                "title_edit_distance": 0.11,
                "description_edit_distance": 0.08,
                "per_cover": {"01.jpg": {"pos": "DOGGY"}},
            }
        ),
        encoding="utf-8",
    )
    (logs_dir / "scene_a.json").write_text(
        json.dumps(
            {
                "scene_id": "scene_a",
                "input": {
                    "studio": "StudioX",
                    "scene_type": "STANDARD",
                    "genres": ["POV", "ORAL"],
                    "performers": ["Alice Blue"],
                },
                "outcomes": {
                    "saved_covers": [{"position_label": "DOGGY"}, {"position_label": "MISSIONARY"}]
                },
            }
        ),
        encoding="utf-8",
    )
    (work_dir / "insight.json").write_text(json.dumps({"setting": "bedroom", "mood": "intense"}), encoding="utf-8")
    feedback_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-05-07T12:00:00Z",
                "scene_id": "scene_a",
                "operator": {"decision": "keep"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    stats = eb.export_approved_example_bank(days_back=365)
    assert stats.exported_rows == 1
    assert stats.output_jsonl_path.exists()
    rows = [json.loads(x) for x in stats.output_jsonl_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert rows[0]["scene_features"]["studio"] == "StudioX"
    assert rows[0]["approved_metadata"]["title"] == "Alice POV Bedroom Session"

    got = eb.retrieve_top_k_examples(
        studio="StudioX",
        scene_type="STANDARD",
        genres=["POV"],
        position_summary={"DOGGY": 2},
        performers=["Alice Blue"],
        top_k=1,
        bank_path=stats.output_jsonl_path,
    )
    assert len(got) == 1
    assert got[0]["scene_id"] == "scene_a"


def test_retrieval_prioritizes_published_success_seed(monkeypatch, tmp_path):
    import amg.learning.example_bank as eb

    train_examples = tmp_path / "training" / "examples"
    train_examples.mkdir(parents=True, exist_ok=True)
    bank_path = train_examples / "approved_example_bank.jsonl"
    seed_row = {
        "example_id": "seed:1",
        "scene_id": "seed_scene",
        "source": {"source_type": "vod_cover_seed_zip", "published_success_seed": True},
        "scene_features": {
            "studio": "PUBLIC_LINKS_DRIVE",
            "scene_type": "GANGBANG",
            "genres": ["GANGBANG", "DOUBLE_PENETRATION"],
            "positions": [],
            "performers": ["Nicole Doshi"],
        },
        "approved_metadata": {
            "title": "Naughty Asian Plays With 4 Cocks",
            "long_description": "Published-success seed",
            "tags": ["gangbang", "double penetration", "asian"],
            "categories": ["Gangbang", "Double Penetration", "Asian", "HD Porn"],
        },
        "quality": {"metadata_acceptance_score": 0.1},
    }
    baseline_row = {
        "example_id": "baseline:1",
        "scene_id": "baseline_scene",
        "source": {"source_type": "reviewed"},
        "scene_features": {
            "studio": "PUBLIC_LINKS_DRIVE",
            "scene_type": "GANGBANG",
            "genres": ["GANGBANG", "DOUBLE_PENETRATION"],
            "positions": [],
            "performers": ["Nicole Doshi"],
        },
        "approved_metadata": {
            "title": "Generic Baseline",
            "long_description": "Baseline",
            "tags": ["gangbang"],
            "categories": ["Gangbang", "HD Porn"],
        },
        "quality": {"metadata_acceptance_score": 0.95},
    }
    bank_path.write_text(
        "\n".join([json.dumps(seed_row), json.dumps(baseline_row)]) + "\n",
        encoding="utf-8",
    )

    got = eb.retrieve_top_k_examples(
        studio="PUBLIC_LINKS_DRIVE",
        scene_type="GANGBANG",
        genres=["GANGBANG", "DOUBLE_PENETRATION"],
        position_summary={},
        performers=["Nicole Doshi"],
        top_k=1,
        bank_path=bank_path,
    )
    assert got
    assert got[0]["scene_id"] == "seed_scene"
