import json
import importlib
from argparse import Namespace
from types import SimpleNamespace


def test_run_scoring_retrain_writes_manifest_and_blocks(monkeypatch, tmp_path):
    mod = importlib.import_module("amg.learning.retrain_scoring")
    reg = importlib.import_module("amg.learning.training_registry")

    monkeypatch.setattr(mod, "TRAINING_FLAGS_PATH", tmp_path / "flags.jsonl")
    monkeypatch.setattr(mod, "TRAINING_RETRAIN_RUNS_DIR", tmp_path / "retrain_runs")
    monkeypatch.setattr(mod, "TRAINING_ACTIVE_SCORER_PATH", tmp_path / "active_scorer.json")
    monkeypatch.setattr(reg, "TRAINING_REGISTRY_PATH", tmp_path / "registry.jsonl")

    monkeypatch.setattr(
        mod,
        "build_training_dataset",
        lambda dataset_name, val_pct, test_pct: SimpleNamespace(
            total_rows=120, train_rows=96, val_rows=12, test_rows=12, output_dir=tmp_path / "datasets" / dataset_name
        ),
    )
    monkeypatch.setattr(
        mod,
        "export_scoring_training_dataset",
        lambda dataset_name, split, output_prefix, max_pairs_per_scene: SimpleNamespace(
            input_rows=120,
            point_rows=90,
            pair_rows=25,
            points_path=tmp_path / "points.jsonl",
            pairs_path=tmp_path / "pairs.jsonl",
        ),
    )
    monkeypatch.setattr(
        mod,
        "evaluate_scoring_dataset",
        lambda dataset_name, split: {
            "decision_labeled_rows": 20,
            "keep_rows": 10,
            "maybe_rows": 0,
            "reject_rows": 10,
            "ranking_pairs_possible": 30,
            "model_score_mae": None,
        },
    )

    result = mod.run_scoring_retrain(from_scene_id="scene_1", note="best run")
    assert result.manifest_path.exists()
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["input_flag"]["scene_id"] == "scene_1"
    assert manifest["candidate_ready"] is False
    assert "model_score_mae_missing" in manifest["gates"]["blocked_reasons"]


def test_promote_score_candidate_writes_active_pointer(monkeypatch, tmp_path):
    mod = importlib.import_module("amg.learning.retrain_scoring")
    reg = importlib.import_module("amg.learning.training_registry")

    monkeypatch.setattr(mod, "TRAINING_RETRAIN_RUNS_DIR", tmp_path / "retrain_runs")
    monkeypatch.setattr(mod, "TRAINING_ACTIVE_SCORER_PATH", tmp_path / "active_scorer.json")
    monkeypatch.setattr(reg, "TRAINING_REGISTRY_PATH", tmp_path / "registry.jsonl")

    run_dir = mod.TRAINING_RETRAIN_RUNS_DIR / "20260505_010000"
    run_dir.mkdir(parents=True)
    manifest = {
        "run_id": "20260505_010000",
        "dataset_name": "scoring_retrain_20260505_010000",
        "candidate_ready": True,
        "export": {"points_path": "p.jsonl", "pairs_path": "q.jsonl"},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest))

    pointer = mod.promote_score_candidate("20260505_010000")
    assert pointer["run_id"] == "20260505_010000"
    assert mod.TRAINING_ACTIVE_SCORER_PATH.exists()


def test_cmd_retrain_status_prints_runs(monkeypatch, capsys):
    cli = importlib.import_module("amg.cli")
    monkeypatch.setattr(cli, "init_logging", lambda: None)
    monkeypatch.setattr(
        "amg.learning.retrain_scoring.list_retrain_runs",
        lambda limit: [{"run_id": "r1", "candidate_ready": True, "dataset_name": "d1", "gates": {"blocked_reasons": []}}],
    )
    code = cli.cmd_retrain_status(Namespace(limit=5))
    out = capsys.readouterr().out
    assert code == 0
    assert "r1" in out
