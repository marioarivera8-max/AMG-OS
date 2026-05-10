"""
AMG OS Logging — Three streams:

1. STRUCTURED:  JSON Lines, machine-readable, for analyzer/learning system
   Path: data/logs/structured/{YYYY-MM-DD}.jsonl

2. RUN:         Per-scene human-readable text log
   Path: data/logs/runs/{scene_id}_{timestamp}.log

3. CONSOLE:     What operator sees in terminal (formatted for humans)

Usage:
    from amg.utils.logging import get_logger
    log = get_logger(__name__)
    log.info("Starting tier 1 scan")
    log.warn("Sharpness floor low", sharp_floor=120)
    log.error("AI call failed", error_code="E_AI_TIMEOUT")
"""
import json
import logging
import sys
import io
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from amg.config import LOGS_DIR, LOG_LEVEL, LOG_DATE_FORMAT


# Module-level state
_initialized = False
_current_run_log_path: Optional[Path] = None


def _configure_text_stream(stream):
    try:
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
        return stream
    except Exception:
        pass
    try:
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            return io.TextIOWrapper(buffer, encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass
    return stream


def init_logging(level: str = None, run_log_name: Optional[str] = None) -> None:
    """
    Initialize logging streams. Called once at process start (or per scene for run logs).

    Args:
        level: Override default log level.
        run_log_name: If provided, opens a per-run log file at data/logs/runs/{name}.log
    """
    global _initialized, _current_run_log_path

    log_level = getattr(logging, (level or LOG_LEVEL).upper(), logging.INFO)

    # Console handler (human-readable)
    console = logging.StreamHandler(_configure_text_stream(sys.stdout))
    console.setLevel(log_level)
    console.setFormatter(_HumanFormatter())

    # Structured JSONL handler (daily file)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    structured_dir = LOGS_DIR / "structured"
    structured_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    structured_path = structured_dir / f"{today}.jsonl"
    structured_handler = logging.FileHandler(structured_path, encoding="utf-8")
    structured_handler.setLevel(logging.DEBUG)  # Capture everything in structured log
    structured_handler.setFormatter(_JSONFormatter())

    # Reset root logger
    root = logging.getLogger("amg")
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(structured_handler)
    root.propagate = False

    # Optional per-run log
    if run_log_name:
        runs_dir = LOGS_DIR / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Sanitize run_log_name
        safe_name = "".join(c if c.isalnum() or c in "_-." else "_" for c in run_log_name)[:80]
        run_path = runs_dir / f"{safe_name}_{ts}.log"
        run_handler = logging.FileHandler(run_path, encoding="utf-8")
        run_handler.setLevel(logging.DEBUG)
        run_handler.setFormatter(_HumanFormatter())
        root.addHandler(run_handler)
        _current_run_log_path = run_path

    _initialized = True


def get_logger(name: str) -> "AMGLogger":
    """
    Get a logger for a module. Auto-initializes if not done yet.

    The returned logger supports keyword arguments which become structured fields
    in the JSON log:

        log.info("Tier 1 complete", tier=1, candidates_found=15)
    """
    if not _initialized:
        init_logging()
    return AMGLogger(logging.getLogger(f"amg.{name}"))


class AMGLogger:
    """
    Wrapper around Python's logger that supports structured kwargs.

    Use kwargs for machine-readable structured fields:
        log.info("Scoring frame", timestamp=123.4, score=8.5)
    """
    def __init__(self, py_logger):
        self._log = py_logger

    def _log_with_extras(self, level, msg, **kwargs):
        # kwargs go into the LogRecord for the JSON formatter
        extra = {"_extras": kwargs} if kwargs else {}
        self._log.log(level, msg, extra=extra)

    def debug(self, msg, **kwargs):
        self._log_with_extras(logging.DEBUG, msg, **kwargs)

    def info(self, msg, **kwargs):
        self._log_with_extras(logging.INFO, msg, **kwargs)

    def warn(self, msg, **kwargs):
        self._log_with_extras(logging.WARNING, msg, **kwargs)

    warning = warn  # Alias

    def error(self, msg, **kwargs):
        self._log_with_extras(logging.ERROR, msg, **kwargs)

    def fatal(self, msg, **kwargs):
        self._log_with_extras(logging.CRITICAL, msg, **kwargs)

    critical = fatal  # Alias


class _HumanFormatter(logging.Formatter):
    """Human-readable formatter for console + run logs."""

    LEVEL_COLORS = {
        "DEBUG": "\033[90m",      # Gray
        "INFO": "\033[37m",       # White
        "WARNING": "\033[33m",    # Yellow
        "ERROR": "\033[31m",      # Red
        "CRITICAL": "\033[35m",   # Magenta
    }
    RESET = "\033[0m"

    def __init__(self):
        super().__init__(fmt="%(asctime)s %(levelname)s %(message)s",
                         datefmt=LOG_DATE_FORMAT)

    def format(self, record):
        formatted = super().format(record)
        # Append structured fields if present
        extras = getattr(record, "_extras", None)
        if extras:
            kv = " ".join(f"{k}={v}" for k, v in extras.items())
            formatted += f" [{kv}]"
        return formatted


class _JSONFormatter(logging.Formatter):
    """JSON Lines formatter for structured log."""

    def format(self, record):
        obj = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }
        # Merge structured fields if present
        extras = getattr(record, "_extras", None)
        if extras:
            obj.update(extras)
        # Add exception info if present
        if record.exc_info:
            obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(obj, default=str)
