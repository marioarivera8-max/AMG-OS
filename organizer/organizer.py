#!/usr/bin/env python3
"""
amg_organizer — route inbox files into AMG delivery folders by manifest.

stdlib only. Python 3.12+.

Subcommands:
  status                 Print manifest detection and per-state counts.
  run [--dry-run]        Process the inbox once and exit.
  watch                  Poll inbox forever, processing new stable files.
  dashboard [--watch]    Write a self-contained HTML status page.
  serve [--port --open]  Local web control panel (view + Run/Dry-run buttons).
  deliver [--dry-run]    Push manifest videos to a cloud remote via rclone.
  verify                 Double-check: list the remote and confirm every
                         expected file is present, in the right title folder.

Safety:
  - Never deletes.
  - Never overwrites (appends _2, _3, ... on collision).
  - Same-filesystem move uses os.replace (atomic). Cross-fs uses
    copy + fsync + verify + delete.
  - status.json is the source of truth. Safe to kill and restart.

Log format (TSV, one event per line):
  ISO8601\\tLEVEL\\tEVENT\\tkey=value\\tkey=value ...
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Local config
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402


# ---------------------------------------------------------------------------
# Paths and small helpers
# ---------------------------------------------------------------------------

def _p(path_str: str) -> Path:
    return Path(os.path.expanduser(path_str)).resolve()


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _log(level: str, event: str, **fields) -> None:
    """Append a TSV log line. Fields are key=value; values are shlex-safe-ish."""
    log_path = _p(config.LOG_FILE)
    _ensure_dir(log_path.parent)
    parts = [_now(), level, event]
    for k, v in fields.items():
        s = str(v).replace("\t", " ").replace("\n", " ")
        parts.append(f"{k}={s}")
    line = "\t".join(parts) + "\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line)
    # Mirror INFO/WARN/ERROR to stderr so CLI users see what's happening.
    if level != "DEBUG":
        print(line.rstrip(), file=sys.stderr)


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------

@dataclass
class ManifestRow:
    row_id: str               # stable identifier for status.json
    scene_id: str
    delivery_folder: str
    target_filename: str      # may be "" if not provided
    source_filename: str      # may be ""
    match_regex: str          # may be ""
    raw: dict = field(default_factory=dict)


@dataclass
class Manifest:
    path: Path
    columns_detected: list[str]
    column_map: dict[str, str]    # logical -> actual column name
    rows: list[ManifestRow]
    missing_required: list[str]   # logical names absent from the CSV


def _resolve_column(header: list[str], candidates: list[str]) -> str | None:
    norm = {h.strip().lower(): h for h in header if h is not None}
    for c in candidates:
        if c.strip().lower() in norm:
            return norm[c.strip().lower()]
    return None


def load_manifest(manifest_path: Path) -> Manifest:
    if not manifest_path.exists():
        return Manifest(
            path=manifest_path,
            columns_detected=[],
            column_map={},
            rows=[],
            missing_required=["__file_missing__"],
        )

    with manifest_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        column_map: dict[str, str] = {}
        for logical, candidates in config.MANIFEST_COLUMNS.items():
            actual = _resolve_column(header, candidates)
            if actual:
                column_map[logical] = actual

        required = ["SCENE_ID", "DELIVERY_FOLDER"]
        missing = [r for r in required if r not in column_map]

        rows: list[ManifestRow] = []
        if not missing:
            for i, raw in enumerate(reader, start=2):  # row 1 = header
                scene_id = (raw.get(column_map["SCENE_ID"], "") or "").strip()
                folder = (raw.get(column_map["DELIVERY_FOLDER"], "") or "").strip()
                if not scene_id or not folder:
                    continue
                target = ""
                if "TARGET_FILENAME" in column_map:
                    target = (raw.get(column_map["TARGET_FILENAME"], "") or "").strip()
                source = ""
                if "SOURCE_FILENAME" in column_map:
                    source = (raw.get(column_map["SOURCE_FILENAME"], "") or "").strip()
                    # Tolerate a full URL — keep just the basename for matching.
                    if "/" in source:
                        source = source.split("?", 1)[0].rsplit("/", 1)[-1]
                regex = ""
                if "MATCH_REGEX" in column_map:
                    regex = (raw.get(column_map["MATCH_REGEX"], "") or "").strip()
                rows.append(ManifestRow(
                    row_id=f"r{i:05d}_{scene_id}",
                    scene_id=scene_id,
                    delivery_folder=folder,
                    target_filename=target,
                    source_filename=source,
                    match_regex=regex,
                    raw=raw,
                ))

        return Manifest(
            path=manifest_path,
            columns_detected=header,
            column_map=column_map,
            rows=rows,
            missing_required=missing,
        )


# ---------------------------------------------------------------------------
# Status (state) persistence
# ---------------------------------------------------------------------------

# State values for each manifest row in status.json:
#   "pending"     — no file has been routed to this row yet
#   "organized"   — a file was matched and moved into the delivery folder
# Files that match no row, or match more than one, never become a row state.
# They're tracked separately under state["unmatched"] and
# state["ambiguous_files"]; the rows they touched stay "pending".

def load_status() -> dict:
    p = _p(config.STATUS_FILE)
    if not p.exists():
        return {"rows": {}, "unmatched": [], "ambiguous_files": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _log("WARN", "status_corrupt_resetting", path=str(p))
        return {"rows": {}, "unmatched": [], "ambiguous_files": []}


def save_status(state: dict) -> None:
    p = _p(config.STATUS_FILE)
    _ensure_dir(p.parent)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def reconcile_status_with_manifest(state: dict, manifest: Manifest) -> dict:
    """Ensure every manifest row has an entry; preserve existing ones."""
    rows = state.setdefault("rows", {})
    for r in manifest.rows:
        if r.row_id not in rows:
            rows[r.row_id] = {
                "state": "pending",
                "scene_id": r.scene_id,
                "delivery_folder": r.delivery_folder,
                "dest": None,
                "moved_at": None,
            }
    return state


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

# A scene ID counts as "in the filename" only when it appears as a whole
# token — bounded by start/end-of-string or one of . _ - or whitespace.
# This avoids a numeric ID like "32389" matching inside "323891" or a hash.
_TOKEN_EDGE = r"(?:^|[\s._\-])"
_TOKEN_EDGE_END = r"(?:$|[\s._\-])"


def _match_row(basename: str, row: ManifestRow) -> bool:
    name_no_ext = os.path.splitext(basename)[0]

    # Rule 1: exact SOURCE_FILENAME (case-insensitive).
    if config.ENABLE_EXACT_SOURCE_MATCH and row.source_filename:
        if basename.lower() == row.source_filename.lower():
            return True
        # also accept extensionless match if the manifest source had no ext
        src_noext = os.path.splitext(row.source_filename)[0]
        if name_no_ext.lower() == src_noext.lower():
            return True

    # Rule 2: scene ID as a token in the filename.
    if config.ENABLE_SCENE_ID_TOKEN_MATCH and row.scene_id:
        tok = re.escape(row.scene_id)
        pat = re.compile(_TOKEN_EDGE + tok + _TOKEN_EDGE_END, re.IGNORECASE)
        if pat.search(basename):
            return True

    # Rule 3: per-row regex.
    if config.ENABLE_PER_ROW_REGEX and row.match_regex:
        try:
            if re.search(row.match_regex, basename, re.IGNORECASE):
                return True
        except re.error as e:
            _log("WARN", "bad_regex", row=row.row_id, regex=row.match_regex, error=str(e))

    return False


def find_matches(basename: str, manifest: Manifest) -> list[ManifestRow]:
    """
    Return every manifest row whose rules match `basename`.

    Matching is a static property of (filename, manifest). It deliberately
    does NOT consider what's already been organized — so processing order
    can never mask an ambiguity, and a --dry-run predicts the real run
    exactly. Whether the matched row is already filled is decided later, in
    process_file().
    """
    return [r for r in manifest.rows if _match_row(basename, r)]


# ---------------------------------------------------------------------------
# Move + collision handling
# ---------------------------------------------------------------------------

def _collision_free_dest(dest_dir: Path, filename: str) -> Path:
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate
    stem, ext = os.path.splitext(filename)
    n = 2
    while True:
        alt = dest_dir / f"{stem}_{n}{ext}"
        if not alt.exists():
            return alt
        n += 1


def _sha256(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for blob in iter(lambda: f.read(chunk), b""):
            h.update(blob)
    return h.hexdigest()


def _safe_move(src: Path, dst: Path) -> None:
    """Move src to dst safely. Same fs => os.replace; cross fs => copy+verify+unlink."""
    _ensure_dir(dst.parent)
    try:
        os.replace(src, dst)
        return
    except OSError:
        # Likely cross-device. Copy, flush to disk, verify, then unlink.
        tmp = dst.with_suffix(dst.suffix + ".part")
        shutil.copy2(src, tmp)
        # Force the copy to physical storage before we trust it enough to
        # delete the source.
        fd = os.open(tmp, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        if _sha256(src) != _sha256(tmp):
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"checksum mismatch copying {src} -> {tmp}")
        os.replace(tmp, dst)
        src.unlink()


# ---------------------------------------------------------------------------
# Inbox iteration
# ---------------------------------------------------------------------------

def _is_candidate(p: Path) -> bool:
    if not p.is_file():
        return False
    if p.name in config.IGNORED_BASENAMES or p.name.startswith("."):
        return False
    if config.ALLOWED_EXTENSIONS and p.suffix.lower() not in config.ALLOWED_EXTENSIONS:
        return False
    if config.MIN_FILE_BYTES and p.stat().st_size < config.MIN_FILE_BYTES:
        return False
    return True


def _stable_files(inbox: Path) -> list[Path]:
    """
    Return files that look done being written.

    A file qualifies when its modification time is at least
    STABILITY_WINDOW_SECS old (nothing has written to it recently) AND its
    size is steady across a short recheck. Using mtime age as the primary
    signal means an in-progress download — whose mtime keeps advancing — is
    skipped until it goes quiet.

    Any entry that errors mid-scan (vanished, permissions) is skipped, not
    fatal: a single bad file must never abort the whole run.
    """
    out: list[Path] = []
    for entry in sorted(inbox.iterdir()):
        try:
            if entry.is_dir():
                continue
            if not _is_candidate(entry):
                continue
            size_a = entry.stat().st_size
            mtime_age = time.time() - entry.stat().st_mtime
            if mtime_age < config.STABILITY_WINDOW_SECS:
                continue
            # Double-check the size hasn't changed in the last instant.
            time.sleep(0.05)
            size_b = entry.stat().st_size
        except OSError:
            # File vanished or became unreadable mid-scan — skip this pass.
            continue
        if size_a == size_b:
            out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Core process step
# ---------------------------------------------------------------------------

def _route_to_delivery(file_path: Path, row: ManifestRow, state: dict, *, dry_run: bool) -> None:
    """Move a single-matched file into its delivery folder and mark the row organized."""
    dest_folder = _p(config.DOWNLOADS_DIR) / row.delivery_folder
    final_name = row.target_filename or file_path.name
    dest = _collision_free_dest(dest_folder, final_name)
    if dry_run:
        _log("INFO", "would_move", src=str(file_path), dst=str(dest), row=row.row_id)
    else:
        _safe_move(file_path, dest)
        _log("INFO", "moved", src=str(file_path), dst=str(dest), row=row.row_id)
    # Mark the row organized in-memory whether or not this is a dry run, so a
    # dry run simulates the real run's row-consumption sequence exactly. In
    # dry-run mode cmd_run never persists state, so status.json is untouched.
    state["rows"].setdefault(row.row_id, {})
    state["rows"][row.row_id].update({
        "state": "organized",
        "scene_id": row.scene_id,
        "delivery_folder": row.delivery_folder,
        "dest": str(dest),
        "moved_at": _now(),
    })


def _route_to_unmatched(file_path: Path, state: dict, *, dry_run: bool,
                        reason: str, matched_row: str | None = None) -> None:
    """Quarantine a file to _unmatched/. `reason` records why it wasn't placed."""
    dest = _collision_free_dest(_p(config.UNMATCHED_DIR), file_path.name)
    if dry_run:
        _log("WARN", "would_quarantine_unmatched",
             src=str(file_path), dst=str(dest), reason=reason)
        return
    _safe_move(file_path, dest)
    entry = {"file": str(dest), "at": _now(), "reason": reason}
    if matched_row:
        entry["row"] = matched_row
    state.setdefault("unmatched", []).append(entry)
    _log("WARN", "unmatched", src=str(file_path), dst=str(dest), reason=reason)


def _route_to_ambiguous(file_path: Path, matches: list[ManifestRow], state: dict,
                        *, dry_run: bool) -> None:
    """Quarantine a file to _ambiguous/ with a sidecar listing the candidate rows."""
    dest = _collision_free_dest(_p(config.AMBIGUOUS_DIR), file_path.name)
    candidates = ",".join(m.row_id for m in matches)
    if dry_run:
        _log("WARN", "would_quarantine_ambiguous",
             src=str(file_path), candidates=candidates)
        return
    _safe_move(file_path, dest)
    sidecar = "Multiple manifest rows matched this file:\n\n" + "\n".join(
        f"  - {m.row_id}  scene_id={m.scene_id}  folder={m.delivery_folder}"
        for m in matches
    ) + "\n\nResolve manually, then move the file into the correct folder.\n"
    dest.with_suffix(dest.suffix + ".candidates.txt").write_text(sidecar, encoding="utf-8")
    state.setdefault("ambiguous_files", []).append({
        "file": str(dest),
        "candidates": [m.row_id for m in matches],
        "at": _now(),
    })
    _log("WARN", "ambiguous", src=str(file_path), dst=str(dest), candidates=candidates)


def process_file(file_path: Path, manifest: Manifest, state: dict, *, dry_run: bool) -> str:
    """
    Process one inbox file. Returns "organized", "ambiguous", or "unmatched".
    Mutates `state` in-memory; the caller persists it (and never does so on a
    dry run).

    The decision tree is order-independent: find_matches() inspects every
    manifest row, so the only state-dependent branch is "the one row I match
    is already filled" — which is itself reproduced faithfully by a dry run
    because _route_to_delivery() simulates the row consumption in memory.

      >1 match            -> _ambiguous/   (never guess)
       1 match, pending   -> delivery folder
       1 match, filled    -> _unmatched/   (reason: row_already_filled)
       0 matches          -> _unmatched/   (reason: no_match)
    """
    matches = find_matches(file_path.name, manifest)

    if len(matches) > 1:
        _route_to_ambiguous(file_path, matches, state, dry_run=dry_run)
        return "ambiguous"

    if len(matches) == 1:
        row = matches[0]
        already_filled = (
            state.get("rows", {}).get(row.row_id, {}).get("state") == "organized"
        )
        if already_filled:
            # The only row this file matches has already been filled by
            # another file. Never overwrite, never guess — quarantine it.
            _route_to_unmatched(file_path, state, dry_run=dry_run,
                                reason="row_already_filled", matched_row=row.row_id)
            return "unmatched"
        _route_to_delivery(file_path, row, state, dry_run=dry_run)
        return "organized"

    # No manifest row matched.
    _route_to_unmatched(file_path, state, dry_run=dry_run, reason="no_match")
    return "unmatched"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    manifest_path = _p(args.manifest or config.MANIFEST_PATH)
    manifest = load_manifest(manifest_path)
    state = load_status()

    print(f"manifest: {manifest_path}")
    if manifest.missing_required == ["__file_missing__"]:
        print("  (file not found — drop a CSV here to enable matching)")
    else:
        print(f"  columns detected: {manifest.columns_detected}")
        print(f"  column map:       {manifest.column_map}")
        if manifest.missing_required:
            print(f"  MISSING REQUIRED FIELDS: {manifest.missing_required}")
            print("  Edit config.MANIFEST_COLUMNS so each required logical field")
            print("  is mapped to one of the column names above.")
        print(f"  rows loaded:      {len(manifest.rows)}")
        has_strong_match = ("SOURCE_FILENAME" in manifest.column_map
                            or "MATCH_REGEX" in manifest.column_map)
        if not manifest.missing_required and not has_strong_match:
            print("  NOTE: no SOURCE_FILENAME or MATCH_REGEX column detected.")
            print("  Matching relies solely on the scene-ID token rule — if your")
            print("  filenames don't contain the scene ID, files will route to")
            print("  _unmatched/. Add a SOURCE_FILENAME or MATCH_REGEX column to")
            print("  the manifest for deterministic matching.")

    reconcile_status_with_manifest(state, manifest)
    rows = state.get("rows", {})
    organized = sum(1 for v in rows.values() if v.get("state") == "organized")
    pending = sum(1 for v in rows.values() if v.get("state") == "pending")
    unmatched = len(state.get("unmatched", []))
    ambiguous = len(state.get("ambiguous_files", []))

    print()
    print("totals:")
    print(f"  manifest rows: {len(rows)}")
    print(f"  organized:     {organized}")
    print(f"  pending:       {pending}")
    print(f"  unmatched:     {unmatched}")
    print(f"  ambiguous:     {ambiguous}")
    return 0


def _process_inbox(manifest_path: Path, *, dry_run: bool) -> dict:
    """
    Process every stable file in the inbox once.

    Returns:
      {"ok": True,  "files": N, "counts": {...}}   on success
      {"ok": False, "error": "...message..."}      if the manifest is unusable

    Shared by `cmd_run`, `cmd_watch`, and the `serve` web UI so all three
    route files through identical logic — there is exactly one code path
    that ever moves a file.
    """
    manifest = load_manifest(manifest_path)
    if manifest.missing_required:
        if manifest.missing_required == ["__file_missing__"]:
            return {"ok": False, "error": f"manifest not found at {manifest_path}"}
        return {"ok": False,
                "error": (f"manifest is missing required fields: "
                          f"{manifest.missing_required} — detected columns: "
                          f"{manifest.columns_detected}")}

    state = load_status()
    reconcile_status_with_manifest(state, manifest)

    inbox = _p(config.INBOX_DIR)
    _ensure_dir(inbox)
    _ensure_dir(_p(config.UNMATCHED_DIR))
    _ensure_dir(_p(config.AMBIGUOUS_DIR))

    files = _stable_files(inbox)
    # Only log cycles that actually have work — otherwise `watch`/`serve`
    # flood organizer.log with an empty run_start/run_end pair.
    if files:
        _log("INFO", "run_start", count=len(files), dry_run=dry_run)

    counts = {"organized": 0, "ambiguous": 0, "unmatched": 0, "skipped": 0}
    for f in files:
        try:
            result = process_file(f, manifest, state, dry_run=dry_run)
        except Exception as e:  # noqa: BLE001
            _log("ERROR", "process_failed", src=str(f), error=repr(e))
            counts["skipped"] += 1
            continue
        counts[result] = counts.get(result, 0) + 1

    if files and not dry_run:
        save_status(state)
    if files:
        _log("INFO", "run_end", **counts, dry_run=dry_run)

    return {"ok": True, "files": len(files), "counts": counts}


def cmd_run(args: argparse.Namespace) -> int:
    result = _process_inbox(_p(args.manifest or config.MANIFEST_PATH),
                            dry_run=args.dry_run)
    if not result["ok"]:
        print(result["error"], file=sys.stderr)
        return 2
    print(f"processed {result['files']} files: {result['counts']}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    interval = config.POLL_INTERVAL_SECS
    manifest_path = _p(args.manifest or config.MANIFEST_PATH)
    print(f"watching {config.INBOX_DIR} every {interval}s. ctrl-c to stop.",
          file=sys.stderr)
    while True:
        try:
            # Reload the manifest each pass so CSV edits apply without a restart.
            result = _process_inbox(manifest_path, dry_run=False)
            if not result["ok"]:
                _log("ERROR", "watch_loop_error", error=result["error"])
            elif result["files"]:
                print(f"processed {result['files']} files: {result['counts']}")
        except KeyboardInterrupt:
            print("stopped.", file=sys.stderr)
            return 0
        except Exception as e:  # noqa: BLE001
            _log("ERROR", "watch_loop_error", error=repr(e))
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("stopped.", file=sys.stderr)
            return 0


# ---------------------------------------------------------------------------
# Dashboard — self-contained HTML status page
# ---------------------------------------------------------------------------

_DASHBOARD_CSS = """
:root{
  --bg:#f6f7f9; --card:#fff; --ink:#1c2530; --muted:#6b7785;
  --line:#e3e7ec; --green:#1a7f4b; --amber:#b9760e; --red:#c0392b; --blue:#2c5d8f;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:28px 22px 60px}
h1{font-size:20px;margin:0 0 2px}
.sub{color:var(--muted);font-size:13px;margin-bottom:22px}
.cards{display:flex;gap:12px;margin-bottom:26px;flex-wrap:wrap}
.card{flex:1;min-width:150px;background:var(--card);border:1px solid var(--line);
  border-radius:10px;padding:14px 16px}
.card .n{font-size:30px;font-weight:650;line-height:1}
.card .l{color:var(--muted);font-size:12px;text-transform:uppercase;
  letter-spacing:.04em;margin-top:6px}
.card.organized .n{color:var(--green)}
.card.pending .n{color:var(--blue)}
.card.unmatched .n{color:var(--amber)}
.card.ambiguous .n{color:var(--red)}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;
  margin-bottom:18px;overflow:hidden}
section h2{font-size:13px;text-transform:uppercase;letter-spacing:.04em;
  color:var(--muted);margin:0;padding:12px 16px;border-bottom:1px solid var(--line)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 16px;border-bottom:1px solid var(--line);
  vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase}
tr:last-child td{border-bottom:none}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;
  word-break:break-all}
.empty{padding:14px 16px;color:var(--muted)}
.tag{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11px;
  font-weight:600}
.tag.INFO{background:#e7f0f8;color:var(--blue)}
.tag.WARN{background:#fbf0dd;color:var(--amber)}
.tag.ERROR{background:#fbe4e1;color:var(--red)}
.banner{background:#fbe4e1;color:var(--red);border:1px solid #f0c2bb;
  border-radius:8px;padding:10px 14px;margin-bottom:18px;font-size:13px}
.controls{display:flex;gap:8px;align-items:center;margin-bottom:18px;
  flex-wrap:wrap}
.controls button,.controls a.btn{font:inherit;font-size:13px;font-weight:600;
  padding:7px 14px;border-radius:8px;border:1px solid var(--line);
  background:var(--card);color:var(--ink);cursor:pointer;text-decoration:none}
.controls button.primary{background:var(--blue);color:#fff;
  border-color:var(--blue)}
.controls button:hover,.controls a.btn:hover{filter:brightness(.97)}
.controls .spacer{flex:1}
.controls .hint{color:var(--muted);font-size:12px}
.notice{background:#e7f0f8;color:var(--blue);border:1px solid #cfe0ee;
  border-radius:8px;padding:10px 14px;margin-bottom:18px;font-size:13px}
.notice.warn{background:#fbf0dd;color:var(--amber);border-color:#ecd9b0}
.notice.err{background:#fbe4e1;color:var(--red);border-color:#f0c2bb}
.foot{color:var(--muted);font-size:12px;margin-top:24px;text-align:center}
""".strip()


def _read_log_tail(n: int) -> list[tuple[str, str, str, str]]:
    """Return the last `n` log lines parsed into (ts, level, event, details)."""
    log_path = _p(config.LOG_FILE)
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[tuple[str, str, str, str]] = []
    for line in lines[-n:]:
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        ts, level, event = parts[0], parts[1], parts[2]
        out.append((ts, level, event, "  ".join(parts[3:])))
    return out


def _render_dashboard(manifest: Manifest, state: dict,
                      log_rows: list[tuple[str, str, str, str]], *, live: bool,
                      controls: str = "", notice: str = "") -> str:
    """
    Render the whole status page as one self-contained HTML string.

    `controls` is an optional HTML snippet (the serve panel's button bar);
    `notice` is an optional banner shown above the cards (e.g. a run result).
    Both default to "" so the static `dashboard` command is unaffected.
    """
    esc = html.escape
    rows = state.get("rows", {})
    organized = sorted((kv for kv in rows.items() if kv[1].get("state") == "organized"))
    pending = sorted((kv for kv in rows.items() if kv[1].get("state") == "pending"))
    unmatched = state.get("unmatched", [])
    ambiguous = state.get("ambiguous_files", [])

    p: list[str] = ["<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">',
                    '<meta name="viewport" content="width=device-width, initial-scale=1">']
    if live:
        p.append(f'<meta http-equiv="refresh" content="{int(config.DASHBOARD_REFRESH_SECS)}">')
    p.append("<title>AMG Organizer — Status</title>")
    p.append("<style>" + _DASHBOARD_CSS + "</style>")
    p.append("</head><body><div class='wrap'>")

    p.append("<h1>AMG Organizer</h1>")
    mode = "live · auto-refresh" if live else "snapshot"
    p.append(f"<div class='sub'>{esc(mode)} · generated {esc(_now())}</div>")

    # Optional serve-panel control bar and action notice (already HTML).
    if controls:
        p.append(controls)
    if notice:
        p.append(notice)

    # Manifest problem banner.
    if manifest.missing_required == ["__file_missing__"]:
        p.append("<div class='banner'>Manifest not found at "
                 f"<span class='mono'>{esc(str(manifest.path))}</span> — "
                 "matching is disabled until a CSV is in place.</div>")
    elif manifest.missing_required:
        p.append("<div class='banner'>Manifest is missing required field(s): "
                 f"{esc(', '.join(manifest.missing_required))}. Detected columns: "
                 f"{esc(', '.join(manifest.columns_detected))}.</div>")

    # Summary cards.
    p.append("<div class='cards'>")
    for cls, count, label in (("organized", len(organized), "Organized"),
                              ("pending", len(pending), "Pending"),
                              ("unmatched", len(unmatched), "Unmatched"),
                              ("ambiguous", len(ambiguous), "Ambiguous")):
        p.append(f"<div class='card {cls}'><div class='n'>{count}</div>"
                 f"<div class='l'>{label}</div></div>")
    p.append("</div>")

    # Manifest summary.
    p.append("<section><h2>Manifest</h2>")
    if manifest.missing_required == ["__file_missing__"]:
        p.append("<div class='empty'>No manifest loaded.</div>")
    else:
        cmap = ", ".join(f"{k} → {v}" for k, v in manifest.column_map.items())
        p.append("<table><tr><th>Path</th><th>Rows</th><th>Column map</th></tr>"
                 f"<tr><td class='mono'>{esc(str(manifest.path))}</td>"
                 f"<td>{len(manifest.rows)}</td>"
                 f"<td class='mono'>{esc(cmap)}</td></tr></table>")
    p.append("</section>")

    # Organized.
    p.append("<section><h2>Organized — delivered files</h2>")
    if organized:
        p.append("<table><tr><th>Row</th><th>Scene ID</th><th>Delivery folder</th>"
                 "<th>Destination file</th><th>Moved at</th></tr>")
        for rid, v in organized:
            dest_name = os.path.basename(v.get("dest") or "")
            p.append(f"<tr><td class='mono'>{esc(rid)}</td>"
                     f"<td>{esc(str(v.get('scene_id', '')))}</td>"
                     f"<td>{esc(str(v.get('delivery_folder', '')))}</td>"
                     f"<td class='mono'>{esc(dest_name)}</td>"
                     f"<td class='mono'>{esc(str(v.get('moved_at') or ''))}</td></tr>")
        p.append("</table>")
    else:
        p.append("<div class='empty'>Nothing organized yet.</div>")
    p.append("</section>")

    # Pending.
    p.append("<section><h2>Pending — manifest rows still awaiting a file</h2>")
    if pending:
        p.append("<table><tr><th>Row</th><th>Scene ID</th><th>Delivery folder</th></tr>")
        for rid, v in pending:
            p.append(f"<tr><td class='mono'>{esc(rid)}</td>"
                     f"<td>{esc(str(v.get('scene_id', '')))}</td>"
                     f"<td>{esc(str(v.get('delivery_folder', '')))}</td></tr>")
        p.append("</table>")
    else:
        p.append("<div class='empty'>No pending rows.</div>")
    p.append("</section>")

    # Unmatched.
    p.append("<section><h2>Unmatched — quarantined in inbox/_unmatched/</h2>")
    if unmatched:
        p.append("<table><tr><th>File</th><th>Reason</th><th>Matched row</th>"
                 "<th>At</th></tr>")
        for e in unmatched:
            p.append(f"<tr><td class='mono'>{esc(os.path.basename(e.get('file', '')))}</td>"
                     f"<td>{esc(str(e.get('reason', '')))}</td>"
                     f"<td class='mono'>{esc(str(e.get('row', '') or ''))}</td>"
                     f"<td class='mono'>{esc(str(e.get('at', '')))}</td></tr>")
        p.append("</table>")
    else:
        p.append("<div class='empty'>Nothing unmatched.</div>")
    p.append("</section>")

    # Ambiguous.
    p.append("<section><h2>Ambiguous — quarantined in inbox/_ambiguous/</h2>")
    if ambiguous:
        p.append("<table><tr><th>File</th><th>Candidate rows</th><th>At</th></tr>")
        for e in ambiguous:
            p.append(f"<tr><td class='mono'>{esc(os.path.basename(e.get('file', '')))}</td>"
                     f"<td class='mono'>{esc(', '.join(e.get('candidates', [])))}</td>"
                     f"<td class='mono'>{esc(str(e.get('at', '')))}</td></tr>")
        p.append("</table>")
    else:
        p.append("<div class='empty'>Nothing ambiguous.</div>")
    p.append("</section>")

    # Recent activity.
    p.append(f"<section><h2>Recent activity — last {len(log_rows)} log lines</h2>")
    if log_rows:
        p.append("<table><tr><th>Time</th><th>Level</th><th>Event</th>"
                 "<th>Details</th></tr>")
        for ts, level, event, details in reversed(log_rows):
            lvl = level if level in ("INFO", "WARN", "ERROR") else "INFO"
            p.append(f"<tr><td class='mono'>{esc(ts)}</td>"
                     f"<td><span class='tag {lvl}'>{esc(level)}</span></td>"
                     f"<td class='mono'>{esc(event)}</td>"
                     f"<td class='mono'>{esc(details)}</td></tr>")
        p.append("</table>")
    else:
        p.append("<div class='empty'>No log activity yet.</div>")
    p.append("</section>")

    p.append("<div class='foot'>amg_organizer · generated by "
             "<span class='mono'>organizer.py dashboard</span></div>")
    p.append("</div></body></html>")
    return "\n".join(p)


def cmd_dashboard(args: argparse.Namespace) -> int:
    manifest_path = _p(args.manifest or config.MANIFEST_PATH)
    out_path = _p(config.DASHBOARD_FILE)

    def _generate() -> None:
        manifest = load_manifest(manifest_path)
        state = load_status()
        # Reconcile in-memory only (like `status`) — the dashboard never
        # writes status.json.
        reconcile_status_with_manifest(state, manifest)
        log_rows = _read_log_tail(config.DASHBOARD_LOG_LINES)
        html_text = _render_dashboard(manifest, state, log_rows, live=args.watch)
        _ensure_dir(out_path.parent)
        tmp = out_path.with_suffix(".html.tmp")
        tmp.write_text(html_text, encoding="utf-8")
        os.replace(tmp, out_path)

    _generate()
    print(f"dashboard written: {out_path}")

    if args.open:
        import webbrowser
        webbrowser.open(out_path.as_uri())

    if args.watch:
        print(f"watching — regenerating every {config.DASHBOARD_REFRESH_SECS}s. "
              "ctrl-c to stop.", file=sys.stderr)
        try:
            while True:
                time.sleep(config.DASHBOARD_REFRESH_SECS)
                _generate()
        except KeyboardInterrupt:
            print("stopped.", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# Serve — local web control panel (view + Run / Dry-run buttons)
# ---------------------------------------------------------------------------
#
# This is the only part of the tool that opens a socket. It binds strictly to
# 127.0.0.1 (see config.SERVE_HOST) so it is never reachable off-machine —
# it's a local control surface, not a network service. No outbound calls.

_SERVE_CONTROLS = (
    "<form class='controls' method='post'>"
    "<button class='primary' formaction='/run'>Run now</button>"
    "<button formaction='/dry-run'>Dry-run</button>"
    "<a class='btn' href='/'>Refresh</a>"
    "<span class='spacer'></span>"
    "<span class='hint'>local control panel · 127.0.0.1 · moves real files</span>"
    "</form>"
)


def _serve_page(manifest_path: Path, *, notice: str = "") -> bytes:
    """Build the served control-panel page as UTF-8 bytes."""
    manifest = load_manifest(manifest_path)
    state = load_status()
    # Reconcile in memory only — viewing the panel never writes status.json.
    reconcile_status_with_manifest(state, manifest)
    log_rows = _read_log_tail(config.DASHBOARD_LOG_LINES)
    page = _render_dashboard(manifest, state, log_rows, live=False,
                             controls=_SERVE_CONTROLS, notice=notice)
    return page.encode("utf-8")


def _serve_notice(result: dict, *, dry_run: bool) -> str:
    """Turn a _process_inbox() result into an HTML notice banner."""
    if not result.get("ok"):
        return f"<div class='notice err'>{html.escape(result.get('error', ''))}</div>"
    if result["files"] == 0:
        return ("<div class='notice'>Inbox empty — nothing to "
                + ("plan." if dry_run else "do.") + "</div>")
    c = result["counts"]
    if dry_run:
        return (f"<div class='notice warn'>Dry-run — would process "
                f"{result['files']} file(s): organized {c['organized']}, "
                f"ambiguous {c['ambiguous']}, unmatched {c['unmatched']}, "
                f"skipped {c['skipped']}. No files were moved.</div>")
    return (f"<div class='notice'>Run complete — processed {result['files']} "
            f"file(s): organized {c['organized']}, ambiguous {c['ambiguous']}, "
            f"unmatched {c['unmatched']}, skipped {c['skipped']}.</div>")


def _make_serve_handler(manifest_path: Path):
    """Build a BaseHTTPRequestHandler subclass bound to one manifest path."""

    class Handler(http.server.BaseHTTPRequestHandler):
        # Silence the default per-request stderr line; real events go to the
        # organizer log via _process_inbox().
        def log_message(self, *args):  # noqa: D401
            pass

        def _send_html(self, body: bytes, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path in ("/", "/index.html"):
                self._send_html(_serve_page(manifest_path))
            else:
                self._send_html(b"<h1>404</h1><p><a href='/'>back</a></p>", 404)

        def do_POST(self):  # noqa: N802
            # Drain any request body so the connection closes cleanly.
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            if self.path in ("/run", "/dry-run"):
                dry = self.path == "/dry-run"
                result = _process_inbox(manifest_path, dry_run=dry)
                notice = _serve_notice(result, dry_run=dry)
                self._send_html(_serve_page(manifest_path, notice=notice))
            else:
                self._send_html(b"<h1>404</h1><p><a href='/'>back</a></p>", 404)

    return Handler


def cmd_serve(args: argparse.Namespace) -> int:
    manifest_path = _p(args.manifest or config.MANIFEST_PATH)
    host = config.SERVE_HOST
    port = args.port if args.port is not None else config.SERVE_PORT

    try:
        httpd = http.server.HTTPServer((host, port), _make_serve_handler(manifest_path))
    except OSError as e:
        print(f"could not bind {host}:{port} — {e}", file=sys.stderr)
        print("something else may be using that port; retry with --port N",
              file=sys.stderr)
        return 1

    url = f"http://{host}:{port}/"
    print(f"organizer control panel: {url}")
    print("LOCAL ONLY (bound to 127.0.0.1). ctrl-c to stop.", file=sys.stderr)
    _log("INFO", "serve_start", url=url)

    if args.open:
        import webbrowser
        webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.", file=sys.stderr)
    finally:
        httpd.server_close()
        _log("INFO", "serve_stop", url=url)
    return 0


# ---------------------------------------------------------------------------
# Deliver — push manifest videos to a cloud remote via rclone
# ---------------------------------------------------------------------------
#
# For each manifest row, `deliver` runs:
#     rclone copyurl "<Video file URL>" "<remote>:<base>/<DVD Title>/<file>"
# which streams the scene straight from its CDN URL into the right title
# folder on the configured remote — no local disk round-trip. This is the
# scripted, batched replacement for transferring links by hand.
#
# delivery_status.json records every delivered row, so the command is safe
# to kill and restart: already-delivered rows are skipped.

def load_delivery_status() -> dict:
    p = _p(config.DELIVERY_STATE_FILE)
    if not p.exists():
        return {"rows": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _log("WARN", "delivery_status_corrupt_resetting", path=str(p))
        return {"rows": {}}


def save_delivery_status(state: dict) -> None:
    p = _p(config.DELIVERY_STATE_FILE)
    _ensure_dir(p.parent)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def _row_video_url(row: ManifestRow, manifest: Manifest) -> str:
    """The full (unmodified) Video file URL for a row, or '' if no such column."""
    col = manifest.column_map.get("SOURCE_FILENAME")
    if not col:
        return ""
    return (row.raw.get(col, "") or "").strip()


def _url_expiry(url: str) -> float | None:
    """
    Return the URL's `validto` expiry as a Unix timestamp, or None if absent.

    The Naughty America CDN links are signed with ?validfrom=...&validto=... —
    `validto` is when the link stops working. `deliver` checks this so it
    skips dead links instead of firing doomed transfers at them, and so a
    --dry-run tells you upfront which rows need fresh links.
    """
    m = re.search(r"[?&]validto=(\d+)", url)
    return float(m.group(1)) if m else None


def _translate_dvd_title(title: str) -> str:
    """
    Translate an AMG-abbreviated DVD title to the full series name Amy uses
    in her delivery folders. "ADD Vol. 24" -> "American Daydreams Vol. 24".

    The abbreviation is the first whitespace-delimited token; the rest
    ("Vol. 24") is appended unchanged. Unknown abbreviations are returned
    intact and logged so the operator can add them to config.STUDIO_FULL_NAMES.
    """
    parts = title.strip().split(None, 1)
    if not parts:
        return title
    abbr = parts[0]
    tail = parts[1] if len(parts) > 1 else ""
    full = config.STUDIO_FULL_NAMES.get(abbr)
    if full is None:
        _log("WARN", "title_no_translation", abbr=abbr, title=title)
        return title
    return f"{full} {tail}".rstrip()


def _delivery_dest(dvd_title: str, filename: str) -> str:
    """
    Build the delivery destination.

    Two modes, decided by config:

    * `DELIVERY_LOCAL_BASE` set (Drive-desktop-sync mode) — returns a plain
      filesystem path like /Users/.../My Drive/Amy Deliveries/Delivery 2/
      <Full DVD Title>/<file>. rclone copyurl writes to it directly; Drive's
      desktop app syncs the file up to the cloud.
    * `DELIVERY_LOCAL_BASE` empty (rclone-remote mode) — returns
      <RCLONE_REMOTE>:<base>/<Full DVD Title>/<file> for `rclone copyurl`.

    Either way, the DVD title is translated abbreviation -> full name first,
    so what lands matches Amy's reference structure.
    """
    full_title = _translate_dvd_title(dvd_title)
    if config.DELIVERY_LOCAL_BASE:
        return os.path.join(
            os.path.expanduser(config.DELIVERY_LOCAL_BASE), full_title, filename
        )
    parts = [p for p in (config.RCLONE_DEST_BASE.strip("/"),
                          full_title, filename) if p]
    return f"{config.RCLONE_REMOTE}:{'/'.join(parts)}"


def cmd_deliver(args: argparse.Namespace) -> int:
    manifest_path = _p(args.manifest or config.MANIFEST_PATH)
    manifest = load_manifest(manifest_path)
    if manifest.missing_required:
        if manifest.missing_required == ["__file_missing__"]:
            print(f"manifest not found at {manifest_path}", file=sys.stderr)
        else:
            print(f"manifest is missing required fields: {manifest.missing_required}",
                  file=sys.stderr)
        return 2
    if "SOURCE_FILENAME" not in manifest.column_map:
        print("manifest has no Video file / source URL column — nothing to deliver.",
              file=sys.stderr)
        print(f"detected columns: {manifest.columns_detected}", file=sys.stderr)
        return 2

    # rclone only needs to exist for a real run; --dry-run just prints the plan.
    if not args.dry_run and shutil.which(config.RCLONE_BINARY) is None:
        print(f"rclone not found on PATH (looked for '{config.RCLONE_BINARY}').",
              file=sys.stderr)
        print("install it with:  brew install rclone", file=sys.stderr)
        print("then configure a remote — see organizer/README.md.", file=sys.stderr)
        return 2

    state = load_delivery_status()
    rows_state = state.setdefault("rows", {})

    counts = {"delivered": 0, "already_done": 0, "failed": 0,
              "no_url": 0, "expired": 0}
    _log("INFO", "deliver_start", manifest=str(manifest_path),
         rows=len(manifest.rows), remote=config.RCLONE_REMOTE, dry_run=args.dry_run)

    for row in manifest.rows:
        if rows_state.get(row.row_id, {}).get("state") == "delivered":
            counts["already_done"] += 1
            continue

        url = _row_video_url(row, manifest)
        if not url:
            _log("WARN", "deliver_no_url", row=row.row_id)
            counts["no_url"] += 1
            continue

        # Pre-flight: a signed CDN link past its validto usually can't be
        # fetched. By default skip it (in dry-run too, so the plan is honest);
        # with --ignore-expiry, attempt anyway and let the CDN be the judge.
        expiry = _url_expiry(url)
        if expiry is not None and expiry < time.time():
            when = datetime.fromtimestamp(expiry, timezone.utc).date().isoformat()
            if not args.ignore_expiry:
                _log("WARN", "deliver_expired", row=row.row_id, expired=when)
                counts["expired"] += 1
                continue
            _log("WARN", "deliver_expired_forced", row=row.row_id, expired=when)

        filename = row.source_filename or url.split("?", 1)[0].rsplit("/", 1)[-1]
        dest = _delivery_dest(row.delivery_folder, filename)

        if args.dry_run:
            _log("INFO", "would_deliver", row=row.row_id, dst=dest)
            counts["delivered"] += 1
            continue

        cmd = [config.RCLONE_BINARY, "copyurl", url, dest]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=config.RCLONE_TIMEOUT_SECS)
        except subprocess.TimeoutExpired:
            _log("ERROR", "deliver_timeout", row=row.row_id, dst=dest)
            counts["failed"] += 1
            continue
        except OSError as e:
            _log("ERROR", "deliver_spawn_failed", row=row.row_id, error=repr(e))
            counts["failed"] += 1
            continue

        if result.returncode == 0:
            rows_state[row.row_id] = {
                "state": "delivered",
                "scene_id": row.scene_id,
                "delivery_folder": row.delivery_folder,
                "dest": dest,
                "delivered_at": _now(),
            }
            save_delivery_status(state)   # persist after each — restartable
            _log("INFO", "delivered", row=row.row_id, dst=dest)
            counts["delivered"] += 1
        else:
            err = (result.stderr or "").strip().replace("\n", " ")[:200]
            _log("ERROR", "deliver_failed", row=row.row_id, dst=dest, error=err)
            counts["failed"] += 1

    _log("INFO", "deliver_end", **counts, dry_run=args.dry_run)
    verb = "would deliver" if args.dry_run else "delivered"
    print(f"{verb} {counts['delivered']}, already done {counts['already_done']}, "
          f"expired {counts['expired']}, failed {counts['failed']}, "
          f"no-url {counts['no_url']} (of {len(manifest.rows)} rows)")
    if counts["expired"] and not counts["delivered"]:
        print("\nEvery deliverable row has an EXPIRED source URL — this manifest "
              "needs a\nfresh export with new links before anything can be "
              "delivered.", file=sys.stderr)
    return 0 if counts["failed"] == 0 else 1


# ---------------------------------------------------------------------------
# Verify — double-check that delivery actually landed
# ---------------------------------------------------------------------------
#
# After a `deliver` run, `verify` lists what is actually present at the
# rclone remote (via `rclone lsf -R`) and cross-checks against the manifest
# and delivery_status.json. It surfaces four categories:
#
#   confirmed   — manifest row delivered AND file present at remote (good)
#   missing     — marked delivered in state but NOT present at remote (BAD;
#                 means the upload was lost or the state file lies — these
#                 are the rows to re-deliver)
#   pending     — manifest row not yet delivered (just hasn't run)
#   extras      — files at the remote not in the manifest (other deliveries,
#                 manual uploads, leftovers; informational)
#
# Exits 0 if no `missing`, non-zero otherwise — so it's scriptable.

def _remote_listing(remote: str, base: str) -> tuple[list[str], str]:
    """
    Return (file_paths, error_message). Paths are relative to the delivery
    root (e.g. ['American Daydreams Vol. 24/file_qt.mp4', ...]).

    In local-path mode (config.DELIVERY_LOCAL_BASE set), walks the local
    folder tree — no rclone required.  In rclone-remote mode, calls
    `rclone lsf -R --files-only <remote>:<base>`.
    """
    if config.DELIVERY_LOCAL_BASE:
        root = Path(os.path.expanduser(config.DELIVERY_LOCAL_BASE)).resolve()
        if not root.exists():
            return [], f"local delivery base not found: {root}"
        try:
            out = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]
        except OSError as e:
            return [], f"could not walk {root}: {e}"
        return out, ""

    target = f"{remote}:{base.strip('/')}" if base.strip("/") else f"{remote}:"
    cmd = [config.RCLONE_BINARY, "lsf", "-R", "--files-only", target]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=config.RCLONE_TIMEOUT_SECS)
    except (subprocess.TimeoutExpired, OSError) as e:
        return [], f"could not run rclone: {e}"
    if result.returncode != 0:
        return [], (result.stderr or "rclone exited non-zero").strip()
    return [ln.strip() for ln in result.stdout.splitlines() if ln.strip()], ""


def cmd_verify(args: argparse.Namespace) -> int:
    manifest_path = _p(args.manifest or config.MANIFEST_PATH)
    manifest = load_manifest(manifest_path)
    if manifest.missing_required:
        if manifest.missing_required == ["__file_missing__"]:
            print(f"manifest not found at {manifest_path}", file=sys.stderr)
        else:
            print(f"manifest is missing required fields: {manifest.missing_required}",
                  file=sys.stderr)
        return 2
    if "SOURCE_FILENAME" not in manifest.column_map:
        print("manifest has no Video file / source URL column — nothing to verify.",
              file=sys.stderr)
        return 2

    # rclone only needed in remote-listing mode; local-path mode just walks
    # the synced folder tree directly.
    if not config.DELIVERY_LOCAL_BASE and shutil.which(config.RCLONE_BINARY) is None:
        print(f"rclone not found on PATH (looked for '{config.RCLONE_BINARY}').",
              file=sys.stderr)
        return 2

    # Expected map: "<Full DVD Title>/filename.mp4" -> row.
    # Use the translated (full) DVD title so the keys line up with what's
    # actually on disk / at the remote, which is also the translated form.
    expected: dict[str, ManifestRow] = {}
    for row in manifest.rows:
        url = _row_video_url(row, manifest)
        filename = row.source_filename or (
            url.split("?", 1)[0].rsplit("/", 1)[-1] if url else "")
        if not filename:
            continue
        key = f"{_translate_dvd_title(row.delivery_folder)}/{filename}"
        expected[key] = row

    state = load_delivery_status()
    delivered_rows = {rid for rid, v in state.get("rows", {}).items()
                      if v.get("state") == "delivered"}

    print(f"verifying {config.RCLONE_REMOTE}: against {manifest_path.name}",
          file=sys.stderr)
    _log("INFO", "verify_start", manifest=str(manifest_path),
         remote=config.RCLONE_REMOTE, expected=len(expected))

    files, err = _remote_listing(config.RCLONE_REMOTE, config.RCLONE_DEST_BASE)
    if err:
        print(f"remote listing failed: {err}", file=sys.stderr)
        _log("ERROR", "verify_listing_failed", error=err)
        return 2

    present = set(files)
    confirmed: list[ManifestRow] = []
    missing: list[ManifestRow] = []          # state says delivered, remote disagrees
    pending: list[ManifestRow] = []          # not in state
    for key, row in expected.items():
        at_remote = key in present
        is_delivered = row.row_id in delivered_rows
        if at_remote and is_delivered:
            confirmed.append(row)
        elif is_delivered and not at_remote:
            missing.append(row)
        elif not is_delivered:
            pending.append(row)
    expected_keys = set(expected.keys())
    extras = sorted(present - expected_keys)

    # Report
    print(f"\n=== {config.RCLONE_REMOTE}: vs {manifest_path.name} "
          f"({len(expected)} expected, {len(present)} present at remote) ===")
    print(f"  confirmed (delivered + present): {len(confirmed)}")
    print(f"  MISSING (delivered but absent):  {len(missing)}")
    print(f"  pending (not yet delivered):     {len(pending)}")
    print(f"  extras (present, not in manifest): {len(extras)}")

    if missing:
        print("\nMISSING — these rows say 'delivered' but the file isn't at the "
              "remote.\nRe-deliver by editing delivery_status.json and setting "
              "state back to\n'pending' for each, then running `deliver` again:")
        for row in missing:
            fname = row.source_filename or "?"
            print(f"  {row.row_id}  "
                  f"{_translate_dvd_title(row.delivery_folder)}/{fname}")
    if extras and args.show_extras:
        print(f"\nEXTRAS at remote (not in this manifest):")
        for f in extras[:30]:
            print(f"  {f}")
        if len(extras) > 30:
            print(f"  ...and {len(extras) - 30} more")

    _log("INFO", "verify_end",
         confirmed=len(confirmed), missing=len(missing),
         pending=len(pending), extras=len(extras))
    return 0 if not missing else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="organizer", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", help="Override manifest CSV path.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Print manifest + state summary.").set_defaults(func=cmd_status)

    r = sub.add_parser("run", help="Process the inbox once and exit.")
    r.add_argument("--dry-run", action="store_true", help="Plan only; touch no files.")
    r.set_defaults(func=cmd_run)

    w = sub.add_parser("watch", help="Poll the inbox forever.")
    w.set_defaults(func=cmd_watch)

    d = sub.add_parser("dashboard", help="Write an HTML status page from status.json.")
    d.add_argument("--watch", action="store_true",
                   help="Regenerate on a loop; the page auto-refreshes to match.")
    d.add_argument("--open", action="store_true",
                   help="Open the generated page in your default browser.")
    d.set_defaults(func=cmd_dashboard)

    s = sub.add_parser("serve",
                       help="Run a local web control panel (view + Run/Dry-run).")
    s.add_argument("--port", type=int, default=None,
                   help=f"Port to bind on 127.0.0.1 (default {config.SERVE_PORT}).")
    s.add_argument("--open", action="store_true",
                   help="Open the control panel in your default browser.")
    s.set_defaults(func=cmd_serve)

    v = sub.add_parser("deliver",
                       help="Push manifest videos to a cloud remote via rclone.")
    v.add_argument("--dry-run", action="store_true",
                   help="Print the planned rclone transfers; transfer nothing.")
    v.add_argument("--ignore-expiry", action="store_true",
                   help="Attempt expired-looking URLs anyway; let the CDN decide.")
    v.set_defaults(func=cmd_deliver)

    vf = sub.add_parser("verify",
                        help="List the remote and confirm every expected file is present.")
    vf.add_argument("--show-extras", action="store_true",
                    help="Also list files present at the remote that aren't in the manifest.")
    vf.set_defaults(func=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
