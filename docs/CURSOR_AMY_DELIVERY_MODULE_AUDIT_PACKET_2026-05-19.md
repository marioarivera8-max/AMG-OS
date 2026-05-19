# Cursor Worker Packet: Amy Delivery Module Audit

You are auditing `scripts/amy_delivery.py` for AMG OS. Do not edit Amy live
workbooks. Do not alter Noah's lane. Do not upload or submit anything.

## Goal

Confirm the delivery module cannot pollute Google Drive root, Amy Deliveries
root, T9, or mirrored archives with process artifacts, while still proving when
required scene/asset files are missing.

## Read First

- `scripts/amy_delivery.py`
- `scripts/AMY_DELIVERY_WORKFLOW.md`
- `scripts/AMG_DVD_SCENE_ORGANIZER_MODULE.md`
- `docs/AMY_DELIVERY_ARTIFACT_GOVERNANCE_2026-05-19.md`
- `docs/AMY_DELIVERY_PROOF_AUDIT_2026-05-17.md`

## Checks

1. List every code path that writes a file.
2. Classify each write as:
   - `DELIVERABLE_ASSET`
   - `LOCAL_PROCESS_ARTIFACT`
   - `QUARANTINE_EVIDENCE`
   - `BUG_RISK`
3. Verify all `LOCAL_PROCESS_ARTIFACT` writes go under
   `/Users/mariorivera/AMG_OS/delivery_work/<Delivery>/`.
4. Verify the module refuses normal `--out-dir` under `CloudStorage` or
   `/Volumes`.
5. Verify upload/copy commands do not include process CSV/HTML/status/temp
   files unless an explicit metadata flag is used.
6. Identify any path that can still create generic files in a human-facing
   folder.
7. Identify any path where required files could be silently skipped or treated
   as complete.

## Output

Return a short report with:

- PASS/FAIL for each check.
- Exact file/line references for any `BUG_RISK`.
- A recommended patch only if needed.
- Confirmation that you did not delete files, alter live workbooks, alter
  Noah's work, upload, or submit anything.
