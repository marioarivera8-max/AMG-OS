# UI Spec From Tutorials (v1 Target)

This spec translates the tutorial-derived workflow into explicit UI + backend requirements for the local-first app.

## Primary goals

- Reduce classification mistakes (especially false penetration labels).
- Preserve human-in-loop approval before any distribution action.
- Replace spreadsheet+multi-window juggling with a single scene workspace.
- Keep operator trust by showing evidence and allowing corrections.

## Pages and required capabilities

## 1) Library (`/`)

Purpose: replace tracker overview with operational queue.

Required:
- Scene table/grid with statuses: `NEW`, `PROCESSING`, `PROCESSED`, `REVIEWED`, `READY_TO_UPLOAD`, `UPLOADED`, `NEEDS_ATTENTION`.
- Filters: studio, performer, scene type, status, date.
- Sort by priority and newest activity.
- "Process now" action for single scene.

Data:
- `decision_logs`, `reviewed`, `distribution_status`, incoming path discovery.

## 2) Scene workspace (`/scene/{id}`)

Purpose: one place for analysis, review, and prep.

Panels:
- Processing summary (runtime, cover counts, fallbacks, errors).
- Contact sheet and cover grid.
- Classification evidence panel per cover:
  - model `type`, `position`, penetration visible/confidence, score.
  - operator correction controls (keep/maybe/reject, penetration yes/no, position label, reason).
- Metadata editor (title, description, genres, performers, notes).
- Compliance checklist (2257 present? releases present? doc paths).

Writes:
- `data/reviewed/{scene}.json`
- `data/operator_feedback/feedback.jsonl`

## 3) Readiness gate (`/scene/{id}/ready`)

Purpose: explicit pre-upload validation for each destination.

Checks:
- Required metadata present.
- Title length by platform rules.
- Required docs present and matched to performers.
- Cover selected.
- Distribution package files exist.

Output:
- Readiness report and blockers.
- Can transition scene to `READY_TO_UPLOAD` only when all selected checks pass.

## 4) Upload prep + manual handoff (`/scene/{id}/delivery`)

Purpose: replace ad-hoc transfer steps with guided checklist while keeping manual submit.

Sections:
- AEBN delivery checklist (metadata, cover, video, docs).
- ADE/MultCloud delivery checklist (destination, files selected, transfer verification).
- "Copy package paths" and "Open destination folder" helpers.
- Explicit "Mark uploaded" button (manual confirmation + notes).

No auto-upload in v11.x.

## 5) Feedback analytics (`/feedback`)

Purpose: teach the local model from operator corrections.

Views:
- Penetration match rate over time.
- Position label match by studio/scene.
- Most common correction reasons.
- Candidates repeatedly rejected for same reason.

Backed by:
- `amg feedback-eval`
- `data/operator_feedback/feedback.jsonl`

## API/Service additions

- `GET /api/scenes` list and filter states.
- `GET /api/scenes/{id}` include decision log + review + delivery status.
- `POST /api/scenes/{id}/process` trigger job.
- `POST /api/scenes/{id}/review` save picks and classification corrections.
- `GET /api/scenes/{id}/ready` evaluate readiness.
- `POST /api/scenes/{id}/mark-uploaded` manual transition with notes.
- `GET /api/feedback/metrics` return correction quality stats.

## Validation rules

- Penetration/position corrections required for any cover marked `keep`.
- If operator marks penetration `no`, position cannot be non-`OTHER`.
- `READY_TO_UPLOAD` requires compliance and selected destination checks.
- Upload mark requires manual user action + timestamp.

## UI progression plan

1. Stabilize v0 run/review (already started).
2. Add persistent scene state model + library statuses.
3. Add full review corrections + feedback analytics.
4. Add readiness and delivery checklist screens.
5. Add optional tracker export/import to reduce spreadsheet drift.
