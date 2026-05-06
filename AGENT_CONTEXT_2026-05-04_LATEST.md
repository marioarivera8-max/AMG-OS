# AMG OS Agent Context (Latest)

Updated: 2026-05-04 (late session, Cursor)

## Executive state

- Primary priority is now **scoring model quality + efficiency** (text improvements are scaffolded, but scoring is prioritized next).
- Web UI is working on `http://127.0.0.1:8080` when launched via:
  - `cd ~/AMG_OS && source venv/bin/activate && python -m amg.cli ui --host 127.0.0.1 --port 8080 --no-open`
- Recent run timing history is **not yet durably persisted for prior runs** in a queryable ledger; currently only active/in-memory job timing is reliably available in UI.

## What was implemented this session

### 1) Personal examples + workbook ingestion pipeline

- Added importer for manual labels:
  - `amg/learning/import_personal_examples.py`
- Supports:
  - CSV
  - JSONL
  - XLSX (with hyperlink extraction)
- Normalizes:
  - score scales (0-10 and 0-100)
  - decisions (`keep/maybe/reject`)
  - performer/category/tag/link fields across heterogeneous sheets
- Writes canonical examples to:
  - `data/training/examples/<dataset>.jsonl`

### 2) Canonical dataset builder (train/val/test)

- Added:
  - `amg/learning/dataset_builder.py`
  - CLI: `amg build-training-dataset`
  - Script: `scripts/build_training_dataset.py`
- Merges:
  - operator feedback (`data/operator_feedback/feedback.jsonl`)
  - imported examples (`data/training/examples/*.jsonl`)
- Outputs:
  - `all.jsonl`, `train.jsonl`, `val.jsonl`, `test.jsonl`, `manifest.json`

### 3) Text-training export for metadata generation

- Added:
  - `amg/learning/text_style_export.py`
  - CLI: `amg export-text-training`
  - Script: `scripts/train_text_style.py`
- Formats:
  - `instruction`
  - `messages` (chat/SFT friendly)
- Supports bundle export across `train/val/test/all` with manifest.

### 4) Training artifact registry and status

- Added:
  - `amg/learning/training_registry.py`
  - CLI: `amg training-status`
- Tracks dataset and export artifacts in:
  - `data/training/registry.jsonl`

### 5) Dataset evaluation harnesses

- Text eval:
  - `amg/learning/eval_harness.py`
  - CLI: `amg eval-text-dataset`
- Scoring eval:
  - `amg/learning/scoring_training_export.py`
  - CLI: `amg eval-scoring-dataset`

### 6) Scoring training artifact export

- Added scoring export module:
  - `amg/learning/scoring_training_export.py`
- CLI:
  - `amg export-scoring-training`
- Script:
  - `scripts/train_local_model.py`
- Outputs:
  - `data/training/scoring/*_scoring_points.jsonl`
  - `data/training/scoring/*_scoring_pairs.jsonl`

### 7) Documentation + tests

- Added/updated:
  - `docs/training_annotation_schema.md`
  - `tests/test_training_dataset.py`
- Targeted suite reached green repeatedly during session:
  - `tests/test_training_dataset.py`
  - `tests/test_ai_prompt.py`
  - `tests/test_config.py`

## Data/artifacts produced in this session

- Imported workbook:
  - `"/Users/mariorivera/Downloads/1. AMG Content Upload Tracking_Ongoing.xlsx"`
- Import result (recorded run):
  - input rows: `2926`
  - accepted rows: `1597`
  - output: `data/training/examples/upload_tracking_ongoing.jsonl`
- Canonical dataset:
  - `data/training/datasets/scoring_selection_v1/`
  - total rows: `1635` (train `1310`, val `152`, test `173`)
- Text exports:
  - `data/training/text/scoring_selection_v1_*_{instruction|messages}_text_training.jsonl`
  - exported rows (all split export path): `2292` combined input across splits (`3270` counted with split overlap)
- Scoring exports:
  - points: `data/training/scoring/scoring_selection_v1_all_scoring_points.jsonl` (`557` rows)
  - pairs: `data/training/scoring/scoring_selection_v1_all_scoring_pairs.jsonl` (`164` rows)

## Key observed bottlenecks / gaps

1. **Scoring labels are imbalanced in val split**
   - `keep=53`, `maybe=0`, `reject=0`
   - score-labeled rows in val: `0`
   - ranking pairs possible in val: `0`
   - implication: scorer training quality is currently constrained by label mix, not exporter code.

2. **Recent per-run phase timing history for UI jobs is not yet persisted in durable queryable form**
   - active job progress is visible
   - prior run step timings are hard to reconstruct unless decision logs are guaranteed present and indexed.

3. **Long-video efficiency request (30m+, 1h+) remains pending**
   - adaptive frame skipping/interval scaling plan was discussed but not yet implemented in code this session.

## Immediate recommended next steps (for next agent)

1. Implement persistent run timing ledger + UI table:
   - capture `phase_results` and `total_duration_sec` per completed job
   - show recent runs + slowest phases in index or dashboard
   - ensure jobs launched via UI always link to persisted timing record

2. Implement adaptive long-video scan stride:
   - duration-based interval multipliers for tiered scan and hunters
   - preserve quality by keeping floor enforcement/fallback behavior unchanged
   - add boundary tests around 30m and 90m thresholds

3. Improve scoring label balance:
   - collect more `reject` and `maybe` decisions via review UI
   - populate operator manual score field on a meaningful subset
   - regenerate scoring points/pairs and reevaluate pair potential

4. Add scorer release gate:
   - define minimum label coverage + pair count + MAE thresholds before enabling candidate scorer

## New/changed command surface

- Import examples:
  - `amg import-personal-examples <csv|jsonl|xlsx> --dataset-name <name>`
- Build dataset:
  - `amg build-training-dataset --dataset-name scoring_selection_v1 --val-pct 0.10 --test-pct 0.10`
- Export text:
  - `amg export-text-training --dataset-name scoring_selection_v1 --all-splits --format messages`
- Export scoring:
  - `amg export-scoring-training --dataset-name scoring_selection_v1 --split all`
- Evaluate:
  - `amg eval-text-dataset --dataset-name scoring_selection_v1 --split val`
  - `amg eval-scoring-dataset --dataset-name scoring_selection_v1 --split val`
- Artifact status:
  - `amg training-status --limit 200`

## Important implementation note

- There is an existing large dirty working tree with many unrelated modifications.  
  Next agent should avoid reverting unrelated files and continue incrementally.

## Golden run saved (2026-05-05)

- Operator marked latest run as best-ever and requested it be saved as baseline.
- Baseline files:
  - `docs/golden_run_2026-05-05.md`
  - `docs/golden_run_latest.json`
- Current locked reference scene:
  - `27 BBGG - couple swap with jimmy and tabatha _ My Wife and I Have Our First Swingers Experience in a Hot Foursome - Yasm`
