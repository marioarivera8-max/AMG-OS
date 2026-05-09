# AMG_OS_NEXT — Phase 5 Build Guide (Title & Copy)

**Version:** v1.0 AUTHORITATIVE
**Authority band on this guide:** AUTHORITATIVE
**Predecessor:** `~/AMG_OS/docs/AMG_OS_NEXT_PHASE_4_BUILD_GUIDE.md` v1.1
**Operating Brief:** `~/AMG_OS_NEXT/CLAUDE.md` (post §16–§19 amendments)
**Operator:** Tony · **Date:** 2026-05-09 · **Author:** Claude (Cowork)

> **Read this first.** This is a **strategy document**, not yet a build guide. Phase 5 cannot enter atomic commit until the §10 operator decisions resolve. The §1 charter overlap with Phase 4 Track C is the load-bearing question. Per CLAUDE.md §3.3, surfacing this contradiction *is* the deliverable.
>
> **Doubly-blocked as of 2026-05-09 morning.** Cursor halted at R.7 with **two red test failures** — `cv2 ModuleNotFoundError` and a `PermissionError` under `~/Library/Containers/com.a…`. CLAUDE.md §8 forbids merge with red CI, so the Phase 5 charter commit itself cannot land until preflight is green. See **§-1 Preflight blockers** below — that section must clear *before* §10 operator decisions matter.

---

## §0. Preamble — locked decisions carried into Phase 5

Phase 5 begins under FAILURE PROTOCOL discipline (CLAUDE.md §4). Authority bands AUTHORITATIVE / REFERENCE / DEPRECATED / UNKNOWN-gap apply. No silent contradiction resolution. Operator confirms every cross-subsystem or destructive action.

**Locked decisions inherited:** L0.1–L0.14 (per Phase 4 build guide §0), I1–I5 invariants, P11–P15 prevention layer, R1–R12 standing rules, full ADR corpus 001–020.

**New L0.X candidates Phase 5 may add** (none locked yet — surface for operator):
- L0.15 candidate — Pattern library sourcing policy (research-derived corpus, operator curation, drift policy).
- L0.16 candidate — Operator preference learner activation threshold and signal weights.
- L0.17 candidate — Performance feedback loop signal contract (which platform metrics feed back).

These remain UNKNOWN-gap until operator decides per §10.

---

## §1. Phase 5 charter (locked)

Phase 5 is locked to Interpretation A per DR-011: Phase 4 shipped generation, and Phase 5 ships next-level title/copy quality through pattern library intelligence, operator preference learning, per-platform variant optimization, and a performance feedback loop.

Locked scope posture:
- Submission metadata quality is in scope (title, description, tags and ranking quality).
- Marketing copy outside submission metadata is out of scope for Phase 5 and deferred to later phases.
- v1.0 feedback-loop signals are takedowns and search rank; clicks/conversions are deferred to Phase 5.x pending privacy review.

## §2. Scope (locked to Interpretation A)

**In scope:**
- Pattern library: research-derived title and description patterns from AVN/XBIZ award winners + top-rated catalogs + analyzed top-performing TSS submissions where licensed access exists.
- Operator preference learner: per-operator (per-subscriber for Tier 1, AMG-side for Tier 2) preference signals — which AI suggestions get picked, which get edited, which words consistently get swapped. Activation gated by reviewed-scene volume threshold.
- Per-platform variant cache: title length and tag-count optimized variants per platform spec; multi-variant generation cached at the asset level.
- A/B testing: two-variant or multi-armed bandit for live submissions. Decision deferred to §6 Q5.5.
- Performance feedback loop: post-publish platform signals (clicks, conversions, search rank, takedowns) feed back into pattern weight and operator preference scores.
- Voice mimicry sub-feature: per-operator voice profile that biases generation away from generic and toward the operator's observed style.

**Out of scope (deferred to Phase 6+):**
- DVD-specific copy (Phase 6).
- Distribution-channel-specific copy beyond submission metadata (Phase 7).
- Vendor-side copy (Phase 8).
- Marketing copy for AMG itself (separate concern, not in any phase).
- Analytics dashboard for copy performance (Phase 9 Operator Console).

---

## §3. Subsystem architecture (provisional)

Five subsystems cooperate. All five must hit their targets; weakest sets perceived quality.

**A. Pattern Library.** Curated corpus of title/description patterns indexed by genre, length, hook style, performer-led / scene-descriptive / numbered-series / studio-branded / narrative-hook (matching `REVIEW_WORKFLOW.md` style enum). Patterns version-locked per CLAUDE.md §19 prompt registry. Drift detected by CI guard. Operator-curated additions allowed; deletions retained for replay.

**B. Generation Engine (extended).** Existing Phase 4 Track C generators wrapped with pattern-library injection at prompt-construction time and per-platform variant fan-out. Multi-variant output: N-best title / description / tag candidates with per-candidate confidence and pattern-attribution.

**C. Operator Preference Learner.** Append-only signal store: each `pick`, `edit`, `reject`, `swap` event recorded with `(operator_id, suggestion_id, action, edit_distance, timestamp)`. Activation threshold (operator-decided in §10) gates when learned signals start influencing ranking. Below threshold = pure pattern-library ranking; above threshold = pattern + operator preference blended.

**D. Per-Platform Variant Cache.** Per-platform title length caps, tag count caps, and content-policy filters applied to candidates; cached per `(release_id, platform_id)`. Cache invalidates on pattern library bump or operator preference update. Cache hit rate is a perf metric.

**E. Performance Feedback Loop.** Post-publish platform signals ingested per platform adapter — clicks, conversions, ranking position, takedown events. Signals attributed to the chosen title / description / tag set. Feedback updates pattern weights (slow learning rate) and operator preference confidence (faster).

**Cross-cutting concerns:**
- Versioned prompt registry (CLAUDE.md §19 / ADR-010) — every generation prompt is a file.
- AI-safe infrastructure (CLAUDE.md §17 / ADR-008) — localhost-only inference; no cloud.
- Confidence threshold map (CLAUDE.md §18 / ADR-009) — Phase 5 needs an extension covering pattern-attribution confidence and operator preference confidence.

---

## §4. Schema additions (provisional, Interpretation A)

Five new tables. Alembic migrations 0010+ in Phase 5 Track A.

**`title_patterns`** — pattern_id (PK), pattern_text_template, style_enum (performer_led | narrative_hook | scene_descriptive | studio_branded | numbered_series), source_attribution (avn_winner | xbiz_winner | top_catalog | operator_added | derived), source_confidence, length_chars, length_words, genre_affinity_json, version, created_at, deprecated_by_id (FK self-reference per §19 retention).

**`operator_preferences`** — preference_id (PK), operator_id (FK → customers.customer_id for Tier 1 subscribers; AMG-internal id for Tier 2), preference_type (style_bias | word_swap | length_band | tag_bias), signal_weight (float 0-1), reviewed_scene_count_at_record, last_updated_at, audit_trail_id.

**`copy_variants`** — variant_id (PK), release_id (FK), platform_id (FK nullable for platform-agnostic variants), copy_type (title | description | tags), variant_rank, candidate_text, pattern_attribution_id (FK → title_patterns nullable), generation_confidence, operator_pick_flag, generated_at.

**`copy_performance`** — perf_id (PK), variant_id (FK → copy_variants), platform_id (FK), signal_type (click | conversion | search_rank | takedown), signal_value, signal_observed_at, source_adapter, audit_trail_id.

**`voice_profiles`** — profile_id (PK), owner_id (FK to operator/subscriber), profile_vector_blob (learned embedding or rule-set), trained_on_corpus_size, last_trained_at, version.

**Inherits invariants:** I3 (every variant traces to a release), I5 (every learning event records a snapshot JSON of the inputs).

**P-rules apply:** P11 (no plaintext PII — operator preferences stored without subscriber-identifying free text); P12 (vault-encrypted at rest where personal preferences accumulate).

---

## §5. Track structure (mirrors Phase 4 A–G)

Each track is a self-contained unit with paste-ready Cursor prompt, deliverables, validation chain, FAILURE PROTOCOL reporting. **Multitask OFF** per Session Log §5.

| Track | Scope | Atomic commit? | Depends on |
|---|---|---|---|
| **A — Schema land** | Migrations 0010–0014 + Pydantic v2 models for the 5 new tables; SOPS-encrypted YAML scaffolds where applicable | Yes | Phase 4 Track A schema; ADR-021 |
| **B — Pattern library ingestion** | Research-derived patterns from AVN/XBIZ corpus + curation tool; pattern versioning per §19 | Yes | Track A |
| **C — Multi-variant generation engine** | Wrap Phase 4 Track C generators with pattern-library injection + per-platform variant fan-out | Yes | Track A + Track B |
| **D — Operator preference learner (gated)** | Signal store + activation threshold + ranking blender; ships with threshold OFF until §10 Q5.6 picks the activation point | Yes | Track A |
| **E — Per-platform variant cache** | Cache layer keyed `(release_id, platform_id)`; invalidation rules; perf budget for cache hit rate | Yes | Track C |
| **F — Performance feedback loop** | Post-publish signal ingestion via existing platform adapters (Phase 4 Track B); attribution to chosen variant; weight updates | Yes | Track A + Track C + Phase 4 Track B |
| **G — Operator-side action items** | Pattern-library seed corpus operator review; voice-profile training opt-in flow per Tier 1 subscriber; performance-loop policy review | Operator Terminal | Tracks A–F shipped |
| **CS — Phase 5 cross-subsystem integration** | Wire Tracks A–F into Phase 4 Track CS scheduled-submissions flow; ensure scheduled drops use latest variant + latest preference state | Yes | All above |

---

## §6. Open questions (must surface before lock)

These ride the §10 operator-decisions list. Surfacing per CLAUDE.md §3.3 + R7.

- **Q5.1 — Phase 5 charter.** Interpretation A vs B vs C per §1. **GATE: blocks atomic commit.**
- **Q5.2 — Pattern library sourcing.** AVN/XBIZ winners only? AMG's own top-performing past releases (training-data extraction)? Licensed access to TSS-style top-submitted analytics? Operator-curated additions allowed?
- **Q5.3 — Per-platform length caps.** Hard cap per platform or learned cap from observed acceptance rates? Affects Track E cache invalidation.
- **Q5.4 — Marketing copy in or out.** Submission metadata only (Interpretation A default) vs broader marketing copy (Interpretation B). Affects Track scope and schema.
- **Q5.5 — A/B testing framework.** Simple two-variant test (operator picks at scene level) vs multi-armed bandit (system picks adaptively, operator override always available). Bandit needs more reviewed scenes before activation.
- **Q5.6 — Operator preference learner activation.** v12 preview suggested ~100 reviewed scenes. Operator confirms threshold before Track D ships.
- **Q5.7 — Feedback loop signal contract.** Which platform signals feed back? Clicks (privacy-sensitive)? Conversions (revenue signal — best but rare)? Search rank (proxy)? Takedowns (compliance signal — most actionable)? Subset gates Track F scope.
- **Q5.8 — Voice mimicry scope.** Per-Tier-1 subscriber voice profiles only, or also per-Tier-2-studio brand voice? Affects schema and training data segregation.
- **Q5.9 — Confidence threshold table extension.** ADR-009 currently covers vision-model thresholds. Phase 5 adds pattern-attribution confidence + operator preference confidence + variant-rank confidence — needs ADR amendment.

---

## §7. ADRs to author (preliminary; numbering picks up after Phase 4's 014–020)

- **ADR-021** — Phase 5 charter scope decision (resolves Q5.1).
- **ADR-022** — Pattern library sourcing + curation policy (resolves Q5.2).
- **ADR-023** — Multi-variant generation + ranking algorithm.
- **ADR-024** — Operator preference learner training threshold + signal weights (resolves Q5.6).
- **ADR-025** — Performance feedback loop architecture + signal contract (resolves Q5.7).
- **ADR-026** — Per-platform copy length policy (resolves Q5.3).
- **ADR-027** — Voice profile schema + training data segregation (resolves Q5.8).
- **ADR-028** — Confidence threshold extension to copy-generation domain (amends ADR-009).

CLAUDE.md auto-update fires after Tracks F + ADRs land (§15 auto-update protocol).

---

## §8. Reporting surface

`docs/phase5_status.md` (new) tracks track-by-track completion. Same format as `phase3_status.md`.

---

## §9. Closure criteria (preliminary)

Phase 5 closes only when ALL items below are complete:

1. All 8 tracks (A–G + CS) complete.
2. ADRs 021–028 signed off by operator.
3. Pattern library seeded with ≥ 200 research-derived patterns; ≥ 80% style-attributed.
4. Operator preference learner activation threshold honored; learner OFF until threshold met.
5. Performance feedback loop wired to ≥ 2 platform adapters with signal contract sign-off.
6. Per-platform variant cache hit rate ≥ 70% in test corpus.
7. Test posture: ≥ 380 tests green; mypy ≥ 150 source files clean; Ruff clean.
8. CLAUDE.md §9 phase-active table updated; `phase5_status.md` reflects closure.
9. Operator signs Phase 5 → Phase 6 handoff in `docs/decisions/decision_register.md` as **DR-011**.

If any criterion regresses post-closure, Phase 5 reopens to "at risk."

---

## §10. Operator decisions (resolved and recorded)

All previously blocking decisions are now resolved and recorded in the decision register:

- DR-011 — Phase 5 charter pick: Interpretation A (next-level title/copy quality).
- DR-012 — Noah co-creator migration plan: read-only ingest from v11.x into AMG_OS_NEXT migration tables.
- DR-014 — Cloud-edition adoption: operator-controlled compute amendment to CLAUDE.md §6 and §17.
- DR-016 — Noah co-creator deferred-compensation arrangement.

This guide reflects those accepted decisions and is now commit-ready as an AUTHORITATIVE build guide.

## §11. What the next atomic commit looks like (preview, post-decisions)

Once §10 resolves, the next atomic commit is the build guide itself, lifted to AUTHORITATIVE:

```
git commit
  Files: ~/AMG_OS/docs/AMG_OS_NEXT_PHASE_5_BUILD_GUIDE.md (new, v1.0)
         ~/AMG_OS_NEXT/CLAUDE.md (§9 phase table update; §15 revision history)
         ~/AMG_OS_NEXT/docs/decisions/decision_register.md (DR-011 charter pick)
  Subject: docs(phase-5): land charter + build guide v1.0 (DR-011)
```

After that commit lands, Track A (schema land) is the first build commit, mirroring Phase 4 Track A `a79e58c`.

---

## §12. Carryover from Phase 4 §6 still open

These are not Phase 5 questions per se but should land as Phase 4.x decision register entries before Phase 5 closes:

- **Q4.3** — auto-renew vs operator-confirm-each-renewal for `licensing_intermediary`.
- **Q4.4** — royalty dispute resolution: arbitration clause vs operator-driven.
- **Q4.5** — beta cohort selection for first L1 Amy review (suggest: 2–3 low-stakes domestic counterparties).
- **Q4.6 (NEW)** — Stripe billing path for Noah (current v11.x beta user). Migrate to Stripe at Phase 4 close (DR-006 dependency), or hold on legacy/manual until v1.0?

Surface for awareness; not Phase 5 blocking, but Q4.6 directly affects DR-006 status.

---

## §14. Co-Creator Migration — Noah / v11.x integration (first-class Phase 5 concern)

**Operator-stated context (2026-05-09):** Noah is the active beta user on AMG OS v11.2 (possibly a later v11.x). Existing artifacts at `~/AMG_OS/` (live legacy codebase) and `~/Library/Containers/com.apple.DEC.AppPredictionInternal.DiagnosticExtension/Data/Downloads/AMG_OS_v11_1/` and `…AMG_OS_v11_2/` (archive locations) are **intentional reference** for the migration path — not stale code to discard. The pytest-discovery scoping fix in the preflight playbook is correct (CI runs only against AMG_OS_NEXT/tests), but the legacy mounts remain available read-only for the migration code Phase 5 Track B-M (below) reads.

### §14.1 — How Noah maps onto locked core concepts

| Concept | Noah's beta state | Action in Phase 5 |
|---|---|---|
| L0.5 two-tier producer model | AMG Co-Creator (Internal tier) per DR-012 and DR-016 | Migrate Noah's records as internal co-creator migration artifacts under operator-controlled custodianship |
| L0.11 unified onboarding gate | Streamlined internal-team onboarding path (not full subscriber 7-step flow) | Locked by DR-012 and DR-016 for co-creator handling |
| L0.12 pricing | Not on subscriber pricing ladder (deferred-comp co-creator arrangement) | Locked by DR-016 |
| L0.13 scheduled submissions | v11.x cadence preferences in legacy decision logs | Migrate into `submission_schedules` table on cutover |
| L0.14 payment processor stack | Stripe migration for Noah at Phase 4 close OR v1.0 | Q4.6 decides; affects DR-006 status |
| I1–I5 invariants | v11.x records may not all satisfy I1–I5 (esp. I5 snapshot JSON) | Migration code synthesizes I5 snapshots from v11.x decision logs; gap-flag any I3/I4 violations for operator review |
| P11 plaintext PII guard | v11.x YAMLs may contain plaintext PII | SOPS-encryption sweep during ingest; pre-commit guard catches anything that leaks through |
| Phase 5 Track B pattern library | Noah's v11.x titles are seed corpus | Mine `~/AMG_OS/data/reviewed/*.json` for confirmed titles; tag by style enum from REVIEW_WORKFLOW.md |
| Phase 5 Track D preference learner | Noah's review history is training data | Activate learner once Noah's reviewed-scene count crosses Q5.6 threshold (~100 per v12 preview) |
| Phase 5 Track F feedback loop | Noah's v11.x submission outcomes are first feedback | Backfill `copy_performance` from v11.x platform-status logs where available; live signals start on cutover |

### §14.2 — New track: Track B-M (Beta Migration)

Slots between current Track A (schema land) and current Track B (pattern library ingestion). Acts as the data prerequisite for everything downstream.

- **B-M.1** — Inventory Noah's v11.x state. Locate canonical artifact paths (`~/AMG_OS/data/reviewed/`, `~/AMG_OS/data/distribution_status/`, `~/AMG_OS/data/performer_documents/`, `~/AMG_OS/data/logs/`, plus any data the Container archives surface).
- **B-M.2** — Extend `legacy_v11_2_parser` (per CLAUDE.md §14) to read every artifact type Noah's beta produced; emit AUTHORITATIVE / REFERENCE / UNKNOWN-gap per record.
- **B-M.3** — Migration plan per Q5.10 decision: read-only ingest, two-way sync, or hard cutover. Each path has different schema-write semantics.
- **B-M.4** — Backfill compliance invariants. For every v11.x scene/release: synthesize I5 snapshot JSON from decision-log inputs; flag any I1/I3/I4 violation for operator review per CLAUDE.md §12 ask-before-applying (no silent backfill of compliance state).
- **B-M.5** — Pattern library seed extraction. Mine confirmed titles from `data/reviewed/*.json`; tag by `REVIEW_WORKFLOW.md` style enum; populate `title_patterns` with `source_attribution = 'amg_v11_noah'` and operator-curation pass.
- **B-M.6** — Operator preference learner seed. Replay Noah's pick / edit / reject events from review logs into `operator_preferences` (signal_weight=initial). Learner stays OFF until Q5.6 activation threshold met.
- **B-M.7** — Performance loop backfill. Where v11.x submission-status logs include platform outcomes, backfill `copy_performance` rows attributed to the chosen variant.
- **B-M.8** — Cutover validation. End-to-end test: Noah's v11.x state ingested → AMG_OS_NEXT pipeline runs the same set of releases → results match within tolerance per a parity gate similar to Phase 2's 27.40% drift baseline.

### §14.3 — Boundary: legacy code stays read-only reference

Per CLAUDE.md §17 rule 2 (read-only default for AI-tool filesystem access), AMG_OS_NEXT code does NOT write to `~/AMG_OS/` or to `~/Library/Containers/.../AMG_OS_v11_*/`. The migration is one-way: read v11.x, write AMG_OS_NEXT. v11.x continues running independently for Noah until cutover lands.

### §14.4 — Cross-phase impact map

Phase 5 absorbs the migration code, but consequences ripple:

| Phase | Impact |
|---|---|
| Phase 1 (Data Foundation) | `legacy_v11_2_parser` gains B-M.2 extensions; ADR-001 dependency lock revisited if v11.x parsing surfaces new dependency needs |
| Phase 4 Track G operator-side | DR-006 Stripe activation depends on Q4.6 (Noah migration timing); G.7 timeline may shift |
| Phase 9 Operator Console | Noah is the first console user — UI must surface migration status, gap-flags, and parity drift |
| Phase 11 Client Portal | Noah is the first client portal user — flows must accommodate beta-grandfather state |

### §14.5 — Privacy + custodianship for Noah's records (CLAUDE.md §6)

100% local AI inference (Ollama localhost:11434). Noah's data never touches cloud. SOPS+age encryption at rest (P12). Per DR-012 and DR-016, Noah is handled as AMG co-creator/internal migration (not Tier 1 subscriber framing). If Q5.10 chooses two-way sync, Noah's authoritative record stays in v11.x until cutover, and AMG_OS_NEXT acts as read-replica.

---

## §15. Competitive positioning anchor (Noxovision deep-dive synthesis, 2026-05-09)

The Noxovision deep-dive research (5 files committed to `~/AMG_OS_NEXT/docs/competitive/`) produced canonical positioning artifacts that this Phase 5 build guide (and every Phase 5+ deliverable) should reference verbatim.

### §15.1 — Canonical "Why Build This" anchor

Use this paragraph verbatim in pitch decks, build-guide preambles, customer-facing one-pagers, and DR-015:

> AMG OS NEXT exists because winning this market requires more than AI inference: it requires legal-grade custody, operator-grade workflow, and revenue-grade execution in one audited system. NOXO can be excellent at infrastructure CV and still not solve AMG's core buyer workflow end-to-end. The defensible strategy is to preserve AMG's control-plane moat, legally copy non-proprietary architecture/commercial patterns, and use NOXO only as an optional constrained partner where it closes specific feature gaps faster.

### §15.2 — Three-axis defensible moat (per synthesis Q6)

| Axis | Moat statement |
|---|---|
| **vs E1 — AMG LLC + Amy legal layer** | E1 is necessary legal authority but not execution infrastructure. AMG NEXT's moat is turning legal obligations into enforceable runtime behavior — gates, invariants, immutable logs, operator controls — rather than relying on policy intent alone. |
| **vs E2 — AMG OS (current v11.x + cloud edition)** | E2 proves real operator workflow; NEXT creates formal reproducibility and auditability at scale through codified transaction models and decision controls. The moat is not replacement; it is systemization of proven workflow into governed platform behavior. |
| **vs E4 — NOXO / Noxovision** | NOXO's moat is infra CV flexibility; AMG's moat is business-layer execution under legal constraints for creator/studio/distributor outcomes. Even where NOXO is stronger on select CV primitives, AMG remains differentiated by integrated custody-aware onboarding, submission/distribution flow, and operator-level monetization lifecycle. |

### §15.3 — Phase 6+ ADR pipeline from synthesis Q8

Eight capability gaps with explicit COPY / PARTNER / BUILD verdicts. Each becomes a Phase 6+ ADR (numbers ADR-029 through ADR-036) when its phase opens. Phase 5 closure criteria §9 is unaffected; this is downstream pipeline:

- ADR-029 Biometric creator-matching parity → **BUILD** (preserve documentary-ID legal model; biometric is assistive, not substitute)
- ADR-030 On-frame age-risk estimation → **BUILD** (keep §2257 documentary semantics; AI age = risk-tray signal only)
- ADR-031 OCR with timing → **COPY** (low-IP-risk pattern; implement against AMG taxonomy)
- ADR-032 Semantic fingerprinting resilience → **PARTNER → BUILD** (fast partner parity; in-house long-term)
- ADR-033 Capacity / latency ops API surface → **COPY** (improves operator trust; minimal IP risk; Phase 9 console)
- ADR-034 Explicit breach-notification contractual language → **BUILD** (codify into AMG contracts + ADR policy)
- ADR-035 Confidence-band override governance → **BUILD** (harden ADR-009 thresholds; expose as customer-trust signal)
- ADR-036 Processor / sub-processor role clarity → **PARTNER (conditional)** (DPA-first diligence; gates Q7 partnership scenario 1)

### §15.4 — Three partnership scenarios with NOXO (per synthesis Q7)

Standing options. Operator picks per ADR per opportunity:

1. **Upstream vendor relationship** — requires §6/§17 amendment (overlaps with **DR-014** cloud-edition adoption); strict default-off; per-call permits; immutable audit; hard local fallback. Eligible for ADR-032, ADR-036.
2. **Co-marketing for shared platform customers** — no data-plane integration; standards/checklists only; sensitive data stays local + policy-gated. Always available.
3. **Feature-specific bake-off** — time-boxed benchmarks on synthetic/sanitized datasets; per-capability decision. Most useful for ADR-029, ADR-030, ADR-032.

### §15.5 — Reference

Full evidence: `~/AMG_OS_NEXT/docs/competitive/noxovision_synthesis_2026-05-09.md` (canonical v2 synthesis from 5-run research). DR-015 in decision register captures the operator decision to adopt this positioning. ADR-021 (Phase 5 charter) and ADR-022–ADR-028 (Phase 5 specifics) precede Phase 6+ ADR-029–036 above. The complete Phase 6+ ADR-029 through ADR-036 sequence is cross-referenced in the master strategic plan and remains the downstream execution pipeline.

---

## §13. Authority status of this document

This document is **AUTHORITATIVE** for Phase 5 charter and execution planning.

Authority basis:
1. Charter scope is locked by DR-011.
2. Noah co-creator migration posture is locked by DR-012 and DR-016.
3. Cloud-edition compute posture is locked by DR-014 and reflected in CLAUDE.md.
4. This guide is promoted from outputs to `~/AMG_OS/docs/AMG_OS_NEXT_PHASE_5_BUILD_GUIDE.md` as v1.0.


**END OF v1.0 AUTHORITATIVE GUIDE**
