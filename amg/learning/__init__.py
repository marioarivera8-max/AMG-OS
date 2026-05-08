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
from amg.learning.rule_packs import (
    save_rule_pack,
    load_rule_pack,
    list_rule_packs,
    get_active_rule_pointer,
    set_active_rule_pack,
    deactivate_rule_pack,
    resolve_rule_pack_for_scene,
)
from amg.learning.rule_lab import run_rule_research, generate_candidate_rule_pack
from amg.learning.rule_evaluator import (
    evaluate_rule_pack_kpis,
    format_rule_kpi_report,
    evaluate_rule_promotion_gates,
)
from amg.learning.rule_promotion import (
    run_rule_eval,
    list_rule_eval_runs,
    promote_rule_pack_from_run,
    rollback_active_rule_pack,
    advance_rule_pack_retrieval_stage_from_run,
)
from amg.learning.example_bank import (
    export_approved_example_bank,
    retrieve_top_k_examples,
)
from amg.learning.doc_example_curator import curate_examples_document
from amg.learning.vod_cover_seed_ingest import ingest_vod_cover_seed_zip

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
    "save_rule_pack",
    "load_rule_pack",
    "list_rule_packs",
    "get_active_rule_pointer",
    "set_active_rule_pack",
    "deactivate_rule_pack",
    "resolve_rule_pack_for_scene",
    "run_rule_research",
    "generate_candidate_rule_pack",
    "evaluate_rule_pack_kpis",
    "format_rule_kpi_report",
    "evaluate_rule_promotion_gates",
    "run_rule_eval",
    "list_rule_eval_runs",
    "promote_rule_pack_from_run",
    "rollback_active_rule_pack",
    "advance_rule_pack_retrieval_stage_from_run",
    "export_approved_example_bank",
    "retrieve_top_k_examples",
    "curate_examples_document",
    "ingest_vod_cover_seed_zip",
]
