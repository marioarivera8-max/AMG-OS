# AMG OS UI Interaction Spec (v11.3)

This document locks the target interaction model for the current UI redesign cycle.

## Core Workflow

1. Operator starts or uploads a scene from Process.
2. Operator monitors queue/progress and opens completed scene review.
3. Operator reviews covers quickly (mouse or keyboard), saves review.
4. Operator uses Library to triage what needs action next.
5. Operator uses Feedback to inspect disagreement patterns and model alignment.

## Process Page

### Current Constraints
- One backend queue, single running job, FIFO dispatch.
- HTMX polling for active jobs.
- Recent scenes and timing history shown in side panel.

### Target Interaction Model
- Separate jobs into:
  - Active queue (`queued`/`running`)
  - Recently completed (`done`/`error`)
- Keep drop/upload flow as the primary CTA.
- Keep per-job phase progress visible and refresh active jobs every 2s.
- Make next action explicit for completed jobs (`Review covers`).

## Library Page

### Current Constraints
- Server-rendered filtering only.
- Scene status inferred from covers/review marker.

### Target Interaction Model
- Default sort/order prioritizes action:
  - `review` and `failed` first by default.
- Filters are semantically correct and predictable:
  - status: `review`, `ready`, `draft`, `failed`
  - score filtering normalized to 0-100 display scale.
- Add explicit sort controls:
  - `action_queue`
  - `newest`
  - `highest_score`
  - `lowest_score`
- Add scalable listing controls:
  - server-side `limit`
  - "Load more" by query param.

## Scene Review Page

### Current Constraints
- Per-cover decisions written via hidden inputs.
- Review submit contract must remain backward compatible.

### Target Interaction Model
- Sticky review toolbar with:
  - selected keep/maybe count
  - unsaved indicator
  - save CTA
- Keyboard-first review:
  - `1` keep, `2` maybe, `3` reject, `0` clear
  - `j` / `k` next/previous cover
  - `[` / `]` previous/next cover
  - `Escape` closes modal preview
- Keep existing field names and payload contract for `save_review`.

## Feedback Page

### Current Constraints
- Data source is `feedback.jsonl`.
- Aggregates and recent rows generated in backend.

### Target Interaction Model
- Add GET filters:
  - `scene`
  - `studio`
  - `since_days`
  - `view` (`disagreements` or `all`)
- Clarify metrics:
  - score agreement is `within +/-5`
  - show MAE separately and clearly
- Table identifies actual cover context:
  - filename
  - cover timestamp seconds
  - save timestamp
- Show trend and coverage summary over recent days.

## Design System + Hardening

### Target Decisions
- Move shared CSS from inline template to static asset.
- Keep Jinja + HTMX architecture; no SPA migration.
- Vendor HTMX locally under static assets.
- Reduce inline styles by introducing reusable utility classes and partials.

## Non-Goals

- No automation past manual operator approval.
- No auth/multi-user/deployment complexity.
- No changes to pipeline quality logic in this UI effort.
