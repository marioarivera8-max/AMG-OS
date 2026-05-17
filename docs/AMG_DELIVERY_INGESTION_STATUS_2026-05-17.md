# AMG Delivery Ingestion Status

Date: 2026-05-17

## Current Conclusion

The delivery-ingestion/organizer idea from the older Claude/Cursor handoffs is no longer just a plan. It has been partially implemented in `AMG_OS` and should be treated as an active NOW workflow area.

Relevant current files:

- `organizer/organizer.py`
- `organizer/config.py`
- `organizer/README.md`
- `organizer/tools/manifest_from_html.py`
- `scripts/amy_delivery.py`
- `scripts/AMY_DELIVERY_WORKFLOW.md`
- `scripts/AMG_DVD_SCENE_ORGANIZER_MODULE.md`

## Verified Post-Restart State

Command run:

```bash
python3 /Users/mariorivera/AMG_OS/organizer/organizer.py status
```

Observed result:

- Manifest path: `/Users/mariorivera/AMG_OS/manifest.csv`
- Columns detected: `DVD Title`, `Scene ID`, `Scene Publication`, `Scene Title`, `Video file`
- Column map: `SCENE_ID`, `DELIVERY_FOLDER`, `SOURCE_FILENAME`
- Rows loaded: `91`
- Organized: `0`
- Pending: `91`
- Unmatched: `0`
- Ambiguous: `0`

Interpretation: the local organizer can read the current manifest shape and is ready for controlled test/import work. It should not be treated as merely speculative chat output.

## Workflow Split

There are two related tools:

### Local Organizer

`organizer/organizer.py` handles local inbox routing:

- watches or processes `~/AMG_OS/inbox`
- maps incoming files to manifest rows
- routes files into `~/AMG_OS/downloads/<DVD title>/`
- never deletes
- never overwrites
- sends unmatched/ambiguous files to review folders

This is best for local/manual file-drop organization.

### Amy Delivery Script

`scripts/amy_delivery.py` handles repeatable Amy delivery structure and cloud workflow:

- validate
- prepare
- queue
- import / watch-import
- report
- audit-data
- audit-assets
- audit-cloud-layout
- audit-video-specs
- upload / cloud-plan / cloud-mkdirs / cloud-copyurls / link

This is best for spreadsheet-to-delivery folder generation, Amy review pages, Drive upload, and delivery verification.

## Historical Handoffs Now Classified

The following staged handoffs are historical/synthesis inputs, not active standalone specs:

- `/Users/mariorivera/Desktop/AMG_LOCAL_STAGING_DO_NOT_SYNC/Chat Triage/Synthesize Next/cowork_organizer_prompt.md`
- `/Users/mariorivera/Desktop/AMG_LOCAL_STAGING_DO_NOT_SYNC/Chat Triage/Synthesize Next/keyframe_claude_cursor_handoff.md`

Their durable lesson is captured here:

- build predictable local ingestion,
- avoid clever matching that silently guesses,
- preserve spreadsheet context beside files,
- log every move,
- never delete,
- keep protected CDN failures visible,
- use manual/import fallback when automation is blocked.

After this note is reviewed, those staged handoffs can be moved to archive candidates.

## Known Download Constraint

Protected Naughty America/CDN URLs may fail under direct HTTP automation, including with browser-like headers or `yt-dlp`, with errors such as `HTTP Error 472`.

Correct business response:

- do not bypass access controls,
- use authorized browser/session/manual download,
- request proper export packages, SFTP, API access, ZIP packages, or signed downloadable links when available,
- keep failure logs visible,
- route manually downloaded files through import/watch-import.

## NOW Priority

For AMG OS NOW, this area matters because it directly reduces:

- manual folder creation,
- manual renaming,
- lost spreadsheet context,
- repeated delivery QA,
- employee/contractor ambiguity,
- Drive clutter,
- hidden rework.

The near-term goal is not public SaaS polish. The near-term goal is a reliable internal AMG delivery operating workflow for Amy and Mario.

## Next Actions

1. Decide which delivery tool is canonical for Amy's current delivery workflow: likely `scripts/amy_delivery.py`.
2. Confirm whether `organizer/organizer.py` remains a local helper or should be folded into the Amy delivery workflow.
3. Add `.gitignore` coverage for runtime outputs such as inbox files, downloads, organizer logs, browser profiles, watcher locks, and generated delivery reports.
4. Run a small dry-run/import verification using a safe manifest and 2-3 known files before any large delivery run.
5. Only after verification, archive the old handoff prompts from staging.

