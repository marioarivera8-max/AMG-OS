def test_curate_examples_document_accepts_and_rejects(tmp_path, monkeypatch):
    import json
    import amg.learning.doc_example_curator as mod

    train_examples_dir = tmp_path / "training_examples"
    monkeypatch.setattr(mod, "TRAINING_EXAMPLES_DIR", train_examples_dir)
    monkeypatch.setattr(mod, "record_training_artifact", lambda *_, **__: None)

    input_path = tmp_path / "examples.txt"
    input_path.write_text(
        "\n".join(
            [
                "Studio: StudioX",
                "Scene Type: STANDARD",
                "Genres: POV, Oral",
                "Performers: Alice Blue, Brady",
                "Winning Title: Alice POV Bedroom Session with Brady",
                "Winning Long Description: Alice leads a clear POV bedroom sequence with explicit pacing and clean composition for shelf readability.",
                "Winning Tags: pov, oral, blowjob, eye contact, bedroom, deepthroat, closeup, explicit, intense",
                "Winning Categories: POV, Blowjob, HD Porn, Bedroom",
                "",
                "Studio: StudioY",
                "Winning Title: Hot Scene",
                "Winning Long Description: Too short.",
                "Winning Tags: hot, sexy",
                "Winning Categories: Porn",
                "",
                "Studio: StudioX",
                "Scene Type: STANDARD",
                "Genres: POV, Oral",
                "Performers: Alice Blue, Brady",
                "Winning Title: Alice POV Bedroom Session with Brady",
                "Winning Long Description: Alice leads a clear POV bedroom sequence with explicit pacing and clean composition for shelf readability.",
                "Winning Tags: pov, oral, blowjob, eye contact, bedroom, deepthroat, closeup, explicit, intense",
                "Winning Categories: POV, Blowjob, HD Porn, Bedroom",
            ]
        ),
        encoding="utf-8",
    )

    stats = mod.curate_examples_document(
        input_path=input_path,
        dataset_name="unit_doc",
        min_quality=2.8,
        dedupe_threshold=0.8,
        append_to_bank=False,
        dry_run=False,
    )
    assert stats.input_records == 3
    assert stats.accepted_rows == 1
    assert stats.rejected_rows == 2
    assert stats.duplicate_rows == 1
    assert stats.accepted_path and stats.accepted_path.exists()
    assert stats.rejected_path and stats.rejected_path.exists()
    assert stats.summary_path and stats.summary_path.exists()

    accepted_rows = [
        json.loads(line)
        for line in stats.accepted_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(accepted_rows) == 1
    assert accepted_rows[0]["approved_metadata"]["title"] == "Alice POV Bedroom Session with Brady"
