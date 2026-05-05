# Training Annotation Schema (Scoring/Selection First)

Use this schema to label bad-run screenshots or final-picked frames quickly and consistently.
You can also ingest operational tracking workbooks (`.xlsx`) directly; hyperlink targets are preserved.

## Goal

Capture operator truth for frame selection so AMG can learn:
- what to keep vs reject
- how strong the score should be (0-100)
- why a frame was rejected
- what should have been picked instead

## CSV Columns

Required minimum:
- `scene_id` — stable scene identifier/folder name
- `filename` or `image_path` — frame identity
- `decision` — `keep`, `maybe`, or `reject`

Recommended full set:
- `scene_id`
- `filename`
- `image_path`
- `model_score` (0-100 or 0-10, auto-normalized)
- `operator_score` (0-100 or 0-10, auto-normalized)
- `decision` (`keep`/`maybe`/`reject`)
- `should_have_picked` (filename/path of better frame if this row is rejected)
- `categories` (comma-separated)
- `tags` (comma-separated)
- `title` (if tied to this frame's best title angle)
- `notes` (why this decision was made)

## Example CSV

```csv
scene_id,filename,model_score,operator_score,decision,should_have_picked,categories,tags,notes
20260504_173558_2025-05-16_16.33.49,04_Unknown__PENETRATION_Single_8.0_13m00s.jpg,81,58,reject,01_Unknown__NUDE_Dual_8.5_4m07s.jpg,"POV,Blowjob","eye contact,oral closeup","Face partially covered and less marketable composition"
20260504_173558_2025-05-16_16.33.49,01_Unknown__NUDE_Dual_8.5_4m07s.jpg,79,92,keep,,"POV,Amateur","eye contact,dual gaze","Strong direct gaze and clear performer visibility"
```

## CLI Workflow

1) Import labels (CSV/JSONL/XLSX):

```bash
amg import-personal-examples /path/to/labels.csv --dataset-name badrun_may04
amg import-personal-examples "/Users/mariorivera/Downloads/1. AMG Content Upload Tracking_Ongoing.xlsx" --dataset-name upload_tracking_ongoing
```

2) Build canonical training split:

```bash
amg build-training-dataset --dataset-name scoring_selection_v1 --val-pct 0.10 --test-pct 0.10
```

3) Export text-training pairs for title/description/tag learning:

```bash
amg export-text-training --dataset-name scoring_selection_v1 --split all
amg export-text-training --dataset-name scoring_selection_v1 --all-splits --format messages
```

4) Evaluate dataset quality on holdout split:

```bash
amg eval-text-dataset --dataset-name scoring_selection_v1 --split val
amg training-status --limit 200
```

5) Export scoring model training artifacts and evaluate:

```bash
amg export-scoring-training --dataset-name scoring_selection_v1 --split all
amg eval-scoring-dataset --dataset-name scoring_selection_v1 --split val
```

Outputs:
- `data/training/examples/<dataset>.jsonl`
- `data/training/datasets/<dataset>/all.jsonl`
- `data/training/datasets/<dataset>/train.jsonl`
- `data/training/datasets/<dataset>/val.jsonl`
- `data/training/datasets/<dataset>/test.jsonl`
- `data/training/datasets/<dataset>/manifest.json`
- `data/training/text/<dataset>_<split>_<format>_text_training.jsonl`
- `data/training/text/<dataset>_<format>_bundle_manifest.json` (when `--all-splits`)
- `data/training/scoring/<dataset>_<split>_scoring_points.jsonl`
- `data/training/scoring/<dataset>_<split>_scoring_pairs.jsonl`

For `.xlsx` imports, rows include:
- `source_sheet`, `source_row_num`, and `source_file`
- extracted `source_links` (from embedded hyperlink targets + URL text)
- inferred weak `decision` from platform status columns when explicit decision is missing
- `record_quality` score (0.0-1.0) for downstream filtering/prioritization
