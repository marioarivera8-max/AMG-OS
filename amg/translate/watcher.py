"""Scan-once watcher for Drive DROP folder (cron entrypoint)."""

from __future__ import annotations

import fcntl
import hashlib
import json
import shutil
import traceback
from datetime import date, datetime
from pathlib import Path

from amg.translate import config
from amg.translate.processor import process_workbook
from amg.translate.schema import SchemaError, validate_and_load
from amg.translate.state_io import atomic_write_json
from amg.translate.translate_log import translate_logger

_LOG = translate_logger()

_KNOWN_STATE_TOP = frozenset(
    {"archived_hashes", "current_files", "metrics", "last_success", "last_error"}
)


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _timestamp_compact() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _collision_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _short_hash(digest: str) -> str:
    return digest[:12]


def _load_state() -> dict:
    path = config.STATE_FILE
    if not path.exists():
        return _default_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_state()
    return _normalize_state(raw)


def _default_state() -> dict:
    return {
        "archived_hashes": [],
        "current_files": {},
        "metrics": {"date": None, "scans": 0, "files_processed": 0},
        "last_success": None,
        "last_error": None,
    }


def _normalize_state(raw: dict) -> dict:
    merged = _default_state()
    if isinstance(raw.get("archived_hashes"), list):
        merged["archived_hashes"] = raw["archived_hashes"]

    cf: dict[str, dict] = {}
    if isinstance(raw.get("current_files"), dict):
        for k, v in raw["current_files"].items():
            if isinstance(k, str) and isinstance(v, dict):
                cf[k] = v

    for k, v in raw.items():
        if k in _KNOWN_STATE_TOP:
            continue
        if isinstance(k, str) and isinstance(v, dict) and "size" in v and ("/" in k or "\\" in k):
            fname = Path(k).name
            prev = cf.get(fname, {})
            cf[fname] = {
                "size": int(v.get("size", 0)),
                "hash": prev.get("hash") or "",
                "first_seen": prev.get("first_seen") or _iso_now(),
                "last_seen": _iso_now(),
                "stable_scans": max(int(prev.get("stable_scans", 0)), int(v.get("count", v.get("stable_scans", 0)))),
            }

    merged["current_files"] = cf

    if isinstance(raw.get("metrics"), dict):
        merged["metrics"] = {**merged["metrics"], **raw["metrics"]}
    merged["last_success"] = raw.get("last_success", merged["last_success"])
    merged["last_error"] = raw.get("last_error", merged["last_error"])
    return merged


def _persist_state(state: dict) -> None:
    atomic_write_json(config.STATE_FILE, state)


def _uniq_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    c = _collision_ts()
    return dest.with_name(f"{dest.stem}_{c}{dest.suffix}")


def _archived_original_name_for_hash(state: dict, digest: str) -> str:
    for ent in reversed(state.get("archived_hashes") or []):
        if isinstance(ent, dict) and ent.get("hash") == digest:
            return str(ent.get("original_name") or "?")
    return "unknown"


class _WatcherLock:
    """Single-instance advisory lock."""

    __slots__ = ("path", "fp")

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fp = None

    def acquire_nonblocking(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fp = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(self.fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fp.seek(0)
            self.fp.truncate()
            self.fp.write(str(Path(__file__).resolve()))
            self.fp.flush()
            return True
        except BlockingIOError:
            try:
                self.fp.close()
            except OSError:
                pass
            self.fp = None
            return False

    def release(self) -> None:
        if self.fp is None:
            return
        try:
            fcntl.flock(self.fp.fileno(), fcntl.LOCK_UN)
        finally:
            try:
                self.fp.close()
            finally:
                self.fp = None


def _finalize_run_metrics(state: dict, processed_delta: int) -> None:
    metrics = dict(state.setdefault("metrics", {}))
    today = date.today().isoformat()
    if metrics.get("date") != today:
        metrics = {"date": today, "scans": 0, "files_processed": 0}
    metrics["scans"] = int(metrics.get("scans", 0)) + 1
    metrics["files_processed"] = int(metrics.get("files_processed", 0)) + int(processed_delta)
    state["metrics"] = metrics


def archive_bytes(state: dict) -> tuple[int, int]:
    root = config.drive_root()
    arc = root / config.ARCHIVE_FOLDER
    if not arc.exists():
        return 0, 0
    cnt = sum(1 for p in arc.iterdir() if p.is_file())
    sz = sum(p.stat().st_size for p in arc.iterdir() if p.is_file())
    return cnt, sz


def scan_once_inner(
    *,
    overwrite_titles: bool = False,
    use_claude: bool | None = None,
) -> list[Path]:
    """Core scan without flock / pause wrapper (usable from tests via patch)."""

    root = config.drive_root()
    drop = root / config.DROP_FOLDER
    output_dir = root / config.OUTPUT_FOLDER
    archive_dir = root / config.ARCHIVE_FOLDER
    errors_dir = root / config.ERRORS_FOLDER

    for d in (drop, output_dir, archive_dir, errors_dir, config.LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)

    claude_flag = config.anthropic_enabled() if use_claude is None else use_claude

    outputs: list[Path] = []
    processed_ok = 0
    state = _load_state()

    if not drop.exists():
        _finalize_run_metrics(state, processed_ok)
        _persist_state(state)
        return outputs

    hashes_index = [h for h in state.get("archived_hashes") or [] if isinstance(h, dict) and "hash" in h]
    archived_digest = {str(h["hash"]) for h in hashes_index}

    seen_names: set[str] = set()

    cf = dict(state.setdefault("current_files", {}))

    row: list[Path] = sorted(drop.iterdir())
    for path in row:
        if not path.is_file():
            continue
        name = path.name
        if name.startswith(".") or name.startswith("~"):
            continue
        if not name.lower().endswith(".xlsx"):
            continue
        if name.startswith("0 - TEMPLATE"):
            continue

        seen_names.add(name)

        digest = _md5_file(path)
        size = path.stat().st_size
        now = _iso_now()
        prev = cf.get(name, {})

        if prev.get("size") == size and prev.get("hash") == digest:
            stable = int(prev.get("stable_scans", 0)) + 1
        else:
            stable = 1

        first_seen = prev.get("first_seen") or now
        cf[name] = {
            "size": size,
            "hash": digest,
            "first_seen": first_seen,
            "last_seen": now,
            "stable_scans": stable,
        }

        if stable < config.STABILITY_SCANS_REQUIRED:
            continue

        stem = path.stem
        ts = _timestamp_compact()

        if digest in archived_digest:
            orig = _archived_original_name_for_hash(state, digest)
            dup_name = f"{stem}_duplicate_{_collision_ts()}{path.suffix}"
            dest = _uniq_dest(archive_dir / dup_name)
            try:
                shutil.move(str(path), str(dest))
            except OSError as e:
                _LOG.warning("DUPLICATE move failed for %s: %s", name, e)
                continue
            _LOG.info(
                "DUPLICATE: %s matches previously-archived %s (hash=%s); skipping reprocessing",
                name,
                orig,
                _short_hash(digest),
            )
            cf.pop(name, None)
            continue

        try:
            input_rows, _ = validate_and_load(path)
        except SchemaError as err:
            err_body = f"{err}\n"
            dest_xlsx = _uniq_dest(errors_dir / f"{stem}_FAILED_{ts}.xlsx")
            err_log = errors_dir / f"{dest_xlsx.stem}.error.log"
            try:
                shutil.move(str(path), str(dest_xlsx))
            except OSError:
                _LOG.exception("Failed to move schema-invalid file %s", name)
                continue
            try:
                err_log.write_text(err_body, encoding="utf-8")
            except OSError:
                pass
            _LOG.error("Schema validation failed for %s: %s", name, err)
            state["last_error"] = {"at": now, "detail": f"{name}: {err}"}
            cf.pop(name, None)
            continue

        row_count = len(input_rows)
        archive_path: Path | None = None
        archive_path = _uniq_dest(archive_dir / f"{stem}_ORIGINAL_{ts}.xlsx")
        translated = _uniq_dest(output_dir / f"{stem}_TRANSLATED_{ts}.xlsx")

        try:
            shutil.move(str(path), str(archive_path))
            process_workbook(
                archive_path,
                translated,
                overwrite_titles=overwrite_titles,
                use_claude=claude_flag,
            )
            outputs.append(translated)
            entry = {
                "hash": digest,
                "original_name": name,
                "archived_as": archive_path.name,
                "archived_at": _iso_now(),
                "row_count": row_count,
            }
            state.setdefault("archived_hashes", []).append(entry)
            archived_digest.add(digest)
            state["last_success"] = {
                "at": entry["archived_at"],
                "file": name,
                "rows": row_count,
            }
            processed_ok += 1
            cf.pop(name, None)
        except Exception:
            tb = traceback.format_exc()
            elog = errors_dir / f"{stem}_FAILED_{ts}.error.log"
            try:
                elog.write_text(tb, encoding="utf-8")
            except OSError:
                pass
            fail_xlsx_name = f"{stem}_FAILED_{ts}.xlsx"
            fail_path = errors_dir / fail_xlsx_name
            try:
                if archive_path is not None and archive_path.exists():
                    shutil.move(str(archive_path), str(_uniq_dest(fail_path)))
            except Exception:
                pass
            state["last_error"] = {"at": _iso_now(), "detail": f"{name}: processing failed"}
            cf.pop(name, None)

    stale = [k for k in list(cf.keys()) if k not in seen_names]
    for k in stale:
        cf.pop(k, None)

    state["current_files"] = cf
    _finalize_run_metrics(state, processed_ok)
    _persist_state(state)

    return outputs


def scan_once(
    *,
    overwrite_titles: bool = False,
    use_claude: bool | None = None,
) -> list[Path]:
    lk = _WatcherLock(config.WATCHER_LOCK_FILE)
    if not lk.acquire_nonblocking():
        return []

    try:
        if config.PAUSE_FLAG_FILE.exists():
            translate_logger().info("PAUSED, skipping run.")
            return []
        return scan_once_inner(overwrite_titles=overwrite_titles, use_claude=use_claude)
    finally:
        lk.release()


def load_public_state() -> dict:
    return _normalize_state(dict(_load_state()))


def main() -> None:
    outs = scan_once()
    if outs:
        for p in outs:
            print(p)
    else:
        print("No stable spreadsheets processed.")


if __name__ == "__main__":
    main()
