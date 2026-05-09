# AMG Automation Milestones

Status: Working document (living plan)
Owner: Mario
Scope: End-to-end automation strategy for AMG operations
Last updated: 2026-05-09

---

## 1) Mission

Build AMG OS from a high-touch assisted pipeline into a fully automated
operating system for scene intake, processing, QA, packaging, delivery, and
continuous optimization, while preserving business safety and quality.

---

## 2) North-Star Outcomes

1. Processing throughput scales without linear operator effort.
2. Cover quality stays at or above current known-good baseline.
3. Every scene can move from intake to platform-ready delivery with minimal
   manual intervention.
4. Compliance risk is proactively detected and blocked before release.
5. System decisions become data-driven through feedback and outcome learning.

---

## 3) Current Reality (starting point)

- Cloud production path is active (`https://amg.exoticplug.app`).
- Core pipeline and review flow work, but operator validation remains central.
- Throughput and reliability are improving, but not yet autonomous.
- Auto-upload is intentionally disabled in current operating policy.

This plan defines what must be built to eventually achieve complete automation.

---

## 4) Guardrails and Gating Rules

- No closed API vision providers for scene analysis.
- `REQUIRE_2257_DOC=False` remains unchanged unless policy/business changes.
- No irreversible platform action without explicit authorization policy.
- Quality regressions block milestone promotion.
- Every automation layer must have:
  - audit log
  - rollback path
  - operator override

---

## 5) Milestones

## M0 - Stabilize and Baseline (Now)

Goal: Lock reliability and metrics so future automation is built on trusted
runtime behavior.

Build:
- Runtime truth dashboard (deployed tags, env defaults, active backend mode)
- Standard timing/quality scorecard per run
- Failure taxonomy and automatic run classification
- Smoke-test playbook (short + long scene)

Done when:
- Team can answer "what changed?" for any regression in under 5 minutes.
- All key production defaults are observable in one place.

---

## M1 - Throughput Engine (Semi-Autonomous Processing)

Goal: Maximize scene throughput with stable quality.

Build:
- Warm pod lifecycle/pool behavior
- Smart scheduling (scene duration/size aware queueing)
- Dynamic backend strategy (auto vs forced hwaccel paths by source profile)
- SLA-style job time expectations and alerts

Done when:
- Repeated queue jobs avoid repeated cold-start penalties.
- Median scene turnaround is materially lower and stable across batches.

---

## M2 - Autonomous Technical QA

Goal: Reduce manual time spent catching weak outputs.

Build:
- Quality gate service that scores blur/composition/faces/exposure consistency
- Rejection and rerun logic before human review
- Cover diversity checks (avoid near-duplicates and same-moment flooding)
- Confidence thresholds with explainable failure reasons

Done when:
- Most low-quality covers are rejected automatically before review.
- Human reviewer time shifts from "screening" to "approval."

---

## M3 - Metadata and Packaging Automation

Goal: Automate non-creative repetitive operations after cover selection.

Build:
- Title/description generation with style policies per platform/studio
- Performer/genre normalization with validation rules
- Packaging templates per destination (naming, foldering, required assets)
- Preflight checks for platform constraints

Done when:
- Scene metadata package is generated and validated without manual formatting.
- Manual edits become exceptions, not default workflow.

---

## M4 - Compliance Automation Layer

Goal: Convert compliance from ad hoc checks to deterministic gating.

Build:
- Performer-document registry with completeness scoring
- Scene-level compliance readiness calculator
- Missing/expired document alerting and hard blocks
- Compliance audit exports

Done when:
- "Ready for delivery" is impossible unless compliance conditions are met.
- Compliance status is queryable per performer, scene, and platform.

---

## M5 - Controlled Delivery Automation

Goal: Move from "manual handoff" to "policy-based execution."

Build:
- Delivery connectors/adapters per platform (or transfer route)
- Explicit policy engine (what can auto-send, what requires approval)
- Staged release queues with retry + idempotency
- End-to-end delivery receipts and reconciliation

Done when:
- Approved scenes can be delivered automatically with full traceability.
- Failures are auto-retried or escalated with actionable diagnostics.

Note:
- This milestone remains blocked until business approval for automation scope.

---

## M6 - Closed-Loop Learning and Optimization

Goal: Make performance and quality improve from outcomes, not only tuning.

Build:
- Feedback ingestion from review decisions and platform outcomes
- Model/rubric tuning pipeline with release gates
- A/B framework for prompts/scoring rules
- Studio-specific policy adaptation

Done when:
- New model/rule versions are promoted by measured lift, not intuition.
- Quality + throughput trends are improving quarter over quarter.

---

## M7 - Multi-Operator Autonomous Platform

Goal: Operate safely with multiple users, roles, and accountability.

Build:
- Role-based access control and scoped permissions
- Per-user activity audit and approval chains
- Assignment/queue ownership model
- Operational dashboards (cost, throughput, quality, risk)

Done when:
- Amy, Mario, and contractors can operate in parallel without process drift.
- Accountability and control are built-in, not manual.

---

## 6) Cross-Cutting Systems (Required Across Milestones)

- Observability: logs, metrics, traces, job event history
- Security: secrets handling, auth, endpoint hardening
- Cost intelligence: per-scene and per-batch economics
- Data model: stable IDs for scenes/assets/reviews/deliveries
- Recovery: backup, restore, replay, and rollback procedures

---

## 7) Milestone Prioritization Framework

Use this scoring for candidate features:

- Impact (1-5): business value or risk reduction
- Automation leverage (1-5): how much manual work it removes
- Confidence (1-5): implementation clarity
- Effort (1-5): complexity/cost

Priority score:

`(Impact + Automation leverage + Confidence) - Effort`

---

## 8) Phase Exit Metrics (minimum)

Every milestone should define target values for:

- Throughput:
  - median scene runtime
  - p90 scene runtime
  - queue wait time
- Quality:
  - review acceptance rate
  - fallback frequency
  - blur/quality reject rate
- Reliability:
  - job success rate
  - mean time to recovery
  - incident count
- Operations:
  - manual minutes per scene
  - cost per scene
  - rerun rate

---

## 9) Risks and Mitigations

- Quality drift from over-automation
  - Mitigation: quality gates + visual sign-off checkpoints
- Compliance false positives/negatives
  - Mitigation: deterministic rules + auditability + manual override workflow
- Cost spikes from scaling behavior
  - Mitigation: budget guardrails + queue policy + kill switches
- Integration brittleness with delivery endpoints
  - Mitigation: adapters, retries, idempotency, reconciliation reports

---

## 10) Immediate Next Planning Actions

1. Break M0-M2 into executable backlog epics with owners.
2. Define first dashboard spec for operational truth + quality/timing.
3. Define "automation policy matrix" (what can run without approval, by stage).
4. Draft the delivery connector strategy (API-native vs transfer automation).
5. Decide milestone review cadence (weekly ops + monthly strategy checkpoint).

---

## 11) Working Notes

Use this section to append discovered facts, constraints, and decisions during
implementation. Keep changes additive and dated.
