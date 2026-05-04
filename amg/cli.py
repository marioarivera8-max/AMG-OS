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
    return 1


# ============================================================
# COMMAND IMPLEMENTATIONS
# ============================================================

def cmd_process(args):
    """Process a single scene."""
    from amg.pipeline import process_scene
    from amg.ingest.inventory import discover_scenes

    init_logging()
    path = Path(args.path).resolve()

    if path.is_dir():
        videos = discover_scenes(path, recursive=False)
        if not videos:
            print(f"No video files found in {path}")
            return 1
        if len(videos) > 1:
            print(f"Multiple videos in folder; using first: {videos[0].name}")
        video_path = videos[0]
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

    # Search common locations
    home = Path.home()
    search_roots = [home / "AMG_Processing", home / "AMG_OS" / "incoming"]

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

    try:
        from amg.ui.app import create_app
    except ImportError as e:
        print(f"Could not load UI app: {e}")
        print("Install dependencies: pip install fastapi uvicorn jinja2 python-multipart")
        return 1

    url = f"http://{args.host}:{args.port}"
    print(f"Starting AMG UI at {url}")
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
        except Exception:
            pass

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


if __name__ == "__main__":
    sys.exit(main())
