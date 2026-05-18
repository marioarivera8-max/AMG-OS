# Amy Delivery Proof Audit

Date: 2026-05-17

## Purpose

This audit checks whether the Amy delivery workflow produces durable proof
artifacts comparable to professional submitter/reporting systems.

## Result

The workflow is active AMG_OS work, not stale research.

`scripts/amy_delivery.py` already produces most of the proof surface needed for
internal AMG delivery operations:

- source spreadsheet rows preserved at the delivery root,
- per-DVD folder manifests,
- per-folder spreadsheet row extracts,
- companion asset URL manifest,
- asset coverage audit,
- human-readable Amy review HTML,
- local missing-file report,
- cloud transfer report,
- cloud failure report,
- cloud layout audit,
- Drive root hygiene audit,
- video spec audit.

## Gap Closed In This Pass

The remaining proof gap was durable file identity.

`audit-data` records presence and size, but it did not write a SHA-256 checksum
for every expected scene file. This pass added:

```bash
python3 scripts/amy_delivery.py \
  --manifest "<delivery spreadsheet>" \
  --delivery-name "<Delivery Name>" \
  audit-file-proof
```

The command writes:

```text
<delivery>/_cloud_reports/<Delivery>_file_proof.csv
```

Each row records:

- CSV row,
- delivery folder,
- scene ID,
- scene title,
- filename,
- relative path,
- status,
- file size in bytes,
- SHA-256.

## Current Proof Coverage

| Requirement | Status | Artifact |
|---|---|---|
| Source row preserved | Present | `_source_spreadsheet_with_paths.csv` |
| Delivery manifest | Present | `_delivery_manifest.csv` |
| Per-folder manifest | Present | `_folder_manifest.csv` |
| Per-folder source rows | Present | `_folder_spreadsheet_rows.csv` |
| Companion assets captured | Present | `_companion_assets.csv` |
| Missing 2257/assets visible | Present | `_asset_audit.csv`, `_AMY_REVIEW.html` |
| Missing scene files visible | Present | `report`, `_AMY_REVIEW.html`, `_data_audit.csv` |
| File count proof | Present | `report`, `audit-data`, `audit-cloud-layout` |
| Local file identity proof | Present after this pass | `audit-file-proof` |
| Cloud transfer proof | Present | `_cloud_reports/*_cloud_transfers_*.csv` |
| Cloud failure proof | Present | `_cloud_reports/*_cloud_failures_*.csv` |
| Cloud folder hygiene | Present | `audit-cloud-layout`, `audit-drive-roots` |
| Video spec proof | Present | `_cloud_reports/*_video_spec_audit.csv` |

## Operating Decision

`scripts/amy_delivery.py` should be treated as the canonical Amy delivery
workflow for spreadsheet-to-deliverable runs.

`organizer/organizer.py` remains a local helper for intake routing and safe
file movement. It already has checksum verification for cross-filesystem copy
operations, which complements the delivery-level file proof added here.

## Next Safe Test

Run the proof audit on the most recent complete local delivery:

```bash
cd /Users/mariorivera/AMG_OS

python3 scripts/amy_delivery.py \
  --manifest "/Users/mariorivera/Downloads/Web VOD - Delivery 6.csv" \
  --delivery-name "Delivery 6" \
  audit-file-proof
```

Expected result:

- creates `_cloud_reports/Delivery 6_file_proof.csv`,
- exits `0` only if every expected scene file exists and is non-empty,
- exits non-zero if any expected file is missing or empty.

## Delivery 6 Observation

The first run against Delivery 6 created:

```text
/Users/mariorivera/AMG_OS/deliveries/Delivery 6/_cloud_reports/Delivery 6_file_proof.csv
```

It audited 76 expected scene rows and reported 76 local missing files. This is
correct behavior for a cloud-first delivery where local `deliveries/Delivery 6`
contains manifests and reports but not the actual MP4 files.

Interpretation:

- `audit-file-proof` is local file-identity proof.
- `audit-cloud-layout` is remote folder-layout proof.
- A delivery can pass cloud layout while failing local file proof if the MP4s
  were uploaded/verified remotely and not retained locally.

Do not treat the local proof failure as a delete/copy instruction. It is a
signal that the authoritative MP4 proof for that delivery lives in Drive/cloud
reports, not in the local delivery folder.
