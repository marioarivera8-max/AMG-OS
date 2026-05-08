# AMG OS Backlog

Last updated: 2026-05-07

Use this file to track bugs, improvements, tweaks, and features in one list.
Keep newest ideas at the bottom, but sort by Priority Score during weekly review.

## How to use this backlog

- Add every idea as one row (even rough ideas).
- Score each item from 1-5 for:
  - Impact: how much operator value or risk reduction
  - Urgency: how soon it should happen
  - Confidence: how clear the solution is
  - Effort: expected implementation effort (5 = large/complex)
- Priority Score formula:
  - `(Impact + Urgency + Confidence) - Effort`
- Higher score means better "do next" candidate.

## Status legend

- `idea` - captured but not triaged
- `queued` - triaged and waiting
- `active` - currently being worked
- `blocked` - cannot move until dependency is resolved
- `done` - shipped and verified
- `dropped` - intentionally not pursuing

## Backlog table

| ID | Type | Title | Status | Impact (1-5) | Urgency (1-5) | Confidence (1-5) | Effort (1-5) | Priority Score | Owner | Notes |
|---|---|---|---|---:|---:|---:|---:|---:|---|---|
| BL-001 | quality | Cloud smoke test: short + long scene cover quality sign-off | queued | 5 | 5 | 4 | 2 | 12 | Mario | First gate before broader optimization claims |
| BL-002 | feature | Pod reuse warm pool for queued jobs | queued | 5 | 4 | 4 | 3 | 10 | Agent | Design exists in `docs/pod_reuse_design.md` |
| BL-003 | bug/security | Require auth for pod `/jobs/{id}/zip` artifact endpoint | queued | 4 | 4 | 5 | 1 | 12 | Agent | Low effort, high confidence hardening |
| BL-004 | reliability | Backup + restore drill for `/var/lib/amg/data` | queued | 4 | 4 | 3 | 2 | 9 | Mario + Agent | Validate recovery, not just backup existence |
| BL-005 | ux | Show warm-pod status + "terminate now" control in UI | idea | 3 | 3 | 4 | 2 | 8 | Agent | Useful after pod reuse lands |
| BL-006 | observability | Improve failed-job debugging details in job card | idea | 4 | 3 | 3 | 3 | 7 | Agent | Focus on actionable error context |
| BL-007 | feature | Multi-user phase 1: route role checks + action audit log | idea | 3 | 2 | 3 | 4 | 4 | Agent | Defer until real onboarding timing |
| BL-008 | cleanup | Triage known UI/scoring WIP leftovers from prior session notes | queued | 3 | 3 | 4 | 2 | 8 | Agent | Reduce hidden regressions before more UI edits |

## Intake template (copy/paste)

```text
ID: BL-xxx
Type: bug | tweak | improvement | feature | quality | security | reliability | ux | cleanup
Title:
Status: idea
Impact (1-5):
Urgency (1-5):
Confidence (1-5):
Effort (1-5):
Owner:
Notes:
```

## Done log (append-only)

- 2026-05-07: backlog framework initialized (first prioritized list added)
