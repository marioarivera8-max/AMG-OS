"""
Timing utilities for AMG OS.

Provides:
- phase_timer: context manager that captures duration + supports timeout
- format_duration: human-readable duration strings
"""
import time
from contextlib import contextmanager

from amg.utils.logging import get_logger

log = get_logger("utils.timing")


class PhaseTimeoutError(Exception):
    """Raised when a phase exceeds its hard timeout."""
    pass


@contextmanager
def phase_timer(name, timeout_sec=None, on_complete=None):
    """
    Context manager for timing a phase of execution.

    Args:
        name: Phase name (for logging)
        timeout_sec: Optional hard timeout. NOTE: Does not interrupt mid-execution
                     (Python doesn't support that cleanly without threading).
                     Instead, code inside should check elapsed periodically.
        on_complete: Optional callback(name, duration_sec) called when block exits.

    Usage:
        with phase_timer("tier_1") as t:
            # ... work ...
            if t.elapsed > 60:
                break  # Manual timeout check
        print(f"Tier 1 took {t.elapsed:.1f}s")
    """
    class TimerHandle:
        def __init__(self):
            self.start = time.time()
            self.end = None
            self.timeout_sec = timeout_sec
            self.name = name

        @property
        def elapsed(self):
            return (self.end or time.time()) - self.start

        @property
        def is_over_budget(self):
            if self.timeout_sec is None:
                return False
            return self.elapsed > self.timeout_sec

    handle = TimerHandle()
    try:
        log.info(f"[{name}] start")
    except Exception:
        pass
    try:
        yield handle
    finally:
        handle.end = time.time()
        try:
            log.info(f"[{name}] done", duration_sec=round(handle.elapsed, 2))
        except Exception:
            pass
        if on_complete:
            try:
                on_complete(name, handle.elapsed)
            except Exception:
                pass  # Don't let logging callback break the actual work


def format_duration(seconds):
    """
    Format seconds as human-readable duration.

    Examples:
        format_duration(45)     -> "45s"
        format_duration(125)    -> "2m 5s"
        format_duration(3725)   -> "1h 2m 5s"
    """
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"


def format_timestamp_mmss(seconds):
    """Format seconds as MmSSs for filenames (e.g. '3m24s')."""
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    return f"{m}m{s:02d}s"
