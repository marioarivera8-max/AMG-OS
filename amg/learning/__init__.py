"""Learning: capture, analyze, calibrate from historical decision logs."""
from amg.learning.recorder import record_scene_outcome
from amg.learning.analyzer import analyze_logs, generate_report
from amg.learning.calibrator import recalibrate_studio
from amg.learning.import_personal_examples import import_personal_examples
from amg.learning.dataset_builder import build_training_dataset
from amg.learning.text_style_export import export_text_training_dataset, export_text_training_bundle
from amg.learning.training_registry import read_training_registry, summarize_training_registry
from amg.learning.eval_harness import evaluate_text_dataset, format_text_eval
from amg.learning.scoring_training_export import (
    export_scoring_training_dataset,
    evaluate_scoring_dataset,
    format_scoring_eval,
)
from amg.learning.timing_calibration import (
    calibrate_phase_thresholds,
    format_thresholds_markdown_table,
    update_cheat_sheet_thresholds,
)
from amg.learning.retrain_scoring import (
    append_retrain_flag,
    read_retrain_flags,
    run_scoring_retrain,
    evaluate_retrain_gates,
    list_retrain_runs,
    promote_score_candidate,
)

__all__ = [
    "record_scene_outcome",
    "analyze_logs",
    "generate_report",
    "recalibrate_studio",
    "import_personal_examples",
    "build_training_dataset",
    "export_text_training_dataset",
    "export_text_training_bundle",
    "read_training_registry",
    "summarize_training_registry",
    "evaluate_text_dataset",
    "format_text_eval",
    "export_scoring_training_dataset",
    "evaluate_scoring_dataset",
    "format_scoring_eval",
    "calibrate_phase_thresholds",
    "format_thresholds_markdown_table",
    "update_cheat_sheet_thresholds",
    "append_retrain_flag",
    "read_retrain_flags",
    "run_scoring_retrain",
    "evaluate_retrain_gates",
    "list_retrain_runs",
    "promote_score_candidate",
]
