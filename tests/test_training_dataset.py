import json
import importlib


def test_import_personal_examples_csv(tmp_path, monkeypatch):
    mod = importlib.import_module("amg.learning.import_personal_examples")

    csv_path = tmp_path / "labels.csv"
    csv_path.write_text(
        "scene_id,filename,model_score,operator_score,decision,tags,notes\n"
        "scene_a,frame1.jpg,8.5,92,keep,\"pov,eye contact\",great frame\n"
        "scene_a,,7.0,6.2,reject,,missing filename but scene provided\n"
    )

    out_dir = tmp_path / "examples"
    monkeypatch.setattr(mod, "TRAINING_EXAMPLES_DIR", out_dir)

    stats = mod.import_personal_examples(csv_path, dataset_name="demo")
    assert stats.input_rows == 2
    assert stats.accepted_rows == 2
    assert stats.errors == 0
    assert stats.output_path == out_dir / "demo.jsonl"
    assert stats.output_path.exists()

    rows = [json.loads(line) for line in stats.output_path.read_text().splitlines() if line.strip()]
    assert rows[0]["model_score_100"] == 85.0
    assert rows[0]["operator_score_100"] == 92.0
    assert rows[0]["decision"] == "keep"
    assert rows[0]["tags"] == ["pov", "eye contact"]


def test_build_training_dataset_combines_feedback_and_examples(tmp_path, monkeypatch):
    mod = importlib.import_module("amg.learning.dataset_builder")
    reg = importlib.import_module("amg.learning.training_registry")

    feedback_path = tmp_path / "feedback.jsonl"
    feedback_path.write_text(
        json.dumps(
            {
                "scene_id": "scene_fb",
                "filename": "fb_01.jpg",
                "operator": {"decision": "keep", "score_100": 88, "position_label": "POV"},
                "model": {"score_100": 74},
            }
        )
        + "\n"
    )

    examples_dir = tmp_path / "examples"
    examples_dir.mkdir()
    (examples_dir / "manual.jsonl").write_text(
        json.dumps(
            {
                "scene_id": "scene_manual",
                "filename": "m_01.jpg",
                "decision": "reject",
                "operator_score_100": 34,
            }
        )
        + "\n"
    )

    out_dir = tmp_path / "datasets"
    monkeypatch.setattr(mod, "OPERATOR_FEEDBACK_PATH", feedback_path)
    monkeypatch.setattr(mod, "TRAINING_EXAMPLES_DIR", examples_dir)
    monkeypatch.setattr(mod, "TRAINING_DATASETS_DIR", out_dir)
    monkeypatch.setattr(reg, "TRAINING_REGISTRY_PATH", tmp_path / "registry.jsonl")

    stats = mod.build_training_dataset(dataset_name="scoring_v1", val_pct=0.2, test_pct=0.2)
    assert stats.total_rows == 2
    assert (stats.output_dir / "all.jsonl").exists()
    assert (stats.output_dir / "train.jsonl").exists()
    assert (stats.output_dir / "val.jsonl").exists()
    assert (stats.output_dir / "test.jsonl").exists()

    manifest = json.loads((stats.output_dir / "manifest.json").read_text())
    assert manifest["dataset_name"] == "scoring_v1"
    assert manifest["total_rows"] == 2


def test_import_personal_examples_xlsx_with_hyperlinks(tmp_path, monkeypatch):
    import openpyxl

    mod = importlib.import_module("amg.learning.import_personal_examples")

    xlsx_path = tmp_path / "tracking.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Yasmina & Brady"
    ws.append(["Scene Number", "Link", "Scene Name", "Description", "Talent", "Genre", "Adult Empire", "Notes"])
    ws.append(["12", "Source", "Bath Tease POV", "Strong eye contact in POV framing", "Yazmina", "POV,Blowjob", "2026-05-01", "Top performer"])
    ws["B2"].hyperlink = "https://mega.nz/file/example"
    wb.save(xlsx_path)

    out_dir = tmp_path / "examples"
    monkeypatch.setattr(mod, "TRAINING_EXAMPLES_DIR", out_dir)

    stats = mod.import_personal_examples(xlsx_path, dataset_name="workbook")
    assert stats.input_rows == 1
    assert stats.accepted_rows == 1
    assert stats.skipped_rows == 0
    assert stats.errors == 0
    assert stats.output_path.exists()

    rows = [json.loads(line) for line in stats.output_path.read_text().splitlines() if line.strip()]
    row = rows[0]
    assert row["title"] == "Bath Tease POV"
    assert row["description"] == "Strong eye contact in POV framing"
    assert row["performers"] == ["Yazmina"]
    assert row["categories"] == ["POV", "Blowjob"]
    assert row["decision"] == "keep"
    assert row["source_sheet"] == "Yasmina & Brady"
    assert "https://mega.nz/file/example" in row["source_links"]


def test_export_text_training_dataset(tmp_path, monkeypatch):
    mod = importlib.import_module("amg.learning.text_style_export")
    reg = importlib.import_module("amg.learning.training_registry")

    datasets_dir = tmp_path / "datasets"
    text_dir = tmp_path / "text"
    ds = datasets_dir / "scoring_selection_v1"
    ds.mkdir(parents=True)
    (ds / "all.jsonl").write_text(
        json.dumps(
            {
                "sample_id": "s1",
                "source_type": "personal_example",
                "scene_id": "scene_x",
                "title": "Yazmina POV Blowjob",
                "description": "Strong lens engagement with explicit action.",
                "studio": "Yasmina & Brady",
                "performers": ["Yazmina", "Brady"],
                "label": {"categories": ["POV"], "tags": ["eye contact", "oral closeup"]},
            }
        )
        + "\n"
        + json.dumps({"sample_id": "s2", "source_type": "feedback"})
        + "\n"
    )

    monkeypatch.setattr(mod, "TRAINING_DATASETS_DIR", datasets_dir)
    monkeypatch.setattr(mod, "TRAINING_TEXT_DIR", text_dir)
    monkeypatch.setattr(reg, "TRAINING_REGISTRY_PATH", tmp_path / "registry.jsonl")

    stats = mod.export_text_training_dataset(dataset_name="scoring_selection_v1", split="all")
    assert stats.input_rows == 2
    assert stats.exported_rows == 1
    assert stats.skipped_rows == 1
    assert stats.output_path.exists()

    lines = [json.loads(x) for x in stats.output_path.read_text().splitlines() if x.strip()]
    assert len(lines) == 1
    assert lines[0]["task"] == "title_description_tags"
    assert lines[0]["target"]["title"] == "Yazmina POV Blowjob"


def test_export_text_training_messages_format(tmp_path, monkeypatch):
    mod = importlib.import_module("amg.learning.text_style_export")
    reg = importlib.import_module("amg.learning.training_registry")

    datasets_dir = tmp_path / "datasets"
    text_dir = tmp_path / "text"
    ds = datasets_dir / "scoring_selection_v1"
    ds.mkdir(parents=True)
    (ds / "all.jsonl").write_text(
        json.dumps(
            {
                "sample_id": "s1",
                "source_type": "personal_example",
                "scene_id": "scene_x",
                "title": "Yazmina POV Blowjob",
                "description": "Strong lens engagement with explicit action.",
                "studio": "Yasmina & Brady",
                "performers": ["Yazmina", "Brady"],
                "label": {"categories": ["POV"], "tags": ["eye contact", "oral closeup"]},
            }
        )
        + "\n"
    )

    monkeypatch.setattr(mod, "TRAINING_DATASETS_DIR", datasets_dir)
    monkeypatch.setattr(mod, "TRAINING_TEXT_DIR", text_dir)
    monkeypatch.setattr(reg, "TRAINING_REGISTRY_PATH", tmp_path / "registry.jsonl")

    stats = mod.export_text_training_dataset(
        dataset_name="scoring_selection_v1",
        split="all",
        format_type="messages",
    )
    assert stats.exported_rows == 1
    lines = [json.loads(x) for x in stats.output_path.read_text().splitlines() if x.strip()]
    assert "messages" in lines[0]
    assert lines[0]["messages"][0]["role"] == "system"
    assert lines[0]["messages"][2]["role"] == "assistant"


def test_export_text_training_bundle(tmp_path, monkeypatch):
    mod = importlib.import_module("amg.learning.text_style_export")
    reg = importlib.import_module("amg.learning.training_registry")

    datasets_dir = tmp_path / "datasets"
    text_dir = tmp_path / "text"
    ds = datasets_dir / "scoring_selection_v1"
    ds.mkdir(parents=True)
    for split in ("train", "val", "test", "all"):
        (ds / f"{split}.jsonl").write_text(
            json.dumps(
                {
                    "sample_id": f"s_{split}",
                    "source_type": "personal_example",
                    "scene_id": "scene_x",
                    "title": "Title",
                    "description": "Desc",
                    "label": {"categories": ["POV"], "tags": ["eye contact"]},
                }
            )
            + "\n"
        )

    monkeypatch.setattr(mod, "TRAINING_DATASETS_DIR", datasets_dir)
    monkeypatch.setattr(mod, "TRAINING_TEXT_DIR", text_dir)
    monkeypatch.setattr(reg, "TRAINING_REGISTRY_PATH", tmp_path / "registry.jsonl")

    bundle = mod.export_text_training_bundle(
        dataset_name="scoring_selection_v1",
        splits=("train", "val", "test", "all"),
        format_type="instruction",
    )
    assert bundle.totals["input_rows"] == 4
    assert bundle.totals["exported_rows"] == 4
    assert bundle.manifest_path.exists()
    manifest = json.loads(bundle.manifest_path.read_text())
    assert manifest["format_type"] == "instruction"


def test_training_registry_summary(tmp_path, monkeypatch):
    reg = importlib.import_module("amg.learning.training_registry")
    monkeypatch.setattr(reg, "TRAINING_REGISTRY_PATH", tmp_path / "registry.jsonl")

    reg.record_training_artifact("dataset_build", tmp_path / "d.json", {"rows": 10})
    reg.record_training_artifact("text_export", tmp_path / "t.jsonl", {"rows": 5})
    summary = reg.summarize_training_registry(limit=20)
    assert summary["total_rows"] == 2
    assert summary["by_type"]["dataset_build"] == 1
    assert summary["by_type"]["text_export"] == 1


def test_eval_text_dataset_metrics(tmp_path, monkeypatch):
    mod = importlib.import_module("amg.learning.eval_harness")
    datasets_dir = tmp_path / "datasets"
    ds = datasets_dir / "scoring_selection_v1"
    ds.mkdir(parents=True)
    (ds / "val.jsonl").write_text(
        json.dumps(
            {
                "title": "Yazmina POV Blowjob",
                "description": "Strong lens engagement.",
                "performers": ["Yazmina", "Brady"],
                "label": {"categories": ["POV"], "tags": ["eye contact"]},
            }
        )
        + "\n"
        + json.dumps({"title": "", "description": "", "performers": [], "label": {}})
        + "\n"
    )
    monkeypatch.setattr(mod, "TRAINING_DATASETS_DIR", datasets_dir)
    metrics = mod.evaluate_text_dataset(dataset_name="scoring_selection_v1", split="val")
    assert metrics["total_rows"] == 2
    assert metrics["title_rate"] == 50.0
    assert metrics["performer_in_title_rate"] == 100.0


def test_export_scoring_training_dataset(tmp_path, monkeypatch):
    mod = importlib.import_module("amg.learning.scoring_training_export")
    reg = importlib.import_module("amg.learning.training_registry")

    datasets_dir = tmp_path / "datasets"
    scoring_dir = tmp_path / "scoring"
    ds = datasets_dir / "scoring_selection_v1"
    ds.mkdir(parents=True)
    (ds / "all.jsonl").write_text(
        json.dumps(
            {
                "sample_id": "a",
                "scene_id": "scene1",
                "filename": "f1.jpg",
                "label": {"decision": "keep", "score_100": 90},
            }
        )
        + "\n"
        + json.dumps(
            {
                "sample_id": "b",
                "scene_id": "scene1",
                "filename": "f2.jpg",
                "label": {"decision": "reject", "score_100": 30},
            }
        )
        + "\n"
    )
    monkeypatch.setattr(mod, "TRAINING_DATASETS_DIR", datasets_dir)
    monkeypatch.setattr(mod, "TRAINING_SCORING_DIR", scoring_dir)
    monkeypatch.setattr(reg, "TRAINING_REGISTRY_PATH", tmp_path / "registry.jsonl")

    stats = mod.export_scoring_training_dataset(dataset_name="scoring_selection_v1", split="all")
    assert stats.input_rows == 2
    assert stats.point_rows == 2
    assert stats.pair_rows >= 1
    assert stats.points_path.exists()
    assert stats.pairs_path.exists()


def test_eval_scoring_dataset_metrics(tmp_path, monkeypatch):
    mod = importlib.import_module("amg.learning.scoring_training_export")

    datasets_dir = tmp_path / "datasets"
    ds = datasets_dir / "scoring_selection_v1"
    ds.mkdir(parents=True)
    (ds / "val.jsonl").write_text(
        json.dumps(
            {
                "scene_id": "s1",
                "label": {"decision": "keep", "score_100": 88},
                "model": {"score_100": 70},
            }
        )
        + "\n"
        + json.dumps(
            {
                "scene_id": "s1",
                "label": {"decision": "reject", "score_100": 42},
                "model": {"score_100": 52},
            }
        )
        + "\n"
    )
    monkeypatch.setattr(mod, "TRAINING_DATASETS_DIR", datasets_dir)
    metrics = mod.evaluate_scoring_dataset(dataset_name="scoring_selection_v1", split="val")
    assert metrics["total_rows"] == 2
    assert metrics["decision_labeled_rows"] == 2
    assert metrics["ranking_pairs_possible"] >= 1
    assert metrics["model_score_mae"] == 14.0


def test_export_scoring_training_dataset_pair_cap(tmp_path, monkeypatch):
    mod = importlib.import_module("amg.learning.scoring_training_export")
    reg = importlib.import_module("amg.learning.training_registry")

    datasets_dir = tmp_path / "datasets"
    scoring_dir = tmp_path / "scoring"
    ds = datasets_dir / "scoring_selection_v1"
    ds.mkdir(parents=True)
    rows = []
    for i in range(6):
        rows.append(
            {
                "sample_id": f"s{i}",
                "scene_id": "scene_cap",
                "filename": f"f{i}.jpg",
                "label": {"decision": "keep" if i % 2 == 0 else "reject", "score_100": 80 - i},
            }
        )
    (ds / "all.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    monkeypatch.setattr(mod, "TRAINING_DATASETS_DIR", datasets_dir)
    monkeypatch.setattr(mod, "TRAINING_SCORING_DIR", scoring_dir)
    monkeypatch.setattr(reg, "TRAINING_REGISTRY_PATH", tmp_path / "registry.jsonl")

    stats = mod.export_scoring_training_dataset(
        dataset_name="scoring_selection_v1",
        split="all",
        max_pairs_per_scene=3,
    )
    assert stats.point_rows == 6
    assert stats.pair_rows <= 3
