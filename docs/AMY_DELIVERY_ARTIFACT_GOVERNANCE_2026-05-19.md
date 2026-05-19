# Amy Delivery Artifact Governance

This rule exists because process CSVs and partial downloads were found loose in
the triedtrue411 Google Drive root on 2026-05-19.

## Root Cause

The delivery module was generating useful internal artifacts, but old commands
and docs allowed the delivery output directory to be a Google Drive for Desktop
path. That mixed three different surfaces:

- Amy-facing deliverables.
- Operator proof and troubleshooting records.
- Temporary browser/download/process files.

When those surfaces share a synced folder, internal files can become visible to
the human operator and duplicate into Drive as generic names such as
`_folder_manifest.csv`, `_folder_spreadsheet_rows.csv`, and `.part` files.

## Current Boundary

Amy-facing folders contain only delivery assets:

- final scene videos,
- supplied or generated cover/PSD assets,
- 2257/model-release/compliance folders when approved for that packet,
- explicitly approved writeback/export files.

Internal process artifacts live only under:

```text
/Users/mariorivera/AMG_OS/delivery_work/<Delivery Name>/
```

That includes:

- `_delivery_manifest.csv`
- `_source_spreadsheet_with_paths.csv`
- `_folder_manifest.csv`
- `_folder_spreadsheet_rows.csv`
- `_companion_assets.csv`
- `_asset_audit.csv`
- `_data_audit.csv`
- `_AMY_REVIEW.html`
- `_download_queue.html`
- `_delivery_status.json`
- `_cloud_reports/`
- `_browser_downloads_tmp/`

## Guardrails

`scripts/amy_delivery.py` now refuses normal delivery output under:

- `~/Library/CloudStorage/...`
- `/Volumes/...`

Use `--allow-synced-output` only for a deliberately approved final package run.
Do not use it for research, queue, review, audit, or normal daily processing.

`cloud-copyurls` no longer uploads metadata by default. Use `--upload-metadata`
only when the operator explicitly wants a metadata package visible in Drive.

`upload` excludes process artifacts even if old runs accidentally left them in a
delivery folder.

`import` and `watch-import` refuse synced source folders by default. Import from
the local intake folder unless an operator explicitly uses
`--allow-synced-source`.

Scene and asset URL downloads create `.part` files under the local process temp
area, then move completed files into the deliverable folder only after the
download is non-empty.

Reports and upload gates treat zero-byte files as missing. A placeholder is not
a delivered scene.

## Lifecycle

1. Run delivery processing locally.
2. Review missing-file and missing-asset reports from `delivery_work`.
3. Upload/copy only final approved assets to Drive.
4. Keep the process folder until the delivery has been verified.
5. Run `clean-work` as a dry run before deleting old process folders:

```bash
python3 scripts/amy_delivery.py clean-work --days 14
```

Only after confirming the list:

```bash
python3 scripts/amy_delivery.py clean-work --days 14 --apply
```

Quarantine folders are excluded by default. They require an explicit
`--include-quarantine` pass because they may contain evidence of a prior
misdelivery.

## Current Quarantine

The 2026-05-19 Drive-root generic CSV/HTML artifacts were moved to:

```text
/Users/mariorivera/AMG_OS/delivery_work/drive_root_quarantine_2026-05-19/
```

They are local process evidence, not Amy deliverables. They should be retained
until we finish the module audit, then deleted if no unique source data remains.

Two `.part` files remain in the Google Drive root after the CSV/HTML
quarantine. One `.part` file was moved into the local quarantine before the
large-placeholder behavior was identified. Handle the remaining `.part` files
separately after checking whether Google Drive is actively syncing or whether
they are failed downloads.
