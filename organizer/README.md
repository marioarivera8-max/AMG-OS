# amg_organizer

A small, predictable file router and delivery tool for the AMG workflow. Files
dropped into `~/AMG_OS/inbox/` are matched against a manifest CSV and moved
into `~/AMG_OS/downloads/<DVD title>/` with the correct filename. The same
manifest can also push videos straight from their source URLs to a cloud
remote via `rclone`.

No content processing, no thumbnails — just routing and (optionally)
delivery.

---

## Folder layout

```
~/AMG_OS/
├── organizer/
│   ├── organizer.py
│   ├── config.py
│   └── README.md
├── manifest.csv              # ← you provide this
├── inbox/                    # drop files here
│   ├── _unmatched/           # auto-created; nothing matched
│   └── _ambiguous/           # auto-created; multiple matches, manual review
├── downloads/                # routed output
│   ├── ADD Vol. 24/
│   ├── ATH Vol. 23/
│   └── ...
└── organizer_logs/
    ├── status.json           # per-row organize state (source of truth)
    ├── organizer.log         # append-only TSV event log
    ├── dashboard.html        # generated HTML status page (see Dashboard)
    └── delivery_status.json  # per-row deliver state (separate from organize)
```

---

## CLI

```bash
# Inspect
python organizer.py status                  # manifest detection + per-state counts

# Organize (route inbox -> downloads/<DVD title>/)
python organizer.py run                     # process inbox once, exit
python organizer.py run --dry-run           # plan only — no files touched
python organizer.py watch                   # poll forever (Ctrl-C to stop)

# Dashboard (read-only HTML status page)
python organizer.py dashboard               # write organizer_logs/dashboard.html
python organizer.py dashboard --open        # ...and open it in your browser
python organizer.py dashboard --watch       # regenerate on a loop; page auto-refreshes

# Serve (local web control panel — Run / Dry-run / Refresh buttons)
python organizer.py serve                   # http://127.0.0.1:8765/
python organizer.py serve --open            # ...and open it in your browser
python organizer.py serve --port 9000       # bind a different port on 127.0.0.1

# Deliver (push manifest video URLs to a cloud remote via rclone)
python organizer.py deliver                 # transfer everything not yet delivered
python organizer.py deliver --dry-run       # print the planned rclone transfers
python organizer.py deliver --ignore-expiry # attempt expired-looking URLs anyway

# Global flag (works with every subcommand)
python organizer.py --manifest path.csv status
```

All subcommands accept `--manifest` to point at a non-default CSV.

---

## Manifest CSV

The manifest lives at `~/AMG_OS/manifest.csv` by default.

### Required columns

| logical name      | example column heading in the sheet | example value           |
| ----------------- | ----------------------------------- | ----------------------- |
| `SCENE_ID`        | `Scene ID`                          | `32389`                 |
| `DELIVERY_FOLDER` | `DVD Title`                         | `IHW Vol. 103`          |

If either is missing, `run` exits with a clear error and `status` prints the
detected columns so you can update `config.MANIFEST_COLUMNS`.

### Optional columns

| logical name      | example heading  | what it does                                              |
| ----------------- | ---------------- | --------------------------------------------------------- |
| `TARGET_FILENAME` | `Final Filename` | Rename the file on move. If absent, keep inbox basename.  |
| `SOURCE_FILENAME` | `Video file`     | Used for exact-name matching **and** as the source URL for `deliver`. A full `https://...` URL with a query string is fine — the organizer strips it to the basename for matching, and passes the full URL untouched to `rclone copyurl`. |
| `MATCH_REGEX`     | `match_regex`    | Any Python regex applied to the inbox basename.           |

### Exporting from Google Sheets

The organizer reads **CSV**, not HTML. From the sheet:

> File → Download → Comma-separated values (.csv)

…and save as `~/AMG_OS/manifest.csv`.

---

## Matching rules

For each inbox file, the three rules below are tried against **every**
manifest row. The set of rows that match decides where the file goes:

| rows matched                        | outcome                                            |
| ----------------------------------- | -------------------------------------------------- |
| more than 1                         | `_ambiguous/` + a `.candidates.txt` sidecar        |
| exactly 1, row still pending        | delivery folder for that row                       |
| exactly 1, row already filled       | `_unmatched/` (reason `row_already_filled`)        |
| 0                                   | `_unmatched/` (reason `no_match`)                  |

Matching is **order-independent**: it's a static property of the filename and
the manifest, not of what's already been organized. That means
`run --dry-run` predicts the real `run` exactly, and an earlier file can
never "steal" a row that a later file needed.

The three rules (each toggleable in `config.py`):

1. **Exact `SOURCE_FILENAME`** — case-insensitive, extension-optional.
   Best for delivery CDN URLs: paste e.g. `ihwsuttinryan_qt.mp4` into the
   `Video file` column and the file routes deterministically.
2. **Scene-ID token** — the `Scene ID` value appears in the filename as a
   whole token, bounded by start/end-of-string or one of `.`, `_`, `-`, or
   whitespace. This avoids a numeric ID like `32389` accidentally matching
   inside `323891` or inside a hash.
3. **Per-row regex** — fill `MATCH_REGEX` in the manifest if you need
   bespoke matching for a few rows.

> **Why no fuzzy matching?** If matching ever guesses wrong, files end up in
> the wrong delivery folder with no easy way to detect it. The
> unmatched/ambiguous bins are the safety net.

---

## Watch mode behavior

- Polls `inbox/` every `POLL_INTERVAL_SECS` (default **5s**).
- A file is only processed once its mtime is at least `STABILITY_WINDOW_SECS`
  old (default **10s**) **and** its size is steady across a short recheck.
  An in-progress download keeps bumping its own mtime, so it stays untouched
  until it goes quiet — that's how partial downloads are avoided.
- Hidden files (`.DS_Store`, dot-files) and anything outside
  `ALLOWED_EXTENSIONS` (default `.mp4 .mov .mkv .m4v .avi .wmv`) are ignored.
- Files smaller than `MIN_FILE_BYTES` (default **1024**) are ignored.

Reload the manifest by editing the CSV — `watch` re-reads it every pass,
no restart needed.

---

## Dashboard

`python organizer.py dashboard` writes a single self-contained HTML file to
`organizer_logs/dashboard.html` — open it in any browser. It's a read-only
view built from `status.json`, the manifest, and the tail of `organizer.log`;
generating it never touches your files or state.

The page shows:

- four count cards — organized, pending, unmatched, ambiguous;
- the detected manifest path, row count, and column map (with a red banner
  if the manifest is missing or missing a required field);
- a table of organized rows (scene ID, delivery folder, destination file);
- pending manifest rows still awaiting a file;
- the contents of `_unmatched/` (with the reason each file landed there) and
  `_ambiguous/` (with the candidate rows);
- the most recent `organizer.log` lines, newest first.

Flags:

- `--open` — also open the page in your default browser (uses Python's
  `webbrowser`; no network call).
- `--watch` — regenerate every `DASHBOARD_REFRESH_SECS` (default **10s**)
  and embed a matching `<meta refresh>` so the open browser tab reloads in
  step. Pair it with a `watch` process in another terminal for a live view.

Everything is inline — no external CSS, JS, fonts, or images — so the file
works offline and can be copied or emailed as a standalone snapshot.

---

## Serve — local control panel

`python organizer.py serve` runs a small web UI you can actually *operate*
the organizer from — not just look at. It serves the same status page as
`dashboard`, plus a control bar:

- **Run now** — process the inbox for real (moves files).
- **Dry-run** — show what a run *would* do; moves nothing.
- **Refresh** — re-read state and redraw.

After each action the page redraws with a banner summarising the result
(`organized 3, ambiguous 0, unmatched 1, …`). Run / Dry-run / Watch / the CLI
all go through the *same* `_process_inbox` code path — the panel can never
behave differently from the CLI.

```bash
python organizer.py serve            # then open http://127.0.0.1:8765/
python organizer.py serve --open     # opens the browser for you
python organizer.py serve --port 9000
```

Stop with Ctrl-C. If the port is busy you get a clear error and a `--port`
hint — nothing is left running.

> **Note on the "no network calls" rule.** The original spec asked for a
> pure local filesystem tool. `serve` is the **only** socket-opening part of
> the tool. It binds strictly to `127.0.0.1` (`config.SERVE_HOST`), makes no
> outbound connections, and is unreachable from any other machine — it's a
> local control surface, not a network service. This is a deliberate
> interpretation of the original constraint. If you'd rather keep the tool
> socket-free, simply don't run `serve` — `status`, `run`, `watch`,
> `dashboard`, and `deliver` never open a listening socket.

---

## Deliver — push to a cloud remote via rclone

`python organizer.py deliver` walks the manifest and, for each row, runs:

```
rclone copyurl "<Video file URL>" "<remote>:<DVD Title>/<file>"
```

That streams the scene **directly from its CDN URL into the cloud** — no
local disk round-trip, no waiting for files to land in `inbox/` first. It's
the scripted, batched replacement for transferring links by hand.

### One-time setup: install and configure rclone

`rclone` must be installed and a remote must be configured. The organizer
will not do this for you — but it gates `deliver` on it and prints the
install command if it's missing.

```bash
brew install rclone
rclone config
```

Then walk the interactive prompts for a Google Drive remote:

```
n              # New remote
name>          gdrive_amy        (must match config.RCLONE_REMOTE)
storage>       drive             (Google Drive)
client_id>                       (blank — accept default)
client_secret>                   (blank — accept default)
scope>         1                 (full access)
service_account_file>            (blank)
Edit advanced config? n
Use auto config?     y           (browser opens — sign into Google, click Allow)
Configure as a Shared Drive (Team Drive)? n
y/e/d>         y                 (confirm)
q              # Quit config
```

Then pin the remote at a specific Drive folder by its folder ID (so every
delivered file lands inside that folder, not at the Drive root):

```bash
rclone config update gdrive_amy root_folder_id <folder_id>
rclone lsd gdrive_amy:                  # smoke test — should list the folder contents
```

The folder ID is the last path segment in the Drive folder's URL.

### `--ignore-expiry`

The Naughty America CDN signs every `Video file` URL with `?validfrom=…&validto=…`
query params. After `validto`, the signature is rejected and the transfer
fails. By default `deliver` reads each URL's `validto`, **skips any row
whose link is already past expiry**, and reports the count as `expired`.
This keeps a `--dry-run` plan honest and avoids firing doomed transfers.

`--ignore-expiry` overrides that pre-flight check and attempts the transfer
anyway, letting the CDN be the judge. Useful when the timestamps look stale
but you want to verify rather than assume — e.g. after a clock drift or when
you suspect the export's `validto` is wrong but the link still works.

If every row reports `expired`, the manifest needs a fresh delivery export
from the content provider — there's nothing the tool can do about a dead
signature.

### Restartability

`delivery_status.json` (in `organizer_logs/`) records every successfully
delivered row, persisted **after each transfer**. Kill the command and
rerun: already-delivered rows are skipped. This file is separate from
`status.json` so organize-state and deliver-state never collide.

### Permissions caveat

The Google account you authorize in `rclone config` needs **edit** access to
the destination Drive folder. If transfers fail with permission errors,
that's a Drive sharing setting on the folder, not a `deliver` bug — the
folder owner has to grant you edit access. The tool cannot change Drive
sharing.

### Other backends

`rclone` supports 70+ backends — Dropbox, S3, Box, SFTP, WebDAV, etc.
Switching destinations is just `rclone config` for a new remote and pointing
`config.RCLONE_REMOTE` at the new name. The `deliver` command itself is
backend-agnostic.

---

## Safety guarantees

- **Never deletes.** Unmatched files are moved, not removed.
- **Never overwrites.** On collision, suffixes `_2`, `_3`, … are appended.
- **Atomic on same filesystem** (`os.replace`).
- **Cross-filesystem moves** copy → `fsync` → sha256-verify → unlink source.
- **Restartable organize.** `status.json` records every move; killing and
  restarting `run`/`watch` never re-moves or re-routes a finished row.
- **Restartable deliver.** `delivery_status.json` records every successful
  transfer (persisted after each row); rerunning `deliver` skips them.
- **Auditable.** `organizer.log` is append-only TSV:
  `ISO8601\tLEVEL\tEVENT\tkey=value\tkey=value …`. Every move, every
  delivery, every error.

---

## Configuring `config.py`

`config.py` is plain module-level constants — no classes, no decorators, no
plugins. Edit and re-run. The file is grouped into sections:

1. **Paths** — everything derives from `AMG_ROOT`. Set the `AMG_ROOT`
   environment variable to relocate the whole tree without editing the file
   (handy for tests). Each path (`MANIFEST_PATH`, `INBOX_DIR`,
   `DOWNLOADS_DIR`, `UNMATCHED_DIR`, `AMBIGUOUS_DIR`, `LOGS_DIR`,
   `LOG_FILE`, `STATUS_FILE`) is `AMG_ROOT/...`.
2. **`MANIFEST_COLUMNS`** — candidate-list-per-logical-field mapping.
   First case-insensitive match wins. Add your sheet's actual column
   headers here.
3. **Matching toggles** — `ENABLE_EXACT_SOURCE_MATCH`,
   `ENABLE_SCENE_ID_TOKEN_MATCH`, `ENABLE_PER_ROW_REGEX`.
4. **Watcher behavior** — `POLL_INTERVAL_SECS`, `STABILITY_WINDOW_SECS`,
   `MIN_FILE_BYTES`, `ALLOWED_EXTENSIONS`, `IGNORED_BASENAMES`.
5. **Dashboard** — `DASHBOARD_FILE`, `DASHBOARD_REFRESH_SECS`,
   `DASHBOARD_LOG_LINES`.
6. **Serve** — `SERVE_HOST` (locked to `127.0.0.1`), `SERVE_PORT` (default
   `8765`).
7. **Deliver / rclone** — `RCLONE_BINARY` (CLI path; just `"rclone"` if it's
   on PATH), `RCLONE_REMOTE` (the configured remote name, e.g.
   `gdrive_amy`), `RCLONE_DEST_BASE` (optional subfolder inside the remote;
   `""` = remote root), `RCLONE_TIMEOUT_SECS` (per-transfer timeout),
   `DELIVERY_STATE_FILE`.

---

## Troubleshooting

| symptom                                          | fix                                                                |
| ------------------------------------------------ | ------------------------------------------------------------------ |
| `manifest is missing required fields`            | Add your actual column heading to the right list in `config.MANIFEST_COLUMNS`. |
| Everything lands in `_unmatched/`                | Likely no `SOURCE_FILENAME` and scene IDs don't appear in filenames. Either paste the CDN basename into a `Video file` column, or add a `match_regex` column. |
| A file landed in `_ambiguous/`                   | Open the `.candidates.txt` sidecar, pick the right row, move the file into the matching `downloads/<folder>/` manually. |
| A file in `_unmatched/` with reason `row_already_filled` | Its manifest row was already filled by an earlier file. Check whether it's a duplicate or a genuinely different file; place or discard it by hand. |
| Need to redo a row                               | Edit `status.json`: set the row's `state` back to `"pending"` and remove the moved file from its delivery folder. Re-run. |
| File appears identical but won't match           | Check for invisible characters in `Scene ID` or `Video file` cells. Trim in Sheets and re-export. |
| File grabbed before its download finished        | Rare — happens only if your transfer tool back-dates the file's mtime. Increase `STABILITY_WINDOW_SECS`, or have the acquisition step write to a temp name and rename into `inbox/` when complete. |
| All `deliver` rows reported as `expired`         | The CDN signed links are past their `validto`. Either pass `--ignore-expiry` to try anyway, or request a fresh delivery export from the content provider — there's no way to revive a dead signature. |
| `rclone not found on PATH`                       | `brew install rclone`, then `rclone config` to set up a remote (see Deliver section). |
| `deliver_failed` with permission errors          | The destination Drive folder's sharing settings deny write. You need **edit** access; ask the folder owner to grant it. The tool cannot change Drive sharing. |

---

## Out of scope

- **Acquisition** — how files get into `inbox/` (SFTP, browser downloads,
  Drive sync, etc.) is upstream of this tool.
- **Content processing** — thumbnails, cover art, PSD generation, 2257
  records, scene metadata. The AMG OS pipeline handles all of that.
- **Upstream sheet generation** — `deliver` consumes the manifest CSV; it
  doesn't produce or modify it. The Google Sheet that becomes
  `manifest.csv` is curated by hand.
