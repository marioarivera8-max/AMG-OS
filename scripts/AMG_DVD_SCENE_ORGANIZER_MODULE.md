# AMG DVD Scene Organizer Module

Purpose: turn Amy's Web VOD spreadsheet into delivery-ready Google Drive
folders with the same structure as the approved examples.

## What It Produces

For each spreadsheet delivery, the module creates:

```text
Amy Deliveries/
  Delivery 6/
    _AMY_REVIEW.html
    _delivery_manifest.csv
    _source_spreadsheet_with_paths.csv
    _companion_assets.csv
    _cloud_reports/
    Naughty Office Vol. 98/
      _folder_manifest.csv
      _folder_spreadsheet_rows.csv
      nocascatyler_qt.mp4
      nosyrenalex_qt.mp4
      ...
    Tonight's Girlfriend Vol. 121/
      ...
```

Every DVD/title folder keeps its own spreadsheet rows so the data travels with
the files. Companion asset links from the spreadsheet are recorded and routed
when usable. Missing video links and missing asset coverage are reported instead
of being hidden.

## Daily Command

```bash
cd /Users/mariorivera/AMG_OS

AMG_DVD_WORKERS=3 scripts/amg_dvd_scene_organizer.sh \
  "/Users/mariorivera/Downloads/Web VOD - Delivery 6.csv" \
  "Delivery 6"
```

If the spreadsheet filename already contains the delivery name, the second
argument can be omitted.

## Worker Rule

Start with `AMG_DVD_WORKERS=3`.

Increase to `4` only after the transfer timing report shows stable completion
without long stalls or Drive/CDN errors. If transfers hang, drop to `2` and rerun;
the module resumes by skipping files already visible in Google Drive.

The wrapper now always does two transfer passes:

1. A normal bulk pass using `AMG_DVD_WORKERS`.
2. A single-worker rescue pass with smaller chunks and a longer timeout.

The second pass skips completed files and only targets anything the faster pass
could not finish cleanly.

## Verification

After every run, check:

```bash
python3 scripts/amy_delivery.py \
  --manifest "/Users/mariorivera/Downloads/Web VOD - Delivery 6.csv" \
  --delivery-name "Delivery 6" \
  audit-cloud-layout

python3 scripts/amy_delivery.py \
  --manifest "/Users/mariorivera/Downloads/Web VOD - Delivery 6.csv" \
  --delivery-name "Delivery 6" \
  audit-video-specs
```

The cloud layout audit proves no scenes are loose at the root and no expected
title folder is empty. The video spec audit checks codec, resolution, frame
rate, time base, and audio profile by DVD/title group so future full-movie
compilation has a clean compatibility report.

The module also runs a Drive-root hygiene audit:

```bash
python3 scripts/amy_delivery.py \
  audit-drive-roots
```

That check fails if generated manifests, partial uploads, or MP4 files are loose
at the Drive root or `Amy Deliveries` root. Delivery files should only exist
inside:

```text
Amy Deliveries/<Delivery Name>/<DVD Title>/
```

## Inbox Shape

The intended Google Drive/Desktop inbox is:

```text
Amy Deliveries/
  DVD Scene Organizer Inbox/
    Web VOD - Delivery 6.csv
    _processed/
    _failed/
```

For now, run the command above against the dropped spreadsheet. The next module
step is a watcher that detects new sheets in this inbox and starts the same
command automatically.
