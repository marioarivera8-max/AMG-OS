# AMG DVD Scene Organizer Module

Purpose: turn Amy's Web VOD spreadsheet into delivery-ready Google Drive
folders with the same structure as the approved examples.

Operator rule: work local first. Process manifests, audits, queues, browser
download temp files, and transfer receipts are local-only under
`~/AMG_OS/delivery_work/`. They are not Amy-facing deliverables and should not
appear at Google Drive root, `Amy Deliveries` root, T9, or any mirror drive.

## What It Produces

For each spreadsheet delivery, the module creates:

```text
Amy Deliveries/
  Delivery 6/
    Naughty Office Vol. 98/
      nocascatyler_qt.mp4
      nosyrenalex_qt.mp4
      ...
    Tonight's Girlfriend Vol. 121/
      ...
```

Every DVD/title folder contains only delivery assets. Spreadsheet rows,
companion asset maps, review HTML, transfer logs, and audits stay in
`~/AMG_OS/delivery_work/<Delivery Name>/`. Missing video links and missing asset
coverage are reported there instead of being hidden.

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
  audit-file-proof

python3 scripts/amy_delivery.py \
  --manifest "/Users/mariorivera/Downloads/Web VOD - Delivery 6.csv" \
  --delivery-name "Delivery 6" \
  audit-video-specs
```

The cloud layout audit proves no scenes are loose at the root and no expected
title folder is empty. The file proof audit writes file size and SHA-256 for
each expected local scene file. The video spec audit checks codec, resolution,
frame rate, time base, and audio profile by DVD/title group so future
full-movie compilation has a clean compatibility report.

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
