"""Command-line interface for setup, translation, and scans."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from amg.translate import config
from amg.translate.processor import dry_run_analyze, process_workbook, write_template
from amg.translate.spend_tracker import get_today_summary
from amg.translate.watcher import archive_bytes, load_public_state, scan_once


def _cmd_setup(_: argparse.Namespace) -> None:
    root = config.drive_root()
    folders = [
        root,
        root / config.DROP_FOLDER,
        root / config.OUTPUT_FOLDER,
        root / config.ARCHIVE_FOLDER,
        root / config.ERRORS_FOLDER,
        config.LOG_DIR,
    ]
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)

    readme = root / config.README_NAME
    readme.write_text(
        "# TRANSLATE SPREADSHEET MODULE\n\n"
        "1. Duplicate `0 - TEMPLATE - Copy This.xlsx` for each batch.\n"
        "2. Drop completed workbooks into `1 - DROP SPREADSHEET HERE/`.\n"
        "3. Pick up translated copies from `2 - TRANSLATED SPREADSHEETS/`.\n"
        "4. Originals archive to `ARCHIVE - ORIGINALS/`; failures go to "
        "`ERRORS - FIX AND RE-DROP/` with a matching `.error.log`.\n\n"
        "**Cron (every 2 minutes)** — stable watcher (MD5 hash + duplicate guards):\n\n"
        "```\n"
        f"*/2 * * * * cd $HOME/AMG_OS && /usr/bin/python3 -m amg.translate.watcher "
        f">> $HOME/AMG_OS/logs/watcher.log 2>&1\n"
        "```\n",
        encoding="utf-8",
    )

    template_path = root / config.TEMPLATE_NAME
    write_template(template_path)

    print(f"Drive scaffold ready at {root}")


def _cmd_translate(ns: argparse.Namespace) -> None:
    src = Path(ns.file).expanduser().resolve()
    if not src.exists():
        raise SystemExit(f"Input not found: {src}")

    out_arg = ns.output
    dest = Path(out_arg).expanduser().resolve() if out_arg else src.with_name(f"{src.stem}_TRANSLATED.xlsx")

    use_claude = config.anthropic_enabled()
    if ns.no_claude:
        use_claude = False

    process_workbook(src, dest, overwrite_titles=ns.overwrite_titles, use_claude=use_claude)
    print(f"Wrote {dest}")


def _cmd_scan(ns: argparse.Namespace) -> None:
    use_claude: bool | None = None if not ns.no_claude else False
    outputs = scan_once(overwrite_titles=ns.overwrite_titles, use_claude=use_claude)
    if not outputs:
        print("No spreadsheets processed.")
        return
    for path in outputs:
        print(f"Translated -> {path}")


def _human_age(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)} sec"
    minutes = seconds / 60.0
    if minutes < 90:
        return f"{int(round(minutes))} min"
    hours = minutes / 60.0
    return f"{hours:.1f} hours"


def _queue_lines() -> tuple[list[str], int]:
    root = config.drive_root()
    drop = root / config.DROP_FOLDER
    lines: list[str] = []
    if not drop.exists():
        return lines, 0

    seen = sorted(p for p in drop.iterdir() if p.is_file() and _eligible_drop_candidate(p))

    cf = load_public_state().get("current_files") or {}

    now = datetime.now().timestamp()
    req = config.STABILITY_SCANS_REQUIRED

    for p in seen:
        st = cf.get(p.name, {})
        size_mb = max(p.stat().st_size, 1) / (1024 * 1024)
        age_s = max(0.0, now - p.stat().st_mtime)
        stab = int(st.get("stable_scans", 0))
        matched = stab >= req
        if matched:
            next_state = "stable, will process next scan"
        else:
            need = req - stab
            next_state = f"awaiting stability ({need} more same-scan pass{'es' if need != 1 else ''})"
        lines.append(f"- {p.name} ({size_mb:.1f} MB, age {_human_age(age_s)}, {next_state})")

    return lines, len(seen)


def _eligible_drop_candidate(p: Path) -> bool:
    n = p.name
    if n.startswith(".") or n.startswith("~"):
        return False
    if not n.lower().endswith(".xlsx"):
        return False
    if n.startswith("0 - TEMPLATE"):
        return False
    return True


def _cmd_status(_: argparse.Namespace) -> None:
    now_dt = datetime.now()
    headline = now_dt.strftime("AMG Translate Status (%Y-%m-%d %H:%M:%S)")

    st = load_public_state()
    metrics = st.get("metrics") or {}
    today_iso = datetime.now().date().isoformat()
    scans_today = int(metrics.get("scans", 0)) if metrics.get("date") == today_iso else 0
    proc_today = int(metrics.get("files_processed", 0)) if metrics.get("date") == today_iso else 0

    batches, spend = get_today_summary()
    cap = float(config.DAILY_SPEND_CAP_USD)
    pct = int(round(100 * spend / cap)) if cap > 0 else 0

    last_ok = st.get("last_success") or {}
    last_err = st.get("last_error")

    n_arch, nbytes = archive_bytes(st)
    arch_mb_total = nbytes / (1024 * 1024) if nbytes else 0

    queue_lines, queue_n = _queue_lines()

    print(headline)
    print("──────────────────────────────────────────")
    qlab = "Queue:"
    print(f"{qlab:17}{queue_n} file{'s' if queue_n != 1 else ''} in drop folder")
    for ln in queue_lines:
        print(f"{'':17} {ln}")

    runs_line = "Today's runs:"
    print(f"{runs_line:17}{scans_today} scans, {proc_today} files processed")

    spent_line = "Today's spend:"
    print(f"{spent_line:17}${spend:.2f} ({pct}% of ${cap:.2f} cap)")

    if isinstance(last_ok, dict) and last_ok.get("at"):
        print(
            f"{'Last success:':17}{last_ok.get('at')} ({last_ok.get('file','')}, "
            f"{last_ok.get('rows','?')} rows)"
        )
    else:
        print(f"{'Last success:':17}none")

    if isinstance(last_err, dict) and last_err.get("at"):
        detail = last_err.get("detail", "").replace("\n", " ")[:120]
        print(f"{'Last error:':17}{last_err.get('at')} — {detail}")
    else:
        print(f"{'Last error:':17}none")

    print(f"{'Archive size:':17}{n_arch} files, total {arch_mb_total:.0f}MB")

    paused = "YES" if config.PAUSE_FLAG_FILE.exists() else "NO"
    print(f"{'Paused:':17}{paused}")


def _cmd_dry_run(ns: argparse.Namespace) -> None:
    src = Path(ns.file).expanduser().resolve()
    if not src.exists():
        raise SystemExit(f"Input not found: {src}")
    rep = dry_run_analyze(src, overwrite_titles=bool(ns.overwrite_titles))

    print(f"Dry-run: {rep.path.name}")
    print(f"Rows:           {rep.row_count}")
    print(f"Studio:         {rep.studio_label}")
    print(f"Language:       {rep.language_line}")
    print("")
    print("Preservation:   ")
    print(f"{16 * ' '} {rep.keep_original_rows} rows have existing titles → KEEP ORIGINAL")
    print(f"{16 * ' '} {rep.new_title_rows} rows empty → NEW TITLE")
    print(f"{16 * ' '} {rep.needs_review_rows} rows flagged NEEDS REVIEW ({rep.bare_filename_rows} bare filenames)")
    print("")
    polish_cost = round(float(rep.est_cost_usd), 2)
    print(f"API polish:     {rep.will_polish_rows} rows would be polished (est ${polish_cost:.2f})")
    meds = ",".join(str(x) for x in rep.med_row_indices)
    highs = ",".join(str(x) for x in rep.high_row_indices)
    print("Compliance:")
    print(f"{16 * ' '} {len(rep.med_row_indices)} MED risk hits (catalog row nums: {meds or '—'})")
    print(f"{16 * ' '} {len(rep.high_row_indices)} HIGH risk hits ({highs or '—'})")
    print("")
    print(f"Would write to: {rep.outgoing_folder}/{rep.output_filename_pattern}")


def _cmd_pause(_: argparse.Namespace) -> None:
    path = config.PAUSE_FLAG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    print(f"Watcher paused ({path})")


def _cmd_resume(_: argparse.Namespace) -> None:
    path = config.PAUSE_FLAG_FILE
    if path.exists():
        path.unlink()
    print(f"Watcher resumed ({path.name} removed if present)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AMG translate spreadsheet tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    setup_parser = sub.add_parser("setup", help="Create Drive folders and template workbook")
    setup_parser.set_defaults(func=_cmd_setup)

    translate_parser = sub.add_parser("translate", help="Translate a workbook")
    translate_parser.add_argument("file", help="Path to source .xlsx")
    translate_parser.add_argument("-o", "--output", "--out", dest="output", help="Destination workbook path")
    translate_parser.add_argument("--overwrite-titles", action="store_true", help="Regenerate copy even if populated")
    translate_parser.add_argument("--no-claude", action="store_true", help="Skip Anthropic polishing")
    translate_parser.set_defaults(func=_cmd_translate)

    scan_parser = sub.add_parser("scan", help="Single watcher pass over DROP folder")
    scan_parser.add_argument("--overwrite-titles", action="store_true")
    scan_parser.add_argument("--no-claude", action="store_true")
    scan_parser.set_defaults(func=_cmd_scan)

    stat_p = sub.add_parser("status", help="Human-readable watchdog / queue overview")
    stat_p.set_defaults(func=_cmd_status)

    dry = sub.add_parser("dry-run", help="Analyze a workbook without filesystem side effects")
    dry.add_argument("file", help="Path to catalog .xlsx")
    dry.add_argument("--overwrite-titles", action="store_true")
    dry.set_defaults(func=_cmd_dry_run)

    pau = sub.add_parser("pause", help="Pause automatic watcher scans")
    pau.set_defaults(func=_cmd_pause)

    res = sub.add_parser("resume", help="Resume automatic watcher scans")
    res.set_defaults(func=_cmd_resume)

    return parser


def main() -> None:
    parser = build_parser()
    ns = parser.parse_args()
    ns.func(ns)


if __name__ == "__main__":
    main()
