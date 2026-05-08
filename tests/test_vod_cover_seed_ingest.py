import json
import zipfile


def test_ingest_vod_cover_seed_zip(tmp_path, monkeypatch):
    import amg.learning.vod_cover_seed_ingest as mod

    train_examples = tmp_path / "training_examples"
    monkeypatch.setattr(mod, "TRAINING_EXAMPLES_DIR", train_examples)
    monkeypatch.setattr(mod, "record_training_artifact", lambda *_, **__: None)

    zip_path = tmp_path / "covers.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("001_Adria Rae1_Hotel Intense Pussy Fuck.jpg", b"x")
        zf.writestr("002_AlexisTae_2_F_Bareback Fuck.jpg", b"x")
        zf.writestr("notes.txt", "ignore")

    stats = mod.ingest_vod_cover_seed_zip(
        zip_path=zip_path,
        dataset_name="unit_vod_seed",
        min_quality=1.0,
        append_to_bank=False,
    )
    assert stats.image_entries == 2
    assert stats.accepted_rows >= 1
    assert stats.accepted_path and stats.accepted_path.exists()
    accepted = [
        json.loads(x)
        for x in stats.accepted_path.read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    assert accepted
    md = accepted[0]["approved_metadata"]
    assert md.get("title")
    assert isinstance(md.get("tags"), list)
