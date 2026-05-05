#!/usr/bin/env python3
"""
AMG OS CLI — v11.1.

Commands:
    amg process <path>          Process a single scene
    amg batch <path>            Process all scenes in a folder
    amg review <scene>          Open human review form for processed scene
    amg ready <scene>           Distribution-ready check
    amg find [filters]          Search scene library
    amg dvd-compile <s1>...     Compile DVD from 4+ scenes
    amg dashboard               Performance trends + recent activity
    amg resume <scene>          Resume a failed scene
    amg status                  Show recent activity
    amg analyze [--days N]      Performance analysis
    amg verify                  Installation health check
    amg version                 Show version + Ollama status
    amg performers              List performer registry
    amg calibrate <studio>      Recalibrate studio thresholds
    amg clean [--older-than]    Clean old work directories
    amg ui                      Start local web UI (run + review)
    amg feedback-eval           Compare model labels vs operator corrections
    amg import-personal-examples <file>  Import manual labels (CSV/JSONL/XLSX)
    amg build-training-dataset  Build canonical train/val/test dataset
    amg export-text-training    Export dataset split to text-training JSONL
    amg export-scoring-training Export scoring points/pairs for ranking model
    amg training-status         Show training artifact registry summary
    amg eval-text-dataset       Evaluate text dataset quality metrics
    amg eval-scoring-dataset    Evaluate scoring dataset quality metrics
    amg timing-calibrate        Calibrate phase timing thresholds from run logs
    amg retrain-score           Build/evaluate scoring retrain candidate
    amg retrain-status          Show recent scoring retrain runs
    amg promote-score-candidate Promote a passed scoring retrain candidate
    amg user <add|passwd|list|disable|enable|delete>  Manage UI auth users
    amg pod-worker              Run pod-side service (cloud edition; pod CMD)
"""
import argparse
import os
import sys
import time
from pathlib import Path
from datetime import datetime

# Allow running from source without install
sys.path.insert(0, str(Path(__file__).parent.parent))

from amg.__version__ import __version__
from amg.config import (
    BATCH_LOCK_FILE,
    DATA_DIR,
    DECISION_LOGS_DIR,
    REQUIRED_ENV_VARS,
    DEFAULT_OPERATOR,
    DEFAULT_MACHINE_ID,
    MIN_FREE_SPACE_GB,
)
from amg.utils.logging import init_logging
from amg.utils.timing import format_duration
from amg.utils.error_form import format_error_report, format_partial_success_report


def main():
    parser = argparse.ArgumentParser(
        prog="amg",
        description="AMG OS — Adult VOD scene processor (v11.1)",
    )
    parser.add_argument("--version", action="version", version=f"AMG OS {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=False)

    # process
    p_process = subparsers.add_parser("process", help="Process a single scene")
    p_process.add_argument("path", type=Path, help="Path to scene folder or video file")
    p_process.add_argument("--dry-run", action="store_true", help="Don't save outputs")

    # batch
    p_batch = subparsers.add_parser("batch", help="Process all scenes in a folder")
    p_batch.add_argument("path", type=Path, help="Folder containing scenes")
    p_batch.add_argument("--dry-run", action="store_true")
    p_batch.add_argument("--force", action="store_true", help="Ignore batch lock")

    # review
    p_review = subparsers.add_parser("review", help="Human review form for a scene")
    p_review.add_argument("scene_id", type=str, help="Scene ID (folder name)")

    # ready
    p_ready = subparsers.add_parser("ready", help="Distribution-ready check")
    p_ready.add_argument("scene_id", type=str)

    # find
    p_find = subparsers.add_parser("find", help="Search scene library")
    p_find.add_argument("--genre", type=str, default=None)
    p_find.add_argument("--performer", type=str, default=None)
    p_find.add_argument("--studio", type=str, default=None)
    p_find.add_argument("--scene-type", type=str, default=None, dest="scene_type")
    p_find.add_argument("--min-score", type=float, default=None, dest="min_score")
    p_find.add_argument("--processed-after", type=str, default=None, dest="processed_after")
    p_find.add_argument("--reviewed", action="store_true", default=None)
    p_find.add_argument("--not-reviewed", action="store_true")
    p_find.add_argument("--ready-for", type=str, default=None, dest="ready_for")
    p_find.add_argument("--limit", type=int, default=50)

    # dvd-compile
    p_dvd = subparsers.add_parser("dvd-compile", help="Compile DVD from 2+ scenes")
    p_dvd.add_argument("scene_ids", nargs="+", help="Scene IDs to compile")
    p_dvd.add_argument("--theme", type=str, required=True, help="Theme name")
    p_dvd.add_argument("--output", type=Path, default=Path("./DVD_Output"))

    # dashboard
    p_dash = subparsers.add_parser("dashboard", help="Performance dashboard")
    p_dash.add_argument("--days", type=int, default=30)

    # resume
    p_resume = subparsers.add_parser("resume", help="Resume failed scene")
    p_resume.add_argument("scene_id", type=str)

    # status
    subparsers.add_parser("status", help="Show recent activity")

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="Performance analysis")
    p_analyze.add_argument("--days", type=int, default=30)
    p_analyze.add_argument("--studio", type=str, default=None)

    # verify, version, performers
    subparsers.add_parser("verify", help="Installation health check")
    subparsers.add_parser("version", help="Show version + system status")
    subparsers.add_parser("performers", help="List performer registry")

    # calibrate
    p_cal = subparsers.add_parser("calibrate", help="Recalibrate studio thresholds")
    p_cal.add_argument("studio", type=str)

    # clean
    p_clean = subparsers.add_parser("clean", help="Clean old work directories")
    p_clean.add_argument("--older-than", type=str, default="30d")
    p_clean.add_argument("--dry-run", action="store_true")
    p_clean.add_argument("--auto", action="store_true")

    # ui
    p_ui = subparsers.add_parser("ui", help="Start local web UI (run + review)")
    p_ui.add_argument("--host", type=str, default="127.0.0.1")
    p_ui.add_argument("--port", type=int, default=8080)
    p_ui.add_argument("--no-open", action="store_true", help="Do not open browser automatically")
    p_ui.add_argument(
        "--auth",
        action="store_true",
        help="Force auth on (login required). Auto-on when --host is non-localhost.",
    )

    # feedback-eval
    p_fe = subparsers.add_parser("feedback-eval", help="Compare model labels vs operator corrections")
    p_fe.add_argument("--scene-id", type=str, default=None)
    p_fe.add_argument("--studio", type=str, default=None)

    # import-personal-examples
    p_imp = subparsers.add_parser(
        "import-personal-examples",
        help="Import manually labeled examples (CSV/JSONL/XLSX) into training examples store",
    )
    p_imp.add_argument("input_path", type=Path, help="Path to CSV, JSONL, or XLSX file")
    p_imp.add_argument("--dataset-name", type=str, default="personal_examples")
    p_imp.add_argument("--dry-run", action="store_true")

    # build-training-dataset
    p_btd = subparsers.add_parser(
        "build-training-dataset",
        help="Build canonical train/val/test dataset from feedback + imported examples",
    )
    p_btd.add_argument("--dataset-name", type=str, default="scoring_v1")
    p_btd.add_argument("--val-pct", type=float, default=0.10)
    p_btd.add_argument("--test-pct", type=float, default=0.10)

    # export-text-training
    p_ett = subparsers.add_parser(
        "export-text-training",
        help="Export canonical dataset split to text-training JSONL pairs",
    )
    p_ett.add_argument("--dataset-name", type=str, default="scoring_selection_v1")
    p_ett.add_argument("--split", type=str, default="all", help="Dataset split: all/train/val/test")
    p_ett.add_argument("--all-splits", action="store_true", help="Export train/val/test/all and write bundle manifest")
    p_ett.add_argument("--format", type=str, default="instruction", choices=["instruction", "messages"])
    p_ett.add_argument("--output-name", type=str, default=None, help="Optional output filename")

    # export-scoring-training
    p_est = subparsers.add_parser(
        "export-scoring-training",
        help="Export canonical dataset split to scoring point/pair training JSONL",
    )
    p_est.add_argument("--dataset-name", type=str, default="scoring_selection_v1")
    p_est.add_argument("--split", type=str, default="all")
    p_est.add_argument("--output-prefix", type=str, default=None)
    p_est.add_argument(
        "--max-pairs-per-scene",
        type=int,
        default=0,
        help="Optional cap for ranking pairs per scene (0 = no cap)",
    )

    # training-status
    p_ts = subparsers.add_parser(
        "training-status",
        help="Show summary of training artifacts and latest outputs",
    )
    p_ts.add_argument("--limit", type=int, default=200, help="How many latest registry rows to inspect")

    # eval-text-dataset
    p_etd = subparsers.add_parser(
        "eval-text-dataset",
        help="Evaluate text dataset quality (coverage, performer mention, diversity)",
    )
    p_etd.add_argument("--dataset-name", type=str, default="scoring_selection_v1")
    p_etd.add_argument("--split", type=str, default="val")

    # eval-scoring-dataset
    p_esd = subparsers.add_parser(
        "eval-scoring-dataset",
        help="Evaluate scoring dataset quality (labels, pair potential, MAE)",
    )
    p_esd.add_argument("--dataset-name", type=str, default="scoring_selection_v1")
    p_esd.add_argument("--split", type=str, default="val")

    # generate-title
    p_gt = subparsers.add_parser(
        "generate-title",
        help="Re-run AI scene insight + title/description generation against existing covers",
    )
    p_gt.add_argument("scene", type=str, help="Scene ID (folder name) or path to a video / scene folder")
    p_gt.add_argument("--print-only", action="store_true", help="Print results, do not update insight.json")

    # timing-calibrate
    p_tc = subparsers.add_parser(
        "timing-calibrate",
        help="Calibrate phase timing thresholds from UI run timing ledger",
    )
    p_tc.add_argument(
        "--run-timings-path",
        type=Path,
        default=DATA_DIR / "logs" / "run_timings.jsonl",
        help="Path to run timings JSONL",
    )
    p_tc.add_argument(
        "--min-samples",
        type=int,
        default=5,
        help="Minimum rows per phase to include in thresholds",
    )
    p_tc.add_argument(
        "--doc-path",
        type=Path,
        default=Path("docs/phase_timing_cheat_sheet.md"),
        help="Cheat-sheet markdown path",
    )
    p_tc.add_argument(
        "--update-doc",
        action="store_true",
        help="Write auto-calibrated threshold table into the cheat sheet",
    )

    # retrain-score
    p_rs = subparsers.add_parser(
        "retrain-score",
        help="Run manual scoring retrain pipeline from current feedback/examples",
    )
    p_rs.add_argument("--from-flag", type=str, default=None, help="Scene ID to flag and include in retrain trigger")
    p_rs.add_argument("--tag", type=str, default="golden_positive", choices=["golden_positive", "golden_negative"])
    p_rs.add_argument("--note", type=str, default=None)
    p_rs.add_argument("--dataset-prefix", type=str, default="scoring_retrain")
    p_rs.add_argument("--max-pairs-per-scene", type=int, default=300)

    # retrain-status
    p_rstat = subparsers.add_parser(
        "retrain-status",
        help="Show recent scoring retrain run manifests",
    )
    p_rstat.add_argument("--limit", type=int, default=10)

    # promote-score-candidate
    p_psc = subparsers.add_parser(
        "promote-score-candidate",
        help="Promote a passed scoring retrain candidate by run ID",
    )
    p_psc.add_argument("--run-id", type=str, required=True)

    # user (auth admin)
    p_user = subparsers.add_parser(
        "user",
        help="Manage UI auth users (add, passwd, list, disable, enable, delete)",
    )
    user_sub = p_user.add_subparsers(dest="user_cmd", required=True)

    p_user_add = user_sub.add_parser("add", help="Create a new UI user")
    p_user_add.add_argument("username", type=str)
    p_user_add.add_argument(
        "--role",
        type=str,
        default="operator",
        choices=["operator", "reviewer", "admin"],
    )
    p_user_add.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read password from stdin (for scripted setup) instead of prompting",
    )

    p_user_pw = user_sub.add_parser("passwd", help="Change a user's password")
    p_user_pw.add_argument("username", type=str)
    p_user_pw.add_argument("--password-stdin", action="store_true")

    user_sub.add_parser("list", help="List all UI users")

    p_user_dis = user_sub.add_parser("disable", help="Disable a user (login refused)")
    p_user_dis.add_argument("username", type=str)

    p_user_en = user_sub.add_parser("enable", help="Re-enable a previously disabled user")
    p_user_en.add_argument("username", type=str)

    p_user_del = user_sub.add_parser("delete", help="Permanently delete a user")
    p_user_del.add_argument("username", type=str)
    p_user_del.add_argument("--yes", action="store_true", help="Skip confirmation prompt")

    # pod-worker (cloud edition: pod-side FastAPI service)
    p_pw = subparsers.add_parser(
        "pod-worker",
        help="Run the pod-side worker (cloud edition; usually the pod's CMD)",
    )
    p_pw.add_argument("--host", type=str, default="0.0.0.0")
    p_pw.add_argument("--port", type=int, default=8000)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    # Dispatch
    try:
        return _dispatch(args)
    except KeyboardInterrupt:
        print("\nAborted by user.")
        _release_batch_lock()
        return 130


def _dispatch(args):
    cmd = args.command
    if cmd == "process":   return cmd_process(args)
    if cmd == "batch":     return cmd_batch(args)
    if cmd == "review":    return cmd_review(args)
    if cmd == "ready":     return cmd_ready(args)
    if cmd == "find":      return cmd_find(args)
    if cmd == "dvd-compile": return cmd_dvd_compile(args)
    if cmd == "dashboard": return cmd_dashboard(args)
    if cmd == "resume":    return cmd_resume(args)
    if cmd == "status":    return cmd_status(args)
    if cmd == "analyze":   return cmd_analyze(args)
    if cmd == "verify":    return cmd_verify(args)
    if cmd == "version":   return cmd_version(args)
    if cmd == "performers": return cmd_performers(args)
    if cmd == "calibrate": return cmd_calibrate(args)
    if cmd == "clean":     return cmd_clean(args)
    if cmd == "ui":        return cmd_ui(args)
    if cmd == "feedback-eval": return cmd_feedback_eval(args)
    if cmd == "import-personal-examples": return cmd_import_personal_examples(args)
    if cmd == "build-training-dataset": return cmd_build_training_dataset(args)
    if cmd == "export-text-training": return cmd_export_text_training(args)
    if cmd == "export-scoring-training": return cmd_export_scoring_training(args)
    if cmd == "training-status": return cmd_training_status(args)
    if cmd == "eval-text-dataset": return cmd_eval_text_dataset(args)
    if cmd == "eval-scoring-dataset": return cmd_eval_scoring_dataset(args)
    if cmd == "generate-title": return cmd_generate_title(args)
    if cmd == "timing-calibrate": return cmd_timing_calibrate(args)
    if cmd == "retrain-score": return cmd_retrain_score(args)
    if cmd == "retrain-status": return cmd_retrain_status(args)
    if cmd == "promote-score-candidate": return cmd_promote_score_candidate(args)
    if cmd == "user":      return cmd_user(args)
    if cmd == "pod-worker": return cmd_pod_worker(args)
    return 1


# ============================================================
# COMMAND IMPLEMENTATIONS
# ============================================================

def cmd_process(args):
    """Process a single scene."""
    from amg.pipeline import process_scene
    from amg.ingest.inventory import discover_scenes
    from amg.video.metadata import get_metadata

    init_logging()
    path = Path(args.path).resolve()

    if path.is_dir():
        videos = discover_scenes(path, recursive=True)
        if not videos:
            print(f"No video files found in {path}")
            return 1
        # Pick longest by duration (fallback: largest size) so generic camera
        # dumps with many files consistently select the primary scene.
        best = None
        best_dur = -1.0
        best_size = -1
        for v in videos:
            meta = get_metadata(v)
            dur = float((meta or {}).get("duration_sec", 0) or 0)
            try:
                size = v.stat().st_size
            except Exception:
                size = -1
            if dur > best_dur or (dur == best_dur and size > best_size):
                best = v
                best_dur = dur
                best_size = size
        video_path = best or videos[0]
        if len(videos) > 1:
            chosen_dur = f"{best_dur:.1f}s" if best_dur > 0 else "unknown duration"
            print(f"Multiple videos found; selected longest: {video_path.name} ({chosen_dur})")
    else:
        video_path = path

    if not video_path.exists():
        print(f"Path does not exist: {video_path}")
        return 1

    result = process_scene(video_path, dry_run=args.dry_run)

    # v11.1.1: three outcomes — full success (silent), partial (warning report),
    # hard failure (error report). Previously, partial runs printed the full error
    # report with "Unknown error" because error_codes was empty.
    if result.get("success") and result.get("partial_success"):
        report = format_partial_success_report(
            scene_id=result.get("scene_id", "unknown"),
            covers_delivered=result.get("covers_saved", 0),
            expected=15,  # COVER_FLOOR
            fallbacks_used=result.get("fallbacks_used", []),
            warnings=result.get("warnings", []),
        )
        print(report)
    elif not result.get("success"):
        report = format_error_report(
            scene_id=result.get("scene_id", "unknown"),
            error_codes=result.get("error_codes", []),
            duration_sec=result.get("total_duration_sec", 0),
            partial_covers=result.get("covers_saved", 0),
            work_dir=str(result.get("work_dir", "")),
        )
        print(report)

    return 0 if result["success"] else 1


def cmd_batch(args):
    """Process all scenes in a folder."""
    from amg.pipeline import process_scene
    from amg.ingest.inventory import discover_scenes
    from amg.learning.batch_tracker import write_batch_summary

    init_logging()

    if not args.force and not _acquire_batch_lock():
        return 1

    try:
        path = Path(args.path).resolve()
        videos = discover_scenes(path, recursive=True)

        if not videos:
            print(f"No video files found in {path}")
            return 1

        print(f"Found {len(videos)} scenes to process")
        print()

        batch_start = time.time()
        results = {"completed": [], "warnings": [], "aborted": []}

        for i, video in enumerate(videos, 1):
            print(f"\n[{i}/{len(videos)}] {video.parent.name}")
            try:
                result = process_scene(video, dry_run=args.dry_run)
            except Exception as e:
                print(f"  ✗ Pipeline crash: {e}")
                results["aborted"].append({"path": video, "error": str(e),
                                           "scene_id": video.parent.name})
                continue

            if result["success"]:
                if result.get("warnings"):
                    results["warnings"].append(result)
                else:
                    results["completed"].append(result)
            else:
                results["aborted"].append(result)
                # Show readable error report
                report = format_error_report(
                    scene_id=result.get("scene_id", "unknown"),
                    error_codes=result.get("error_codes", []),
                    duration_sec=result.get("total_duration_sec", 0),
                    partial_covers=result.get("covers_saved", 0),
                    work_dir=str(result.get("work_dir", "")),
                )
                print(report)

        batch_duration = time.time() - batch_start

        # v11.1: write batch summary for dashboard
        try:
            write_batch_summary(
                batch_results=results,
                batch_duration_sec=batch_duration,
                scenes_attempted=len(videos),
            )
        except Exception as e:
            print(f"(Note: batch summary not written: {e})")

        _print_batch_summary(results, batch_duration, len(videos))

    finally:
        _release_batch_lock()

    return 0 if len(results["aborted"]) == 0 else 1


def cmd_review(args):
    """Open human review form."""
    from amg.review.form import open_review_form
    init_logging()
    result = open_review_form(args.scene_id)
    return 0 if result else 1


def cmd_ready(args):
    """Distribution-ready check."""
    from amg.review.distribution_gate import check_distribution_ready
    init_logging()
    result = check_distribution_ready(args.scene_id, verbose=True)
    return 0 if result["overall_ready"] else 1


def cmd_find(args):
    """Search scene library."""
    from amg.library.search import find_scenes, format_search_results
    init_logging()

    reviewed = None
    if args.reviewed:
        reviewed = True
    if args.not_reviewed:
        reviewed = False

    results = find_scenes(
        genre=args.genre,
        performer=args.performer,
        studio=args.studio,
        scene_type=args.scene_type,
        min_score=args.min_score,
        processed_after=args.processed_after,
        reviewed=reviewed,
        ready_for=args.ready_for,
        limit=args.limit,
    )
    print(format_search_results(results))
    return 0


def cmd_dvd_compile(args):
    """Compile DVD from scenes."""
    from amg.library.dvd_compile import compile_dvd
    init_logging()

    print(f"Compiling DVD: theme='{args.theme}'")
    print(f"Scenes: {', '.join(args.scene_ids)}")
    print()

    result = compile_dvd(
        scene_ids=args.scene_ids,
        theme=args.theme,
        output_dir=args.output,
        operator=DEFAULT_OPERATOR,
    )

    if not result.get("success"):
        print(f"\n✗ DVD compilation failed: {result.get('error', 'unknown error')}")
        if result.get("spec_issues"):
            print("\nSpec issues:")
            for issue in result["spec_issues"]:
                print(f"  ✗ {issue}")
        return 1

    print()
    print("=" * 67)
    print(f"  DVD COMPILED: {result['dvd_id']}")
    print("=" * 67)
    print(f"  Theme:           {args.theme}")
    print(f"  Total duration:  {format_duration(result['total_duration_sec'])}")
    print(f"  Output:          {result['output_path']}")
    print(f"  Case art:        {result['case_art_path']}")
    print(f"  Metadata:        {result['metadata_path']}")
    if result.get("spec_issues"):
        print()
        print("  ⚠ Spec issues encountered:")
        for issue in result["spec_issues"]:
            print(f"    - {issue}")
    print("=" * 67)
    return 0


def cmd_dashboard(args):
    """Performance dashboard."""
    from amg.learning.dashboard import render_dashboard
    init_logging()
    print(render_dashboard(days=args.days))
    return 0


def cmd_resume(args):
    """Resume a failed scene (currently just re-runs it; future: state recovery)."""
    from amg.pipeline import process_scene
    init_logging()

    # Find the scene by ID
    print(f"Resuming: {args.scene_id}")
    print("(Note: v11.1 resume re-runs from scratch; full state recovery in v11.2)")

    from amg.config import INCOMING_ROOTS
    search_roots = list(INCOMING_ROOTS)

    scene_path = None
    for root in search_roots:
        if not root.exists():
            continue
        for path in root.rglob(f"*{args.scene_id[:40]}*"):
            if path.is_dir():
                from amg.ingest.inventory import discover_scenes
                videos = discover_scenes(path, recursive=False)
                if videos:
                    scene_path = videos[0]
                    break
        if scene_path:
            break

    if not scene_path:
        print(f"Could not locate scene: {args.scene_id}")
        return 1

    result = process_scene(scene_path)
    return 0 if result["success"] else 1


def cmd_status(args):
    """Show recent activity status."""
    from amg.scoring.ai_client import AIClient

    init_logging()
    print("=" * 64)
    print(f"AMG OS v{__version__} Status")
    print("=" * 64)
    print()
    print("System:")
    print(f"  Version:        {__version__}")
    print(f"  Operator:       {DEFAULT_OPERATOR}")
    print(f"  Machine:        {DEFAULT_MACHINE_ID}")

    client = AIClient()
    if client.is_alive():
        if client.is_model_loaded():
            print(f"  Ollama:         ✓ running, qwen2.5-vl:7b loaded")
        else:
            print(f"  Ollama:         ⚠ running but model NOT loaded")
    else:
        print(f"  Ollama:         ✗ NOT responding")

    try:
        import shutil
        free_gb = shutil.disk_usage(DATA_DIR).free / (1024 ** 3)
        print(f"  Disk free:      {free_gb:.1f} GB")
    except Exception:
        pass

    print()
    print("Recent activity (last 24h):")
    if DECISION_LOGS_DIR.exists():
        cutoff = time.time() - 86400
        recent = [
            p for p in DECISION_LOGS_DIR.glob("*.json")
            if p.stat().st_mtime > cutoff
        ]
        print(f"  Scenes processed: {len(recent)}")
    else:
        print("  Scenes processed: 0")

    print()
    print("Quick commands:")
    print("  amg dashboard            Full performance dashboard")
    print("  amg analyze              Performance trends")
    print("  amg batch /path/         Process incoming folder")
    print("  amg find --genre <tag>   Search library")
    print("=" * 64)
    return 0


def cmd_analyze(args):
    """Performance analysis from decision logs."""
    from amg.learning.analyzer import analyze_logs, generate_report

    init_logging()
    analysis = analyze_logs(days_back=args.days, studio=args.studio)
    print(generate_report(analysis))
    return 0


def cmd_verify(args):
    """Installation health check."""
    from amg.scoring.ai_client import AIClient

    init_logging()
    print("=" * 64)
    print("AMG OS v11.1 Installation Health Check")
    print("=" * 64)

    checks = []
    client = AIClient()

    checks.append(("Ollama running", client.is_alive()))
    checks.append(("Vision model loaded", client.is_model_loaded()))

    for var, expected in REQUIRED_ENV_VARS.items():
        actual = os.environ.get(var)
        checks.append((f"{var}={expected}", actual == expected))

    for pkg in ["cv2", "PIL", "numpy", "imagehash", "requests"]:
        try:
            __import__(pkg)
            checks.append((f"Python: {pkg}", True))
        except ImportError:
            checks.append((f"Python: {pkg}", False))

    # v11.1.3: PyAV is the primary decode backend (replaces decord/OpenCV
    # seek-and-decode pattern with linear stream decode). Required, not optional.
    try:
        import av as _av
        checks.append((f"Python: av (PyAV {_av.__version__})", True))
        # Try opening a hwaccel option to detect whether VideoToolbox is at
        # least accepted by ffmpeg (true HW engagement isn't reliably probeable
        # in PyAV 13.1 — speedup may come from linear decode pattern alone).
        import platform
        if platform.system() == "Darwin" and platform.machine() == "arm64":
            checks.append(("VideoToolbox: option supported (HW engagement not probeable in PyAV 13.1)", None))
    except ImportError:
        checks.append(("Python: av (PyAV) MISSING — install with: pip install av==13.1.0", False))

    try:
        import shutil
        free_gb = shutil.disk_usage(DATA_DIR).free / (1024 ** 3)
        checks.append((f"Disk free: {free_gb:.1f} GB", free_gb > MIN_FREE_SPACE_GB))
    except Exception:
        checks.append(("Disk space check", False))

    print()
    all_pass = True
    for label, status in checks:
        if status is True:
            print(f"  ✓ {label}")
        elif status is False:
            print(f"  ✗ {label}")
            all_pass = False
        else:
            print(f"  - {label}")

    print()
    if all_pass:
        print("All systems operational.")
    else:
        print("Some checks failed. Run ./scripts/setup.sh to fix.")
    print("=" * 64)
    return 0 if all_pass else 1


def cmd_version(args):
    """Show version + system info."""
    from amg.scoring.ai_client import AIClient

    print(f"AMG OS v{__version__}")
    client = AIClient()
    if client.is_alive():
        if client.is_model_loaded():
            print("Ollama: running, qwen2.5-vl:7b loaded")
        else:
            print("Ollama: running but model not loaded")
    else:
        print("Ollama: not responding")
    return 0


def cmd_performers(args):
    """List performer registry."""
    init_logging()
    registry_path = DATA_DIR / "performers" / "registry.json"
    if not registry_path.exists():
        print("No performers registered yet. Process some scenes first.")
        return 0

    import json
    with open(registry_path) as f:
        data = json.load(f)
    performers = data.get("performers", {})

    print(f"Performer Registry ({len(performers)} entries)")
    print("=" * 64)
    for pid, info in sorted(performers.items()):
        print(f"  {info.get('canonical_name', pid):<25} "
              f"{info.get('scenes_count', 0):3d} scenes  "
              f"avg top {info.get('avg_top_pick_score', 0):.1f}")
    return 0


def cmd_calibrate(args):
    """Recalibrate a studio's thresholds."""
    from amg.learning.calibrator import recalibrate_studio

    init_logging()
    result = recalibrate_studio(args.studio)
    print(f"Studio: {args.studio}")
    print(f"Scenes used: {result['scenes_used']}")
    if result["success"]:
        print(f"Old floor: {result['old_floor']}")
        print(f"New floor: {result['new_floor']:.1f}")
        print(f"Change: {result.get('delta_pct', 0):+.1f}%")
    print(f"\n{result['recommendation']}")
    return 0 if result["success"] else 1


def cmd_clean(args):
    """Clean old work directories."""
    init_logging()
    age_str = args.older_than
    if age_str.endswith("d"):
        days = int(age_str[:-1])
    else:
        print(f"Invalid age format: {age_str}. Use like '30d' or '7d'.")
        return 1

    print(f"Looking for work directories older than {days} days...")
    print("Cleanup feature coming in v11.1.1")
    return 0


def cmd_ui(args):
    """Start local FastAPI UI (run + review)."""
    init_logging()
    try:
        import uvicorn
    except ImportError:
        print("Missing UI dependency: uvicorn")
        print("Install dependencies: pip install fastapi uvicorn jinja2 python-multipart")
        return 1

    # Auth policy:
    # - Localhost bind (127.0.0.1, localhost, ::1): default to auth OFF for the
    #   single-user Mac workflow. Operator can opt back in with --auth.
    # - Any other bind (0.0.0.0, LAN IP, public IP): auth REQUIRED. The UI is
    #   reachable from another device, so refusing to start without a session
    #   secret is the safe default. Operator can override with
    #   AMG_AUTH_DISABLED=1 (e.g. for a one-off LAN demo).
    is_local_bind = args.host in {"127.0.0.1", "localhost", "::1"}
    user_set_auth_disabled = "AMG_AUTH_DISABLED" in os.environ
    if not user_set_auth_disabled:
        if is_local_bind and not args.auth:
            os.environ["AMG_AUTH_DISABLED"] = "1"
        elif not is_local_bind and not args.auth:
            # Force auth on for non-localhost binds; operator can still disable
            # via explicit env var if they know what they're doing.
            os.environ.setdefault("AMG_AUTH_DISABLED", "0")
    if args.auth:
        os.environ["AMG_AUTH_DISABLED"] = "0"

    auth_on = os.environ.get("AMG_AUTH_DISABLED", "").lower() not in {"1", "true", "yes"}
    if auth_on and not os.environ.get("AMG_SESSION_SECRET", "").strip():
        print("Auth is enabled but AMG_SESSION_SECRET is not set.")
        print("Generate one and re-run:")
        print("  export AMG_SESSION_SECRET=\"$(python -c 'import secrets; print(secrets.token_urlsafe(48))')\"")
        if not is_local_bind:
            print(f"(Auth was auto-enabled because --host is {args.host}; pass --host 127.0.0.1 for local-only use.)")
        return 1

    try:
        from amg.ui.app import create_app
    except ImportError as e:
        print(f"Could not load UI app: {e}")
        print("Install dependencies: pip install fastapi uvicorn jinja2 python-multipart argon2-cffi itsdangerous")
        return 1

    url = f"http://{args.host}:{args.port}"
    print(f"Starting AMG UI at {url}")
    if auth_on:
        from amg.ui import auth as _auth
        print(f"Auth: ON (users registered: {_auth.count_users()})")
        if _auth.count_users() == 0:
            print("  No users yet. Create one with: amg user add <username>")
    else:
        print("Auth: OFF (localhost-only single-user mode)")
    print("Press Ctrl+C to stop.")

    if not args.no_open:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass

    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def cmd_feedback_eval(args):
    """Compare model classification outputs against operator corrections."""
    from amg.learning.feedback_eval import evaluate_feedback, format_feedback_report

    init_logging()
    metrics = evaluate_feedback(scene_id=args.scene_id, studio=args.studio)
    print(format_feedback_report(metrics))
    return 0


def cmd_import_personal_examples(args):
    """Import manually labeled training examples from CSV/JSONL/XLSX."""
    from amg.learning.import_personal_examples import import_personal_examples

    init_logging()
    input_path = Path(args.input_path).expanduser().resolve()
    if not input_path.exists():
        print(f"Input file not found: {input_path}")
        return 1

    stats = import_personal_examples(
        input_path=input_path,
        dataset_name=args.dataset_name,
        dry_run=bool(args.dry_run),
    )
    print("Import personal examples")
    print("=" * 64)
    print(f"Input rows: {stats.input_rows}")
    print(f"Accepted: {stats.accepted_rows}")
    print(f"Skipped: {stats.skipped_rows}")
    print(f"Deduped: {stats.deduped_rows}")
    print(f"Errors: {stats.errors}")
    if args.dry_run:
        print("Dry-run: no files written")
    elif stats.output_path:
        print(f"Output: {stats.output_path}")
    return 0 if stats.accepted_rows > 0 else 1


def cmd_build_training_dataset(args):
    """Build canonical train/val/test dataset from current learning signals."""
    from amg.learning.dataset_builder import build_training_dataset

    init_logging()
    val_pct = float(args.val_pct)
    test_pct = float(args.test_pct)
    if val_pct < 0 or test_pct < 0 or (val_pct + test_pct) >= 0.9:
        print("Invalid split values. Use non-negative values with val_pct + test_pct < 0.9")
        return 1

    stats = build_training_dataset(
        dataset_name=args.dataset_name,
        val_pct=val_pct,
        test_pct=test_pct,
    )
    print("Build training dataset")
    print("=" * 64)
    print(f"Total rows: {stats.total_rows}")
    print(f"Train: {stats.train_rows}")
    print(f"Val: {stats.val_rows}")
    print(f"Test: {stats.test_rows}")
    print(f"Output dir: {stats.output_dir}")
    return 0 if stats.total_rows > 0 else 1


def cmd_export_text_training(args):
    """Export dataset split into text-training JSONL examples."""
    from amg.learning.text_style_export import export_text_training_bundle, export_text_training_dataset

    init_logging()
    if bool(args.all_splits):
        try:
            bundle = export_text_training_bundle(
                dataset_name=args.dataset_name,
                splits=("train", "val", "test", "all"),
                format_type=args.format,
            )
        except FileNotFoundError as e:
            print(str(e))
            return 1
        except ValueError as e:
            print(str(e))
            return 1

        print("Export text training bundle")
        print("=" * 64)
        print(f"Format: {bundle.format_type}")
        for split, out in bundle.outputs.items():
            print(f"{split}: {out}")
        print(
            "Totals: "
            f"input={bundle.totals.get('input_rows', 0)} "
            f"exported={bundle.totals.get('exported_rows', 0)} "
            f"skipped={bundle.totals.get('skipped_rows', 0)}"
        )
        print(f"Manifest: {bundle.manifest_path}")
        return 0 if bundle.totals.get("exported_rows", 0) > 0 else 1

    try:
        stats = export_text_training_dataset(
            dataset_name=args.dataset_name,
            split=args.split,
            output_name=args.output_name,
            format_type=args.format,
        )
    except FileNotFoundError as e:
        print(str(e))
        return 1
    except ValueError as e:
        print(str(e))
        return 1

    print("Export text training dataset")
    print("=" * 64)
    print(f"Format: {args.format}")
    print(f"Input rows: {stats.input_rows}")
    print(f"Exported: {stats.exported_rows}")
    print(f"Skipped: {stats.skipped_rows}")
    print(f"Output: {stats.output_path}")
    return 0 if stats.exported_rows > 0 else 1


def cmd_export_scoring_training(args):
    """Export scoring/ranking training artifacts from canonical split."""
    from amg.learning.scoring_training_export import export_scoring_training_dataset

    init_logging()
    try:
        stats = export_scoring_training_dataset(
            dataset_name=args.dataset_name,
            split=args.split,
            output_prefix=args.output_prefix,
            max_pairs_per_scene=(args.max_pairs_per_scene or None),
        )
    except FileNotFoundError as e:
        print(str(e))
        return 1

    print("Export scoring training dataset")
    print("=" * 64)
    print(f"Input rows: {stats.input_rows}")
    print(f"Point rows: {stats.point_rows}")
    print(f"Pair rows: {stats.pair_rows}")
    print(f"Points output: {stats.points_path}")
    print(f"Pairs output: {stats.pairs_path}")
    return 0 if stats.point_rows > 0 else 1


def cmd_training_status(args):
    """Show summary of locally tracked training artifacts."""
    from amg.learning.training_registry import summarize_training_registry

    init_logging()
    summary = summarize_training_registry(limit=int(args.limit))
    print("Training artifact status")
    print("=" * 64)
    print(f"Rows scanned: {summary.get('total_rows', 0)}")
    by_type = summary.get("by_type", {}) or {}
    if by_type:
        print("Counts by artifact type:")
        for k in sorted(by_type):
            print(f"  - {k}: {by_type[k]}")
    else:
        print("No registry entries yet.")

    latest = summary.get("latest_existing_by_type", {}) or summary.get("latest_by_type", {}) or {}
    if latest:
        print("\nLatest by type:")
        for k in sorted(latest):
            row = latest[k]
            print(f"  - {k}: {row.get('artifact_path')} ({row.get('ts_utc')})")
    return 0


def cmd_eval_text_dataset(args):
    """Evaluate text dataset quality metrics for a split."""
    from amg.learning.eval_harness import evaluate_text_dataset, format_text_eval

    init_logging()
    try:
        metrics = evaluate_text_dataset(dataset_name=args.dataset_name, split=args.split)
    except FileNotFoundError as e:
        print(str(e))
        return 1
    print(format_text_eval(metrics))
    return 0


def cmd_eval_scoring_dataset(args):
    """Evaluate scoring dataset quality metrics for a split."""
    from amg.learning.scoring_training_export import evaluate_scoring_dataset, format_scoring_eval

    init_logging()
    try:
        metrics = evaluate_scoring_dataset(dataset_name=args.dataset_name, split=args.split)
    except FileNotFoundError as e:
        print(str(e))
        return 1
    print(format_scoring_eval(metrics))
    return 0


def cmd_generate_title(args):
    """Re-run vision insight + AI title/description generation for an existing scene."""
    import json
    from amg.ingest.inventory import discover_scenes, make_work_dir
    from amg.scoring.insight_pipeline import generate_scene_insight_payload

    init_logging()

    # Resolve scene to a video file + decision log.
    arg = args.scene
    target = Path(arg).expanduser()
    decision_log = None
    if target.exists():
        if target.is_dir():
            videos = discover_scenes(target, recursive=False)
            if not videos:
                print(f"No video files found in {target}")
                return 1
            video_path = videos[0]
        else:
            video_path = target
    else:
        # Treat arg as a scene_id; look up decision log.
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in arg)[:120]
        log_path = DECISION_LOGS_DIR / f"{safe}.json"
        if not log_path.exists():
            print(f"No scene found matching: {arg}")
            return 1
        with open(log_path) as f:
            decision_log = json.load(f)
        scene_path = decision_log.get("scene_path")
        if not scene_path:
            print(f"Decision log missing scene_path: {log_path}")
            return 1
        video_path = Path(scene_path)

    work_dir = make_work_dir(video_path, version="v11")
    if not work_dir.exists():
        print(f"Work dir does not exist (process the scene first): {work_dir}")
        return 1

    saved_covers = []
    contact_sheet = None
    if decision_log is None:
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in video_path.parent.name)[:120]
        dl_path = DECISION_LOGS_DIR / f"{safe}.json"
        if dl_path.exists():
            with open(dl_path) as f:
                decision_log = json.load(f)
    if decision_log:
        saved_covers = (decision_log.get("outcomes") or {}).get("saved_covers", []) or []
    sheets = sorted(work_dir.glob("00_*_contact_sheet.jpg"))
    if sheets:
        contact_sheet = sheets[0]

    if not saved_covers and not contact_sheet:
        print(f"No covers or contact sheet in {work_dir}")
        return 1

    out_payload = generate_scene_insight_payload(
        video_path=video_path,
        saved_covers=saved_covers,
        work_dir=work_dir,
        title_tone=TITLE_TONE_DEFAULT,
        persist=not args.print_only,
    )

    print(f"Scene: {video_path.parent.name}")
    print(f"  Studio: {out_payload.get('studio') or '(unknown)'}")
    performers = out_payload.get("performers") or []
    print(f"  Performers: {performers or '(none)'}")
    folder_ctx = out_payload.get("folder_context") or {}
    print(f"  Generic filename: {bool(folder_ctx.get('is_generic_filename'))}")
    print(f"  Description: {out_payload.get('operator_description') or '(none)'}")
    print()

    insight = out_payload.get("insight")
    if insight:
        print("Insight:")
        print(f"  Setting: {insight.get('setting')}")
        print(f"  Location: {insight.get('location_hint')}")
        print(f"  Features: {', '.join(insight.get('notable_features') or []) or '(none)'}")
        print(f"  Mood: {insight.get('mood')}")
        print(f"  Action: {insight.get('action_summary')}")
        print()
    else:
        print("Insight: (AI offline or unavailable)\n")

    print("Title suggestions:" + ("" if out_payload.get("ai_used") else " (template fallback — AI offline)"))
    for i, t in enumerate(out_payload.get("ai_titles", []), 1):
        warn = f"  ⚠ {'; '.join(t['warnings'])}" if t.get("warnings") else ""
        print(f"  {i}. [{t.get('style','?')}] {t['text']}  ({t['char_count']}c){warn}")
    if out_payload.get("long_description"):
        print()
        print("Long description:")
        print(f"  {out_payload['long_description']}")
    if not args.print_only:
        print(f"\nWrote {work_dir / 'insight.json'}")
    return 0


def cmd_timing_calibrate(args):
    """Calibrate phase timing thresholds from run_timings.jsonl."""
    from amg.learning.timing_calibration import (
        calibrate_phase_thresholds,
        format_thresholds_markdown_table,
        update_cheat_sheet_thresholds,
    )

    init_logging()
    run_timings_path = Path(args.run_timings_path).expanduser().resolve()
    doc_path = Path(args.doc_path).expanduser().resolve()
    min_samples = max(1, int(args.min_samples))

    rows = calibrate_phase_thresholds(run_timings_path, min_samples=min_samples)
    if not rows:
        print("No calibratable timing data found.")
        print(f"  - Expected ledger: {run_timings_path}")
        print(f"  - Min samples per phase: {min_samples}")
        print("Run a few scenes from the UI first, then retry.")
        return 1

    print("Phase timing thresholds (auto-calibrated)")
    print("=" * 64)
    print(f"Run timings: {run_timings_path}")
    print(f"Min samples per phase: {min_samples}")
    print()
    print(format_thresholds_markdown_table(rows))

    if args.update_doc:
        ok = update_cheat_sheet_thresholds(
            doc_path=doc_path,
            rows=rows,
            run_timings_path=run_timings_path,
            min_samples=min_samples,
        )
        if ok:
            print()
            print(f"Updated cheat sheet: {doc_path}")
        else:
            print()
            print("Could not update cheat sheet automatically.")
            print(f"Ensure markers exist in: {doc_path}")
            print("  <!-- AUTO_THRESHOLD_TABLE_START -->")
            print("  <!-- AUTO_THRESHOLD_TABLE_END -->")
            return 1
    return 0


def cmd_retrain_score(args):
    """Run scoring retrain pipeline and print gate results."""
    from amg.learning.retrain_scoring import run_scoring_retrain

    init_logging()
    try:
        result = run_scoring_retrain(
            from_scene_id=args.from_flag,
            tag=args.tag,
            note=args.note,
            dataset_prefix=args.dataset_prefix,
            max_pairs_per_scene=int(args.max_pairs_per_scene),
        )
    except Exception as e:
        print(f"Retrain failed: {e}")
        return 1

    print("Scoring retrain run")
    print("=" * 64)
    print(f"Run ID: {result.run_id}")
    print(f"Dataset: {result.dataset_name}")
    print(f"Manifest: {result.manifest_path}")
    print(f"Points: {result.points_path}")
    print(f"Pairs: {result.pairs_path}")
    if result.candidate_ready:
        print("Gate status: PASS (candidate ready)")
        return 0

    print("Gate status: BLOCKED")
    for reason in result.blocked_reasons:
        print(f"  - {reason}")
    return 1


def cmd_retrain_status(args):
    """Show recent scoring retrain run summaries."""
    from amg.learning.retrain_scoring import list_retrain_runs

    init_logging()
    runs = list_retrain_runs(limit=int(args.limit))
    if not runs:
        print("No retrain runs found.")
        return 0

    print("Recent scoring retrain runs")
    print("=" * 64)
    for r in runs:
        run_id = r.get("run_id")
        ready = bool(r.get("candidate_ready"))
        gates = r.get("gates") if isinstance(r.get("gates"), dict) else {}
        blocked = gates.get("blocked_reasons") or []
        print(f"{run_id} :: {'READY' if ready else 'BLOCKED'} :: dataset={r.get('dataset_name')}")
        if blocked:
            print(f"  blocked: {', '.join(blocked)}")
    return 0


def cmd_promote_score_candidate(args):
    """Promote a scoring retrain candidate if gates passed."""
    from amg.learning.retrain_scoring import promote_score_candidate

    init_logging()
    try:
        pointer = promote_score_candidate(args.run_id)
    except Exception as e:
        print(f"Promotion failed: {e}")
        return 1

    print("Scoring candidate promoted")
    print("=" * 64)
    print(f"Run ID: {pointer.get('run_id')}")
    print(f"Dataset: {pointer.get('dataset_name')}")
    print(f"Pointer: {pointer.get('manifest_path')}")
    return 0


# ============================================================
# HELPERS
# ============================================================

def _acquire_batch_lock():
    if BATCH_LOCK_FILE.exists():
        try:
            with open(BATCH_LOCK_FILE) as f:
                content = f.read()
            print(f"Another batch is running. Lock contents:\n{content}")
            print("If you're sure no batch is running, run: amg batch --force")
            return False
        except Exception as e:
            print(f"Batch lock exists but could not be read: {e}")
            print("Assuming another batch may be active; use --force only if you are sure.")
            return False

    BATCH_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    BATCH_LOCK_FILE.write_text(
        f"PID: {os.getpid()}\nStarted: {datetime.now().isoformat()}\n"
    )
    return True


def _release_batch_lock():
    try:
        BATCH_LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _print_batch_summary(results, duration, total):
    print()
    print("=" * 64)
    print(f"BATCH COMPLETE — {total} scenes in {format_duration(duration)}")
    print("=" * 64)

    if results["completed"]:
        print(f"\n✓ COMPLETED ({len(results['completed'])}):")
        for r in results["completed"][:10]:
            print(f"  - {r['scene_id']}: {r['covers_saved']} covers, "
                  f"{format_duration(r['total_duration_sec'])}")

    if results["warnings"]:
        print(f"\n⚠ COMPLETED WITH WARNING ({len(results['warnings'])}):")
        for r in results["warnings"][:5]:
            warns = ", ".join(r.get("error_codes", [])) or "warnings"
            print(f"  - {r['scene_id']}: [{warns}]")

    if results["aborted"]:
        print(f"\n✗ ABORTED ({len(results['aborted'])}):")
        for r in results["aborted"][:5]:
            errs = ", ".join(r.get("error_codes", []) or [r.get("error", "unknown")])
            print(f"  - {r.get('scene_id', r.get('path', 'unknown'))}: [{errs}]")
        print("\nRetry aborted scenes:")
        for r in results["aborted"][:3]:
            sid = r.get("scene_id", "")
            print(f"  amg resume \"{sid}\"")
    print("=" * 64)
    print()
    print("Next steps:")
    if results["completed"] or results["warnings"]:
        sample = (results["completed"] + results["warnings"])[0]
        print(f"  amg review \"{sample['scene_id']}\"   # Review and approve")
        print(f"  amg ready \"{sample['scene_id']}\"    # Check distribution-ready")
    print(f"  amg dashboard                          # See trends")
    print("=" * 64)


# ============================================================
# USER ADMIN
# ============================================================

def _read_password_stdin() -> str:
    return sys.stdin.read().rstrip("\r\n")


def _prompt_new_password() -> str | None:
    """Prompt twice and return the password, or None if mismatched."""
    import getpass

    pw1 = getpass.getpass("New password: ")
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        print("Passwords do not match.")
        return None
    return pw1


def _format_login_ts(ts) -> str:
    if ts is None:
        return "never"
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "never"


def cmd_user(args):
    """Manage UI auth users (add, passwd, list, disable, enable, delete)."""
    from amg.ui import auth

    sub = args.user_cmd

    if sub == "add":
        if args.password_stdin:
            password = _read_password_stdin()
        else:
            password = _prompt_new_password()
            if password is None:
                return 1
        try:
            uid = auth.add_user(args.username, password, role=args.role)
        except ValueError as exc:
            print(f"Error: {exc}")
            return 1
        print(f"Created user '{args.username}' (id={uid}, role={args.role}).")
        if auth.count_users() == 1:
            print("This is the first user — they can sign in to the UI immediately.")
        return 0

    if sub == "passwd":
        user = auth.get_user(args.username)
        if user is None:
            print(f"Error: user '{args.username}' not found.")
            return 1
        if args.password_stdin:
            password = _read_password_stdin()
        else:
            password = _prompt_new_password()
            if password is None:
                return 1
        try:
            ok = auth.set_password(args.username, password)
        except ValueError as exc:
            print(f"Error: {exc}")
            return 1
        if not ok:
            print(f"Error: failed to update password for '{args.username}'.")
            return 1
        print(f"Password updated for '{args.username}'.")
        return 0

    if sub == "list":
        users = auth.list_users()
        if not users:
            print("No users yet. Create one with: amg user add <username>")
            return 0
        print(f"{'USERNAME':<24} {'ROLE':<10} {'LAST LOGIN':<18} STATUS")
        print("-" * 64)
        for u in users:
            status = "disabled" if u.get("disabled_at") else "active"
            print(
                f"{u['username']:<24} {u['role']:<10} "
                f"{_format_login_ts(u.get('last_login_at')):<18} {status}"
            )
        return 0

    if sub == "disable":
        if not auth.disable_user(args.username):
            print(f"Error: user '{args.username}' not found.")
            return 1
        print(f"Disabled '{args.username}'. Existing sessions remain valid until they expire; "
              "rotate AMG_SESSION_SECRET to revoke them immediately.")
        return 0

    if sub == "enable":
        if not auth.enable_user(args.username):
            print(f"Error: user '{args.username}' not found.")
            return 1
        print(f"Enabled '{args.username}'.")
        return 0

    if sub == "delete":
        if not args.yes:
            ans = input(f"Permanently delete user '{args.username}'? [y/N]: ").strip().lower()
            if ans not in {"y", "yes"}:
                print("Cancelled.")
                return 1
        if not auth.delete_user(args.username):
            print(f"Error: user '{args.username}' not found.")
            return 1
        print(f"Deleted '{args.username}'.")
        return 0

    print(f"Unknown user subcommand: {sub}")
    return 1


def cmd_pod_worker(args):
    """Run the pod-side worker (cloud edition entry point inside the GPU pod)."""
    init_logging()
    from amg.cloud.pod_worker import cli_main

    return cli_main(host=args.host, port=args.port)


if __name__ == "__main__":
    sys.exit(main())
