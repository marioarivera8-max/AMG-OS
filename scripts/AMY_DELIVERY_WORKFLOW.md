# Amy Delivery Workflow

This is the repeatable path for Amy's spreadsheet deliveries:

1. Export Amy's Google Sheet as XLSX for final delivery work. CSV is acceptable
   only for video-only or metadata-only passes.
2. Validate the XLSX/CSV.
3. Create one local folder per DVD title.
4. Open a download queue for manual studio download.
5. Review the report.
6. Upload the complete delivery folder to `gdrive_amy:` only after review.

The script can work local-first or directly into the Google Drive for Desktop
folder. When Amy's sheet contains valid signed URLs, the automation downloads
the scene files and any companion asset URLs directly into the delivery shape.
If a sheet only contains video URLs, the asset audit will call out the missing
Delivery 1-style companion files instead of silently treating the delivery as
complete.

The spreadsheet data travels with the delivery:

- `_delivery_manifest.csv` at the delivery root contains every row and its
  expected sorted path.
- Each DVD folder contains `_folder_manifest.csv` with only that title's
  scenes.
- These manifests update after prepare/import/report-oriented actions, so
  they show whether each expected file is present.
- `_companion_assets.csv` records any 2257, model-release, sleeve, PSD, cover,
  or compliance asset URLs found in the spreadsheet.
- `_asset_audit.csv` checks the folder against the Delivery 1 pattern: sleeve
  PSD in the DVD root, DVD-level model-release PDF, and 2257/model-release
  coverage for each person present in each scene.

## Target Drive Shape

The generated folders mirror Amy's ready-for-delivery Drive layout:

```text
Delivery 3/
  American Daydreams Vol. 26/
    2257/
      ... Model Releases.pdf
    addreagankyle_qt.mp4
    ... SLEEVE.psd
    ...
  Dirty Wives Club Vol. 47/
    2257/
    nadwcblakeryan_qt.mp4
    ...
```

The sheet shorthand is expanded for folder names. For example:

- `ADD Vol. 26` -> `American Daydreams Vol. 26`
- `DWC Vol. 45` / `NADWC Vol. 47` -> `Dirty Wives Club Vol. 45/47`
- `IHW Vol. 101` -> `I Have a Wife Vol. 101`
- `MSHF Vol. 119` -> `My Sister's Hot Friend Vol. 119`

Scene video filenames stay as the studio/download filename, matching the
existing delivered folders.

Rows where the `DVD Title` cell is `x` or blank inherit the previous real DVD
title, matching Amy's Delivery 1 spreadsheet format.

## Source File Rules

Amy's expanded sheet matters. The complete source includes:

```text
DVD Title
Scene ID
Scene Publication
Scene Title
Video file
Meta-data
Cover Art & 2257 Compliance records
upload/status columns
```

Use `.xlsx` for the final pass because Google Sheets CSV exports visible text
only. If column F displays a folder name with a hidden Google Drive hyperlink,
CSV keeps only the folder name and loses the link. The script reads `.xlsx`
files directly and stores hidden hyperlinks as additional `__link` fields in
the delivery manifests.

Use CSV only when:

- the source cells visibly contain plain URLs, or
- you only need to refresh videos/descriptions and no hidden links.

## Quick Start

```bash
cd ~/AMG_OS

python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  validate

python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  prepare

python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  queue
```

Open the printed `_download_queue.html` file. In the authenticated studio
browser/session, right-click each URL and download/save the file into:

```text
~/AMG_OS/delivery_intake/
```

Then route the downloaded files:

```bash
python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  import
```

Or leave the router running while you download:

```bash
python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  watch-import
```

With `watch-import` running, right-click-download files into
`~/AMG_OS/delivery_intake/`; after each file finishes and sits stable for a
few seconds, the script moves it into the correct DVD folder.

Then check the delivery:

```bash

python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  report

python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  audit-data

python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  audit-assets
```

The local output will be:

```text
~/AMG_OS/deliveries/Delivery 3/
  IHW Vol. 103/
  TNGF Vol. 149/
  ...
```

## Upload To Amy's Drive

First do a dry-run:

```bash
python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  upload --dry-run
```

Then run the real upload:

```bash
python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  upload
```

After upload, generate the Drive link:

```bash
python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  link
```

By default this copies to:

```text
gdrive_amy:Amy Deliveries/Delivery 3
```

## Cloud-First Mode

If you want Drive to be the working destination instead of a local delivery
folder, use the cloud commands.

Preview the cloud folder plan:

```bash
python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  cloud-plan
```

Create the folder structure directly on Google Drive:

```bash
python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  cloud-mkdirs
```

Try a direct cloud URL transfer for one row:

```bash
python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  cloud-copyurls --limit 1
```

If the studio URLs work without browser-only cookies, `cloud-copyurls` can
stream the scene files straight to `gdrive_amy:` without storing them in the
local delivery folder. If the studio blocks those URLs outside the browser
session, use the manual right-click path and target a Google Drive for
Desktop synced intake folder.

## Browser-Authenticated Download

For the daily studio workflow, use the browser downloader. It opens a real
browser profile, lets you log in once, then downloads each spreadsheet URL
into the matching Google Drive-synced DVD folder.

First test one file:

```bash
python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  --out-dir "/Users/mariorivera/Library/CloudStorage/GoogleDrive-triedtrue411@gmail.com/My Drive/Amy Deliveries" \
  browser-download --limit 1 --pause-for-login --stop-on-error
```

After the login/profile works, run the full delivery:

```bash
python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  --out-dir "/Users/mariorivera/Library/CloudStorage/GoogleDrive-triedtrue411@gmail.com/My Drive/Amy Deliveries" \
  browser-download
```

Completed files are skipped on future runs, so you can restart safely.

## Companion Assets

If the spreadsheet includes URLs in columns whose names mention cover art,
2257, compliance, sleeve, PSD, assets, or model releases, run:

```bash
python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  --out-dir "/Users/mariorivera/Library/CloudStorage/GoogleDrive-triedtrue411@gmail.com/My Drive/Amy Deliveries" \
  download-assets
```

Asset placement rules:

- 2257, compliance, and model-release files land in the DVD folder's `2257/`
  subfolder.
- sleeve, cover-art, and PSD files land beside the scene videos in the DVD
  folder root.
- all asset source URLs and destination paths are written to
  `_companion_assets.csv`.

The audit splits the scene title on `/`, so a scene named `Person One/Person
Two` produces two performer-level 2257 checks. If `download-assets` says no
companion URLs were found, the sheet does not contain source links for those
files. In that case `_asset_audit.csv` is the handoff list for what must be
supplied before the folder is Amy-complete.

If `download-assets` says no companion URLs were found while using a CSV, export
the sheet again as Microsoft Excel `.xlsx` and rerun the same command. If it
still finds no companion URLs, the source sheet contains labels but not usable
links.

## Link Freshness

The `Video file` URLs are signed CDN links. They include `validto=...`.
The tool validates that timestamp before download and refuses expired
spreadsheets, because expired links return HTTP 472 and cannot be recovered
without a fresh spreadsheet/export.

To use a different Drive folder:

```bash
python3 scripts/amy_delivery.py \
  --manifest manifest_delivery3.csv \
  --delivery-name "Delivery 3" \
  upload --remote-base "Deliveries for Amy"
```

## Notes

- Required source columns: `DVD Title`, `Scene ID`, `Scene Title`, `Video file`.
- Existing completed files are skipped, so interrupted imports can resume.
- Per-row download state is stored in `_delivery_status.json` inside the local delivery folder.
- `_delivery_status.json` and `_download_queue.html` are excluded from Drive uploads.
- `_delivery_manifest.csv` and each `_folder_manifest.csv` do upload, because
  that is the spreadsheet context Amy needs alongside the sorted files.
- The script refuses to upload if manifest files are missing unless you pass `--allow-incomplete`.
- `rclone` is already configured on this machine with the remote `gdrive_amy:`.
- A direct `download` command still exists for URLs that do not require
  studio/browser auth, but the standard Amy workflow should use `queue` +
  `import`.
