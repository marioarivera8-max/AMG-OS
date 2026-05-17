"""File logging for translate watchdog (translate.log)."""

from __future__ import annotations

import logging
from typing import Optional

_handlers_setup = False


def configure_translate_logging() -> logging.Logger:
    global _handlers_setup
    from amg.translate import config

    log = logging.getLogger("amg.translate.runtime")
    if _handlers_setup and log.handlers:
        return log
    log.handlers.clear()
    log.setLevel(logging.INFO)
    log.propagate = False
    path = config.LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    log.addHandler(fh)
    _handlers_setup = True
    return log


def translate_logger() -> logging.Logger:
    return configure_translate_logging()
