# UI Interaction Spec (v11.3 Active)

This is the canonical active UI interaction spec.

## Core Operator Flow

1. Submit scene/job from Process UI.
2. Monitor queue and run state.
3. Open completed scene review.
4. Approve or adjust cover choices manually.
5. Use Library/Feedback views to prioritize next actions.

## Interaction Constraints

- Keep Jinja + HTMX architecture.
- Keep review actions explicit and operator-controlled.
- Preserve current backend payload compatibility for review persistence.
- No auto-upload behavior in v11.x UI.

## Page-Level Intent

- Process: clear queue state and next action.
- Library: action-first sorting/filtering.
- Scene Review: fast keyboard/mouse decision flow.
- Feedback: inspect disagreement and model-alignment patterns.

## Source of Truth Rule

When UI behavior claims conflict with runtime docs, align with:

- `AGENT_CONTEXT_CURRENT.md`
- `docs/RUNBOOK.md`
- `docs/TROUBLESHOOTING.md`
