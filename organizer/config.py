"""
Configuration for the AMG delivery file organizer.

Plain module-level constants — no classes, no clever loading.
Edit values directly. Anything path-shaped accepts ~ and is resolved at use time.
"""

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
#
# Everything derives from AMG_ROOT. The env var AMG_ROOT, if set, wins —
# useful for tests and for relocating the tree without editing this file.

import os as _os

AMG_ROOT = _os.environ.get("AMG_ROOT", "~/AMG_OS")

# Where the manifest CSV lives. Override on the CLI with --manifest.
MANIFEST_PATH = _os.path.join(AMG_ROOT, "manifest.csv")

# Watch folder. New files dropped here get matched and routed.
INBOX_DIR = _os.path.join(AMG_ROOT, "inbox")

# Output root. Each manifest row's `delivery_folder` becomes a subdirectory here.
DOWNLOADS_DIR = _os.path.join(AMG_ROOT, "downloads")

# Quarantine subfolders inside the inbox for files we won't move automatically.
UNMATCHED_DIR = _os.path.join(AMG_ROOT, "inbox", "_unmatched")
AMBIGUOUS_DIR = _os.path.join(AMG_ROOT, "inbox", "_ambiguous")

# Logs and state.
LOGS_DIR = _os.path.join(AMG_ROOT, "organizer_logs")
LOG_FILE = _os.path.join(AMG_ROOT, "organizer_logs", "organizer.log")
STATUS_FILE = _os.path.join(AMG_ROOT, "organizer_logs", "status.json")


# ---------------------------------------------------------------------------
# Manifest column mapping
# ---------------------------------------------------------------------------
#
# Each entry below maps a logical field used by the organizer to a list of
# candidate column names from the CSV. The first match (case-insensitive,
# whitespace-trimmed) wins. Add the actual column names from your Google
# Sheet export here — that is the main customization point.
#
# Required fields: SCENE_ID, DELIVERY_FOLDER.
# Optional fields: TARGET_FILENAME, SOURCE_FILENAME, MATCH_REGEX.
#   - TARGET_FILENAME absent -> the file keeps its original inbox basename.
#   - SOURCE_FILENAME / MATCH_REGEX absent -> those matching rules are skipped,
#     leaving only the scene-ID token rule (see "Matching rules" below).
#
# If a required logical field has no matching column, `status` and `run`
# print the detected columns and exit cleanly without touching files.

MANIFEST_COLUMNS = {
    # Observed in the Web VOD - Delivery 5 sample: "Scene ID" (5-digit int).
    "SCENE_ID":        ["scene_id", "scene id", "id", "scene", "scene #", "scene_number"],
    # Observed: "DVD Title" — format "<STUDIO_ABBR> Vol. <N>" (e.g. "IHW Vol. 103").
    "DELIVERY_FOLDER": ["delivery_folder", "delivery folder", "dvd", "dvd title", "title", "folder"],
    # Not present in the Delivery 5 sample — if absent, the organizer keeps
    # the original inbox basename. Add this column to enforce a rename on move.
    "TARGET_FILENAME": ["target_filename", "final_filename", "filename", "final file name", "output filename"],
    # Recommended: paste the basename of the "Video file" URL (e.g.
    # "ihwsuttinryan_qt.mp4") into this column for exact-match routing.
    "SOURCE_FILENAME": ["source_filename", "source file", "input_filename", "original_filename", "video file"],
    # Optional escape hatch: any Python regex applied to the inbox basename.
    "MATCH_REGEX":     ["match_regex", "regex", "filename_regex"],
}


# ---------------------------------------------------------------------------
# Matching rules
# ---------------------------------------------------------------------------
#
# Order matters — first rule that produces a unique match wins. If multiple
# manifest rows match under the same rule, the file is routed to _ambiguous/.
# All comparisons are case-insensitive and ignore the file extension on
# the inbox side (.mp4, .mov, etc.).

# Rule 1: exact match against SOURCE_FILENAME column, if present.
ENABLE_EXACT_SOURCE_MATCH = True

# Rule 2: scene ID appears as a token in the filename.
# A token boundary is start/end-of-string, ".", "_", "-", or whitespace.
ENABLE_SCENE_ID_TOKEN_MATCH = True

# Rule 3: per-row regex from MATCH_REGEX column. Applied with re.IGNORECASE
# to the basename (extension included).
ENABLE_PER_ROW_REGEX = True


# ---------------------------------------------------------------------------
# Watcher behavior
# ---------------------------------------------------------------------------

# Poll interval for `watch` mode (seconds).
POLL_INTERVAL_SECS = 5

# A new file must show the same size for this many seconds before we touch it,
# so we don't move partial downloads.
STABILITY_WINDOW_SECS = 10

# Files smaller than this are ignored entirely (bytes). Set to 0 to disable.
MIN_FILE_BYTES = 1024

# File extensions we'll consider. Empty set = consider everything.
# Lowercase, with leading dot. Add what you actually deliver.
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".wmv"}

# Hidden / system files we skip outright (exact basename match).
IGNORED_BASENAMES = {".DS_Store", "Thumbs.db", ".localized"}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

# Where `dashboard` writes its self-contained HTML status page.
DASHBOARD_FILE = _os.path.join(AMG_ROOT, "organizer_logs", "dashboard.html")

# In `dashboard --watch` mode, regenerate the page this often (seconds).
# The page embeds a matching <meta refresh> so the browser reloads in step.
DASHBOARD_REFRESH_SECS = 10

# How many recent log lines to show in the dashboard's activity panel.
DASHBOARD_LOG_LINES = 60


# ---------------------------------------------------------------------------
# Serve — local web control panel
# ---------------------------------------------------------------------------

# `serve` binds here. Keep it on loopback: this panel can MOVE FILES, so it
# must never be reachable from outside this machine.
SERVE_HOST = "127.0.0.1"
SERVE_PORT = 8765


# ---------------------------------------------------------------------------
# Deliver — push files to a cloud destination via rclone
# ---------------------------------------------------------------------------
#
# `deliver` reads a manifest and, per row, runs `rclone copyurl` to stream the
# scene's video URL straight into <remote>:<DVD Title>/<file>. No local disk
# round-trip. rclone handles the cloud auth and 70+ backends, so the same
# command delivers to Google Drive today and Dropbox/S3/Box/SFTP later — just
# point RCLONE_REMOTE at a different configured remote.
#
# One-time setup (the operator does this — see organizer/README.md):
#   brew install rclone
#   rclone config      # add a "drive" remote, authorize Google, name it below

# The rclone CLI binary. Just "rclone" if it's on PATH.
RCLONE_BINARY = "rclone"

# The configured rclone remote that points at the delivery destination.
# For Amy's Google Drive: make a `drive` remote whose root_folder_id is her
# shared folder, and name it here.
RCLONE_REMOTE = "gdrive_amy"

# Optional subfolder path inside the remote. "" = the remote's root.
RCLONE_DEST_BASE = ""

# Per-transfer timeout in seconds. Large videos over a slow link need room.
RCLONE_TIMEOUT_SECS = 3600

# Where `deliver` records what's been pushed — separate from the local
# status.json so organize-state and deliver-state never collide.
DELIVERY_STATE_FILE = _os.path.join(AMG_ROOT, "organizer_logs", "delivery_status.json")

# Local-path delivery mode. If set, `deliver` writes files to this local
# folder (creating <Full DVD Title>/<file> beneath it) instead of pushing
# to a cloud remote. Use this when the Google Drive desktop app is
# installed: point this at a folder inside ~/Library/CloudStorage/
# GoogleDrive-<email>/My Drive/, and Drive's app syncs the writes up to
# the cloud. No rclone Google OAuth needed — rclone just runs locally.
# Leave as "" to use the rclone-remote mode (RCLONE_REMOTE) instead.
DELIVERY_LOCAL_BASE = ""


# ---------------------------------------------------------------------------
# Studio abbreviation -> full DVD-line title
# ---------------------------------------------------------------------------
#
# AMG manifests carry DVD titles in abbreviated form ("ADD Vol. 24"). Amy's
# Drive delivery folders use the full series name ("American Daydreams
# Vol. 24"). `deliver` and `verify` translate the manifest's abbreviation
# to the full name when building the title-folder path, so what lands in
# Drive matches Amy's shape and her existing Delivery 1 reference.
#
# Add rows as new abbreviations appear in future manifests. Anything not
# in this map passes through unchanged and is logged as `title_no_translation`
# so you can see what to add.

STUDIO_FULL_NAMES = {
    "ADD":   "American Daydreams",
    "ATH":   "Naughty Athletics",
    "NATH":  "Naughty Athletics",          # CDN-URL prefix variant
    "DWC":   "Dirty Wives Club",
    "NADWC": "Dirty Wives Club",           # CDN-URL prefix variant
    "IHW":   "I Have a Wife",
    "MDHF":  "My Daughter's Hot Friend",
    "MDHG":  "My Dad's Hot Girlfriend",
    "MFHG":  "My Friend's Hot Girl",
    "MFHM":  "My Friend's Hot Mom",
    "MFST":  "My First Sex Teacher",
    "MSHF":  "My Sister's Hot Friend",
    "MWHF":  "My Wife's Hot Friend",
    "NAPFS": "Perfect Fucking Strangers",
    "TNGF":  "Tonight's Girlfriend",
    # TODO: confirm full title for these against Naughty America's actual catalog
    # before delivering. Currently they'll fall through with the abbreviation
    # and produce a `title_no_translation` log line.
    # "NAF":   "Naughty <?>",
    # "NO":    "Naughty Office",
}
