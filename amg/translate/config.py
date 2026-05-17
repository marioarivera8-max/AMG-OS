"""Configuration for the translation module."""
from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()

DRIVE_ROOT = Path(
    os.environ.get(
        "AMG_TRANSLATE_ROOT",
        HOME
        / "Library"
        / "CloudStorage"
        / "GoogleDrive-marioarivera8@gmail.com"
        / "My Drive"
        / "TRANSLATE SPREADSHEET MODULE",
    )
)

TEMPLATE_NAME = "0 - TEMPLATE - Copy This.xlsx"
README_NAME = "READ ME FIRST.md"
DROP_FOLDER = "1 - DROP SPREADSHEET HERE"
OUTPUT_FOLDER = "2 - TRANSLATED SPREADSHEETS"
ARCHIVE_FOLDER = "ARCHIVE - ORIGINALS"
ERRORS_FOLDER = "ERRORS - FIX AND RE-DROP"

INCOMING_DIR = DRIVE_ROOT / DROP_FOLDER
OUTGOING_DIR = DRIVE_ROOT / OUTPUT_FOLDER
FAILED_DIR = DRIVE_ROOT / ERRORS_FOLDER
ARCHIVE_DIR = DRIVE_ROOT / ARCHIVE_FOLDER

AMG_OS_ROOT = Path(os.environ.get("AMG_OS_ROOT", HOME / "AMG_OS"))
LOG_DIR = AMG_OS_ROOT / "logs"
LOG_FILE = LOG_DIR / "translate.log"

STATE_FILE = AMG_OS_ROOT / ".translate_state.json"
SPEND_TRACKER_FILE = AMG_OS_ROOT / ".translate_spend.json"
WATCHER_LOCK_FILE = AMG_OS_ROOT / ".translate_watcher.lock"
PAUSE_FLAG_FILE = AMG_OS_ROOT / ".translate_paused"


def preserve_existing(overwrite_cli_flag: bool) -> bool:
    """When False, regenerate title/description/tags even if cells were populated."""
    if overwrite_cli_flag:
        return False
    return os.environ.get("AMG_TRANSLATE_OVERWRITE", "0") != "1"


PRESERVE_EXISTING = preserve_existing(False)

USE_CLAUDE_POLISH = os.environ.get("AMG_TRANSLATE_CLAUDE", "1") == "1"
CLAUDE_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

CLAUDE_MODEL = os.environ.get("AMG_TRANSLATE_MODEL", "claude-sonnet-4-20250514")
CLAUDE_MAX_TOKENS = 4096
CLAUDE_BATCH_SIZE = 10

MAX_ROWS_PER_FILE = int(os.environ.get("AMG_TRANSLATE_MAX_ROWS", "2000"))
MAX_API_BATCHES_PER_RUN = int(os.environ.get("AMG_TRANSLATE_MAX_API_BATCHES", "50"))

DAILY_SPEND_CAP_USD = float(os.environ.get("AMG_TRANSLATE_DAILY_SPEND_CAP_USD", "5.00"))
ESTIMATED_COST_PER_API_BATCH_USD = float(os.environ.get("AMG_TRANSLATE_BATCH_COST_USD", "0.03"))

STABILITY_SCANS_REQUIRED = 2
CRON_INTERVAL_SECONDS = 120

TITLE_MIN_WORDS = 5
TITLE_MAX_WORDS = 10
DESC_MIN_WORDS = 8
DESC_MAX_WORDS = 14
TAGS_MIN = 3
TAGS_MAX = 6


def drive_root() -> Path:
    return DRIVE_ROOT


def anthropic_enabled() -> bool:
    return USE_CLAUDE_POLISH and bool(CLAUDE_API_KEY.strip())


def ensure_drive_dirs() -> None:
    for d in (INCOMING_DIR, OUTGOING_DIR, FAILED_DIR, ARCHIVE_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
