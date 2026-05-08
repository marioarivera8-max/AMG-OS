# AMG OS Roadmap

Last updated: 2026-05-07
Owner: Mario (operator) + current coding agent

This is the single source of truth for what should happen next.
Use it with `docs/BACKLOG.md`:
- `ROADMAP.md` = direction and priorities
- `BACKLOG.md` = specific items and execution order

## Goals (what success looks like)

1. Keep cover quality at v11.1 baseline or better.
2. Reduce operator wait time per scene and per batch.
3. Keep cloud edition reliable, recoverable, and easy to run solo.
4. Preserve human-in-the-loop controls for every irreversible action.

## Planning horizons

## Now (0-2 weeks)

- Confirm cover quality on completed cloud jobs (short + long-form scenes).
- Ship pod reuse (`warm pool`) to remove repeated cold starts on queued jobs.
- Tighten cloud security baseline:
  - protect pod zip endpoint with bearer auth
  - review any unauthenticated internal routes
- Clean up high-confidence UI WIP leftovers called out in handoff notes.

Definition of done for "Now":
- At least 2 smoke runs manually reviewed by Mario
- Batch queue flow reuses one pod and tears down on idle timeout
- No unauthenticated artifact-download endpoint in pod worker

## Next (2-6 weeks)

- Add operator-facing observability:
  - clearer phase timing visibility
  - warm pod status and "terminate now" control
  - easier failed-job diagnosis in UI
- Hardening:
  - backup and restore drill for controller data
  - explicit disaster recovery checklist validation
- Start lightweight multi-user readiness work without overbuilding:
  - role-gated route checks
  - action audit trail

Definition of done for "Next":
- Operator can self-diagnose most failed runs from UI + logs
- Recovery runbook tested at least once end-to-end
- Auth/roles changes do not increase day-to-day friction for single operator mode

## Later (6+ weeks)

- Controlled multi-user rollout (Amy + contractor workflow).
- Cost dashboard and job economics visibility.
- Optional Cloudflare Tunnel hardening if external exposure risk changes.
- Revisit local training/scoring branch if quality lift justifies complexity.

Definition of done for "Later":
- Additional users can operate without breaking existing operator flow
- Cost/throughput decisions are data-backed, not guessed

## Prioritization rules

When choosing the next task, rank in this order:

1. Cover quality regressions or quality uncertainty
2. Reliability/data-loss risk
3. Throughput bottlenecks that compound daily
4. Security improvements with low implementation risk
5. UX polish and convenience items

If two items are similar value, pick the smaller one first to keep momentum.

## Weekly operating cadence (simple and repeatable)

Use this once per week (15-30 minutes):

1. Review `docs/BACKLOG.md` and re-score top 10 items.
2. Promote max 3 items into active focus for the week.
3. Run at least one end-to-end smoke job and note quality observations.
4. Archive completed items and capture 1 sentence of impact ("what got better").
5. Update this roadmap only if priorities changed.

## Guardrails

- No closed API vision providers (adult-content policy conflict).
- No auto-upload automation without explicit approval.
- Do not change `REQUIRE_2257_DOC=False`.
- Avoid bundled mega-changes; keep one clear change per commit.
