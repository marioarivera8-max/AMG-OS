# AMG_OS_NEXT — Phase 4 Build Guide

**Version:** v1.1 (DRAFT — in progress 2026-05-08)
**Authority:** AUTHORITATIVE for Phase 4 (Distribution + Customer & Revenue Model + Vendor Management)
**Predecessor:** `~/AMG_OS/docs/AMG_OS_NEXT_PHASE_3_BUILD_GUIDE.md` v1.1 (Compliance + §2257 + per-platform legal forms)
**Operating Brief:** `~/AMG_OS_NEXT/CLAUDE.md` (post §16-§19 amendments)
**Operator:** Tony (sole operator and final decision authority)
**Custodian of Records:** Amy Lew, All Media Group, 18819 Topham, LA 91335

---

## §0. Preamble — locked decisions and Phase 3 lessons

Phase 4 begins under FAILURE PROTOCOL discipline. Every claim cites a source. Authority bands: AUTHORITATIVE / REFERENCE / DEPRECATED / UNKNOWN-gap. No silent contradiction resolution. Operator confirms every cross-subsystem or destructive action.

Locked decisions carried into Phase 4 from operator approval in earlier sessions:

L0.1 — Customer schema is **five transaction types**: `enterprise_b2b`, `individual_subscriber`, `platform_revenue_share`, `catalog_buyer`, `licensing_intermediary`. Locked 2026-05-08.

L0.2 — `licensing_intermediary` is the AMG-as-middleman license model: AMG holds a master license from upstream rights holders (studio / creator / brand / IP owner) and grants downstream sublicenses to platforms / redistributors / third-party buyers. AMG is the named licensee on the upstream agreement and the named licensor on the downstream agreement. Revenue model: margin spread between upstream royalty and downstream fee. Compliance implication: AMG remains a **secondary producer** under 18 USC § 2257 for any content flowing through this channel.

L0.3 — Beta gate L1 = Amy Lew legal review of all `licensing_intermediary` schema fields, sublicense template language, and chain-of-rights documentation **before any production licensing transaction executes**. Sign-off recorded in `docs/decisions/decision_register.md`.

L0.4 — TubeSubmitter throughput parity is non-negotiable. TSS's claim is "submit to 100+ sites in seconds." If AMG_OS_NEXT cannot match or surpass this, the system is competitive-dead at launch regardless of every other feature. Phase 4 architecture must achieve measurable throughput parity by beta close.

L0.5 — Two-tier producer model carries from Phase 3 with **unified onboarding verification (per L0.11)**. Tier 1 = subscriber-as-primary-producer with self-custodianship of primary records; AMG holds verification copies as defensive co-record. Tier 2 = AMG-as-secondary-producer with full custodian-of-records obligations; Amy Lew is custodian of Tier 2 content records and AMG's verification archive. Both tiers go through the same onboarding verification gate. The Section 230 passive-conduit posture is intentionally NOT relied upon — AMG's defense is verified-at-onboarding, not editorial-non-involvement.

L0.6 — Cross-tool orchestration rules (CLAUDE.md §16) govern Phase 4: Claude (synthesis / spec / governance), Cursor (implementation / file edits / commits), GPT (independent verification when consulted). No tool overrides operator. Conflicts surface; never silently resolved.

L0.7 — AI-safe infrastructure rules (CLAUDE.md §17) apply to all Phase 4 code: localhost-only by default, read-only filesystem default, scoped credentials per worker, snapshot-before-destructive-action, irreversible-operation block list, migrations-only schema changes, audit log immutability.

L0.8 — Versioned prompt registry rule (CLAUDE.md §19) applies to every prompt file Phase 4 creates. No inline string literals in prompt-using code paths.

L0.10 — **TSS competitive positioning is reliability-and-features superiority, not raw speed parity.** Operator-locked 2026-05-08. AMG does NOT need to beat TSS on submission throughput. AMG wins on submission *success rate* (captcha bypass, bot-detection evasion, pre-flight overlay scan, per-platform packet completeness), AI-generated assets (covers, titles, descriptions, GIFs, trailers with quality threshold gating), unified onboarding verification (no other competitor verifies at the door), tier-aware compliance routing, and customer-relationship layer (5 transaction types incl. licensing intermediary). Phase 4 §3 throughput targets are aspirational; the CI perf budget enforces no-regressions but does not block ship on TSS-parity speed metrics.

L0.14 — **Payment processor stack: Stripe primary + Tier 2 multi-method payouts.** Operator-locked 2026-05-08. AMG positions as SaaS-membership-service (not adult-content seller); Stripe is approved processor for inbound subscriber payments + B2B invoicing. International coverage via Stripe's 135+ currencies + native local payment methods (SEPA, iDEAL, BACS, BECS, Konbini, OXXO, Boleto, Mercado Pago, GrabPay, Alipay, etc.) + Apple Pay + Google Pay + PayPal + Klarna/Afterpay/Affirm BNPL. Backup processor (CCBill or Segpay) held cold in env-var swap-ready posture in case Stripe underwriting terminates post-launch. Tier 2 payouts use multi-method stack: Stripe Connect (Custom) primary for international Tier 2 + automatic 1099 reporting; Wise Business backup for high-value international with cheaper FX; direct ACH for large US studios; USDC stablecoin optional for crypto-preferring Tier 2 customers; legacy paper check via Amy for studios with specific banking constraints. Per-customer payout method recorded in vendors_licensees table (Track A schema; processor + processor_customer_id columns).

L0.15 — **[OPERATOR-PENDING IN THIS COMMIT] AMG Co-Creator / Internal tier (free, full access, separate from subscriber tier ladder).** Proposed 2026-05-09 pending explicit typed operator approval (`approve L0.15 in commit`) OR typed deferral (`defer L0.15`). Distinct from L0.12 subscriber tiers (Basic / Pro / Studio Tier 2). AMG core employees, founding-team co-creators (e.g., Noah per DR-016), and Amy Lew (custodian-of-records partner) all have Internal tier access: free, permanent, full system, no compute quotas. Internal tier is NOT customer-facing — never marketed, never sold, never converted to paid. Distinct from beta-grandfather windows (which apply to subscribers). Internal-tier seats granted by operator decision only; revoked by operator decision only.

L0.13 — **Scheduled submissions with AI-suggested cadence are first-class Phase 4 features.** Operator-locked 2026-05-08. Submitting a customer's entire backlog at once to all destination platforms is operationally counter-productive — saturates platforms, triggers spam-detection, hurts customer SEO/discovery, and burns captcha budget. AMG_OS_NEXT must support: (a) per-customer scheduled drops with weekly / bi-weekly / monthly / custom cadence, (b) AI-suggested optimal cadence based on customer's backlog size + per-platform velocity recommendations + tier policy, (c) AMG-operator-side scheduling for Tier 2 customers with multi-month horizon (90/180/365 days), (d) per-platform schedule overlays (e.g., Mondays-only to Pornhub, Tuesdays/Fridays to xHamster) honoring platform burst-penalty and consistency-reward signals. Feature gated by tier per L0.12: Basic = immediate submission only; Pro = AI-suggested + custom scheduling; Studio Tier 2 = AMG-managed multi-month operator scheduling.

L0.12 — **AMG individual_subscriber pricing band locked at premium positioning.** Operator-locked 2026-05-08. Pricing tiers:
- AMG Basic Tier 1: **$49.99 / month** or **$499 / year** (saves $99/yr; ~17% annual discount)
- AMG Pro Tier 1: **$99.99 / month** or **$999 / year** (saves $200/yr; ~17% annual discount)
- AMG Studio Tier 2: **custom enterprise pricing** (per L0.5; full-service distribution with custodian)

Comparison anchor: TSS pricing is $34.95/mo, $99/3mo, $349/yr (cited in `docs/competitive/tss_business_analysis.md`, commit 69ca048). AMG's premium of ~43% over TSS at the Basic tier is justified by: (a) unified onboarding verification overhead per L0.11, (b) AI inference costs per L0.10 reliability moat, (c) captcha bypass + bot-detection evasion operations, (d) tier-aware compliance routing infrastructure, (e) custodian-of-records architecture for Tier 2 customers. Pricing is reviewable at end of beta cohort; pricing changes require operator decision register entry and CLAUDE.md auto-update trigger.

L0.11 — **Unified onboarding verification gate.** Operator-locked 2026-05-08. Every subscriber, regardless of tier, completes full compliance verification before payment unlocks and before any submission can route through the pipeline. Verification requires: (a) subscriber's valid government-issued photo ID, (b) photo ID for every performer who will appear in any submitted content, (c) signed performer release per performer, (d) co-performer consent form when a scene contains multiple performers, (e) subscriber attestation of producer-of-record status appropriate to their tier. Verification records are stored in the AMG vault (SOPS-encrypted, vault-pathed). Tier 1 subscribers retain primary custodianship of their own records; AMG's vault copies are defensive co-records. Tier 2 subscriptions transfer custodianship to Amy Lew. The intake gate (Phase 3 Gate 1) refuses any submission where every performer in the scene does not already have a verified onboarding record on file.

L0.9 — **Five correctness invariants** (cross-cutting, encoded in Phase 3 Track B.8 and inherited by Phase 4 through `ComplianceEngine.run()`):

- **I1.** Scenes are atomic compliance units. A scene is the smallest object that can carry a compliance decision; sub-scene fragments share the parent scene's compliance state.
- **I2.** 2257 records immutable once created. INSERT-only; UPDATE/DELETE revoked at DB role level on `compliance_2257_records` and `compliance_audit_events`.
- **I3.** All assets trace to a scene. Every row in `assets` carries a non-null `scene_id`; orphaned assets blocked at insert by check constraint.
- **I4.** Every performer links to verified identity documents. Every row in `performers` requires non-null `verified_id_doc_id` before any release referencing that performer can transition to `executed`.
- **I5.** Every compliance record includes a snapshot JSON. `compliance_snapshot_json` column is non-null on every gate decision, capturing the full state of context inputs at the time of decision (for replay / audit).

These five invariants are runtime-validated by `amg_os_next/compliance/invariants.py`, DB-level enforced by Alembic migration `<rev>_invariant_constraints.py`, and CI-gated by `tools/invariant_check.py`. ADR-011 documents the rationale.

Phase 3 lessons baked into Phase 4 prevention layer (P-rules continued):

- **P11:** No plaintext PII in any committed file. Pre-commit guard `tools/plaintext_pii_guard.py` extends to `customers/`, `vendors/`, `licensing/` directories.
- **P12:** Every customer record, vendor contract, and licensing agreement is SOPS+age encrypted at rest.
- **P13:** All money-moving operations require operator-typed confirmation per occurrence. No AI tool initiates a payment, transfer, refund, payout, or chargeback dispute autonomously.
- **P14:** Platform credential rotation cadence is enforced by `tools/credential_freshness_guard.py` (90-day max age for any platform API token).
- **P15:** Throughput regressions are CI-gated. A perf budget file (`perf/throughput_budget.yaml`) sets the floor; any commit that fails the budget breaks the build.

---

## §1. Phase 4 scope

Phase 4 builds three interlocking subsystems on top of Phase 3's compliance foundation:

**Distribution.** Per-platform submission engine targeting US + foreign tube sites, VOD platforms, cam networks, VR platforms, and B2B redistributors. Must achieve TSS-parity throughput (100+ sites in measurable single-digit minutes for full pipeline; sub-minute for queue-and-confirm UI return).

**Customer & Revenue Model.** Five-type transaction schema (per L0.1) with full lifecycle: lead → quote → contract → onboarding → recurring billing → reconciliation → renewal / churn. Includes vendor-side reconciliation for `platform_revenue_share` and `licensing_intermediary` flows.

**Vendor Management.** Counterparty registry (studios, networks, individual creators on the licensor side; platforms, redistributors, B2B buyers on the licensee side) with contract lifecycle, royalty statement processing, audit trail, and dispute handling.

Out of scope for Phase 4 (deferred to Phase 5+):

- Live-streaming / cam integration as a Production output
- Affiliate / referral program
- Tony-facing analytics dashboard beyond minimum viable observability
- Mobile apps (Tauri desktop only in Phase 4)
- Blockchain / NFT licensing primitives

---

## §2. Customer & revenue model — five-type schema

The five transaction types are first-class schema entities. Each has its own SQLAlchemy model, Pydantic v2 validators, Alembic migration, and per-type business rules. They are not subtypes of a single "Transaction" supertable — they have meaningfully different fields, lifecycles, and revenue-recognition rules.

### §2.1 — `enterprise_b2b`

Direct contracts with studios, networks, distributors. AMG provides production services (covers, descriptions, trailers, asset packaging) and / or distribution services (multi-platform submission, compliance handling, royalty reporting).

Schema fields (canonical): `customer_id`, `legal_entity_name`, `dba_name`, `contract_doc_path` (SOPS-encrypted YAML reference), `contract_start_date`, `contract_end_date`, `services_in_scope` (enum list: production, distribution, compliance, custodian, packet_assembly), `billing_cadence` (enum: monthly, quarterly, annual, per_release), `billing_amount_usd`, `payment_terms` (net_15 / net_30 / net_60 / prepaid), `signed_msa_path`, `signed_sow_paths` (list, one per active SOW), `account_manager_id`, `compliance_tier` (1 or 2), `status` (active / paused / terminated / churned), `created_at`, `updated_at`, `audit_trail_id`.

Revenue recognition: ratable over service period for fixed-fee SOWs; per-release recognition at delivery for per-release SOWs.

### §2.2 — `individual_subscriber`

Single-creator SaaS subscriber paying AMG monthly. Tier 1 by definition (subscriber is producer of record; AMG is a verified-content service vendor, not a producer). Per L0.11, subscriber must complete full onboarding verification BEFORE payment unlocks.

Schema fields: `customer_id`, `legal_name` (SOPS-encrypted), `stage_name`, `email` (SOPS-encrypted), `subscription_tier` (basic / pro), `monthly_price_usd` (per L0.12: basic=49.99, pro=99.99), `annual_price_usd` (per L0.12: basic=499, pro=999), `billing_cadence` (monthly | annual), `payment_method_id` (Stripe customer ID; no card data stored), `subscription_start_date`, `subscription_status` (see expanded enum below), `onboarding_status` (see L0.11 enum), `subscriber_id_doc_id` (FK to id_documents — verified subscriber ID), `subscriber_attestation_doc_id` (FK to release_documents — signed Tier 1 attestation), `authorized_performer_ids` (list of FK to performers — performers this subscriber is verified to submit), `compliance_tier` (always 1 for this type), `usage_metrics` (submissions_this_month, bandwidth_gb, storage_gb, ai_tokens_consumed_this_month), `created_at`, `updated_at`.

Tier feature gate (per L0.12 premium positioning):

| Feature | Basic ($49.99/mo) | Pro ($99.99/mo) |
|---|---|---|
| Unified onboarding verification (L0.11) | ✓ | ✓ |
| Per-platform compliance overlays (Phase 3 Track C) | ✓ | ✓ |
| AI cover / thumbnail generation | ✓ (basic threshold) | ✓ (pro threshold) |
| AI title generation (TSS parity) | ✓ | ✓ |
| AI description generation (TSS parity) | ✓ | ✓ |
| AI tag generation (TSS parity) | ✓ | ✓ |
| AI GIF preview generation | — | ✓ |
| AI trailer cuts (30s/60s/90s/120s) | — | ✓ |
| Bulk profile update across platforms | ✓ | ✓ |
| Scheduled submissions (per L0.13) | — | ✓ AI-suggested + custom |
| Multi-month scheduling horizon | — | up to 90 days |
| Concurrent submission lanes | 16 lanes | 32 lanes |
| Captcha bypass attempts / month | 1,000 | 5,000 |
| HITL operator review tray priority | standard | premium |
| Multi-account vault | — | ✓ |
| Customer support SLA | 48-hour | 12-hour |

Subscription status enum: `pre_onboarding` (account created, no docs submitted), `onboarding_in_progress` (docs submitted, awaiting AMG verification), `verification_failed` (one or more docs rejected; remediation queue), `verified_unpaid` (cleared verification, payment pending), `active` (paid + verified + submission privileges enabled), `past_due` (payment failed; grace window), `suspended_compliance` (compliance issue surfaced post-onboarding), `canceled`, `terminated`.

Onboarding status enum (per L0.11): `incomplete`, `subscriber_id_pending`, `performer_ids_pending`, `releases_pending`, `under_review`, `verified`, `blocked`.

State machine rules: `subscription_status` cannot transition to `verified_unpaid` until `onboarding_status = verified`. `subscription_status` cannot transition to `active` until payment is recorded. Adding new performers later requires re-entering `onboarding_in_progress` for those specific performers without losing access to already-authorized ones.

Revenue recognition: monthly recurring at billing date. Failed payment moves status to `past_due` and pauses submission privileges after 7-day grace window. Onboarding verification failures move status to `verification_failed` and route to operator review tray; no payment occurs until cleared.

### §2.3 — `platform_revenue_share`

AMG receives a percentage cut of revenue earned on a destination platform when AMG-distributed content sells. Common with VOD networks (AEBN, ADE), per-scene aggregators, subscription tube networks.

Schema fields: `customer_id` (the platform paying out), `platform_id` (foreign key to vendors registry), `revenue_share_pct_to_amg`, `revenue_share_pct_to_creator`, `min_payout_threshold_usd`, `payout_cadence` (monthly / quarterly), `royalty_statement_doc_paths` (list, one per period), `last_reconciled_period`, `disputed_amounts_usd`, `compliance_tier` (typically 2; AMG is secondary producer), `creator_share_recipients` (list of creator IDs receiving downstream cut), `created_at`, `updated_at`.

Revenue recognition: at receipt of royalty statement; reconciliation against AMG-side submission log catches discrepancies. Disputed amounts held in `royalty_disputes` table until resolved.

### §2.4 — `catalog_buyer`

One-off purchaser of an AMG-owned title or asset bundle. Examples: a regional distributor buying a back-catalog license for a specific territory; a content aggregator buying a curated bundle for a niche site.

Schema fields: `customer_id`, `purchase_id`, `purchased_at`, `total_price_usd`, `payment_method` (wire / Stripe / crypto-stablecoin), `payment_received_at`, `assets_purchased` (list of release_ids or asset_bundle_ids), `delivery_method` (sftp / s3-presigned / physical-drive), `delivery_completed_at`, `license_grant_doc_path` (SOPS-encrypted; specifies what the buyer can and cannot do with the asset), `compliance_tier` (typically 1; AMG sells finished asset, buyer becomes downstream distributor), `created_at`.

Revenue recognition: at delivery completion. No recurring revenue.

### §2.5 — `licensing_intermediary` — the AMG-as-middleman type (L0.2)

AMG holds a master license from upstream rights holders and grants downstream sublicenses. AMG is licensee upstream, licensor downstream. Margin = downstream fee minus upstream royalty owed.

Schema fields:
- `licensing_id` — primary key
- `upstream_licensor_id` — foreign key to vendors registry (the original rights holder)
- `upstream_license_doc_path` — SOPS-encrypted reference to the master license agreement
- `upstream_license_term_start` / `upstream_license_term_end`
- `upstream_territory` (enum list: us / na / latam / eu / uk / apac / global / custom)
- `upstream_exclusivity_flag` (boolean)
- `upstream_sublicensing_authority_granted` (boolean — must be `true` for any downstream sublicense to be valid)
- `upstream_royalty_pct` — what AMG owes upstream per downstream sublicense revenue dollar
- `upstream_minimum_guarantee_usd` — fixed amount AMG owes upstream regardless of downstream activity (zero for pure royalty deals)
- `downstream_licensee_id` — foreign key to vendors / customers (the entity AMG sublicenses to)
- `downstream_sublicense_doc_path` — SOPS-encrypted reference to the executed sublicense
- `downstream_term_start` / `downstream_term_end` — must fit within upstream term
- `downstream_territory` — must be subset of upstream territory
- `downstream_exclusivity_flag` — must be compatible with upstream exclusivity grants
- `downstream_fee_amount_usd` — what the downstream licensee pays AMG (flat fee or per-period)
- `downstream_fee_cadence` (one_time / monthly / quarterly / annual / per_use)
- `amg_margin_pct_estimated` — calculated field: `(downstream_fee - upstream_royalty_owed) / downstream_fee * 100`
- `audit_cadence_days` — how often AMG audits downstream usage to verify royalty compliance
- `renewal_policy` (auto_renew / negotiate_at_end / no_renewal)
- `chain_of_rights_doc_paths` — list of SOPS-encrypted documents proving unbroken chain from creator → upstream licensor → AMG → downstream licensee
- `compliance_tier` — always 2 for this type (AMG is secondary producer; § 2257 obligations attach)
- `secondary_producer_attestation_signed` — boolean; references custodian designation
- `amy_lew_legal_review_completed` — boolean; references beta gate L1 sign-off
- `amy_lew_legal_review_doc_path` — SOPS-encrypted reference to Amy's signed acknowledgment
- `status` (drafting / under_review / executed / active / disputed / terminated / expired)
- `created_at`, `updated_at`, `audit_trail_id`

Business rules:

- B-LI-1: A `licensing_intermediary` record cannot transition to `executed` status until `amy_lew_legal_review_completed` is true (Beta gate L1).
- B-LI-2: Downstream territory must be a subset of upstream territory. Validated at draft time and at any update; violations block save with operator alert.
- B-LI-3: Downstream term must fit within upstream term. Same validation pattern as B-LI-2.
- B-LI-4: `upstream_sublicensing_authority_granted` must be `true` before any downstream sublicense can be saved. Hard validator.
- B-LI-5: Chain-of-rights documents must trace from creator (named in 2257 record) through every upstream licensor to AMG. Gap = blocking validation error.
- B-LI-6: Royalty reconciliation runs per `audit_cadence_days`; discrepancies surface to operator review tray.
- B-LI-7: Renewal triggers fire 90 / 60 / 30 / 14 days before `upstream_license_term_end` regardless of `renewal_policy`. Operator-actionable surface in console.
- B-LI-8: Termination of upstream license auto-terminates all downstream sublicenses derived from it. Cascading status update; downstream licensees receive operator-reviewed notice.

Revenue recognition: monthly accrual based on downstream fee cadence; upstream royalty obligation accrued in mirror table `licensing_upstream_obligations` for reconciliation.

### §2.6 — Unified Onboarding Verification Flow (per L0.11)

Every subscriber regardless of tier traverses the same onboarding sequence. The intake gate (Phase 3 Gate 1) refuses any submission whose scene includes a performer not already onboarded.

**Onboarding sequence:**

1. **Account creation.** Email + password + initial demographics. `subscription_status = pre_onboarding`. No payment captured. No submission privileges. No data collected beyond what's needed to identify the subscriber for support contact.

2. **Subscriber ID submission.** Subscriber uploads valid government-issued photo ID (passport, driver's license, state ID, or equivalent). AMG vision-validates document type, extracts name + DOB + expiration, runs age verification (must be 18+), checks for tampering signals. SOPS-encrypted vault path written to `id_documents` table; FK landed in subscriber record. `onboarding_status = performer_ids_pending` if subscriber ID passes; `verification_failed` with remediation note if not.

3. **Performer ID submission.** For each performer the subscriber will include in any future submission, subscriber uploads performer's valid photo ID. Same vision validation pipeline. Each performer ID gets its own row in `id_documents` and a `performer_onboarding_record` row linking subscriber → performer with verification timestamp. `onboarding_status = releases_pending` if all performer IDs pass.

4. **Signed performer releases.** Subscriber uploads or generates (using `performer_release.v1.template.md` rendered against the verified performer record) one signed release per performer. Stored in `release_documents` table linked to performer + scene-context. `onboarding_status = under_review`.

5. **Co-performer consent (if applicable).** When subscriber declares a scene will contain multiple performers, the `coperformer_consent.v1.template.md` is generated and signed by all participating performers. One consent doc per scene-grouping. Stored in `release_documents`. Multi-performer scenes cannot bypass this step.

6. **Subscriber attestation.** Subscriber signs `tier_1_subscriber_attestation.v1.template.md` (Tier 1) or routes to Tier 2 onboarding flow with `custodian_designation.v1.template.md` plus secondary-producer agreement. `onboarding_status = verified` if all checks pass.

7. **Payment unlocks.** `subscription_status = verified_unpaid`. Subscriber enters payment details. On successful payment capture, `subscription_status = active`. Submission privileges enabled.

**Adding new performers post-onboarding:** Subscriber initiates "add performer" flow. New performer ID + release + consent (if applicable) submitted. `performer_onboarding_record` row created with verification timestamp. Submission privileges for that specific performer activate when verified. Already-authorized performers continue unaffected.

**Onboarding artifacts in the vault:**
- `id_documents` table: id_doc_id, owner_type (subscriber|performer), owner_id, doc_type (passport|drivers_license|state_id|other), vault_path (SOPS-encrypted), verified_at, verification_method, verification_confidence, expiration_date, issuing_authority, audit_trail_id.
- `release_documents` table: release_doc_id, performer_id, scene_id_optional, doc_type (performer_release|coperformer_consent|tier_1_attestation|custodian_designation|tier_2_secondary_producer_agreement), vault_path (SOPS-encrypted), signed_at, signature_method, template_version, audit_trail_id.
- `performer_onboarding_record` table: record_id, subscriber_customer_id, performer_id, id_doc_id, latest_release_doc_id, status (verified|pending|blocked|expired), verified_at, expires_at, audit_trail_id.

**Custodianship of onboarding records.** Per L0.5: For Tier 1 subscribers, the subscriber is primary custodian of their own records; AMG's vault copies are defensive co-records. For Tier 2, Amy Lew is custodian. For the AMG-side verification archive itself (the meta-layer documenting which subscribers AMG has verified), Amy Lew is custodian under the same designation as Tier 2 — operator decision: a single custodian for the AMG record-keeping operation simplifies legal architecture and is operator-locked 2026-05-08.

**Beta gate L1 scope clarification.** Amy Lew reviews the Tier 2 set only at beta gate L1: `performer_release.v1.template.md`, `coperformer_consent.v1.template.md`, `custodian_designation.v1.template.md`. The Tier 1 attestation template (`tier_1_subscriber_attestation.v1.template.md`) goes through a separate review path with general counsel for AMG's customer-facing legal hedge — different hat, different review.

### §2.7 — Schema relationships

A single AMG release can have transactions of multiple types attached. Example: a Maximo Garcia scene released on AEBN (`platform_revenue_share`) and licensed to a regional EU aggregator (`licensing_intermediary`) and sold as a back-catalog bundle 18 months later (`catalog_buyer`). The schema must support this multiplicity without coupling — each transaction stands alone, joined to the release via release_id.

Junction table: `release_transactions` (release_id, transaction_type, transaction_id, attached_at).

---

## §3. Distribution — reliability-and-features superiority (the moat, repositioned per L0.10)

Per L0.10, the moat is no longer "submit faster than TSS." The moat is "submit *better* than TSS — higher success rate, fewer takedowns, AI-generated assets, unified verification, two-tier compliance routing, captcha and bot-detection bypass." Speed is good-enough; reliability + features + compliance is the differentiator.

The throughput analysis from `docs/competitive/tss_throughput_analysis.md` (delivered by Stream 2 Cursor research run) still feeds §3 — but now informs *floor* values (perf budget no-regression gates) rather than *ceiling* targets to beat. Sub-2-second batch UI return remains a usability requirement; aggressive sub-60s end-to-end is aspirational.

**Why this positioning is defensible:**

- TSS-style competitors have visible failure modes the user community complains about (captcha hits, bot-detection account churn, deny-list rejections, post-submission takedowns). Beating those failure modes is a stronger moat than beating raw speed.
- AMG's verified-onboarding posture (per L0.11) means platforms receiving AMG submissions know the content has documented compliance, raising platform-acceptance rates.
- AI-generated cover/title/description/GIF/trailer assets at quality threshold gating saves subscribers manual production work TSS does not provide.
- Two-tier compliance routing serves both indie creators and full-service-distribution customers in a single product — a wider market than TSS addresses.
- Customer-relationship layer (5 transaction types incl. licensing intermediary) means AMG can monetize multiple revenue paths off the same content infrastructure.

### §3.1 — Architectural pillars (preliminary)

Five pillars carry the throughput moat. All five must hit their targets; weakest link sets the operator-perceived speed.

**Pillar A — Parallelism.** Asyncio-based worker pool with per-platform adapter pattern. One coroutine per active submission. Worker count auto-scales based on system load and platform-specific rate limits.

**Pillar B — Pre-rendered asset variants.** All output formats (cover JPG, vertical thumb, horizontal thumb, GIF preview, multiple trailer lengths, multiple resolution scene files) generated upstream of submission, cached, deduplicated by content hash. Submission pulls from cache; never blocks on transformation.

**Pillar C — Persisted session state.** Login sessions, cookies, CSRF tokens cached per platform with TTL-aware refresh. Re-authentication only on expiry, never per submission.

**Pillar D — Graceful failure isolation.** One platform's downtime does not block others. Per-site retry policy with exponential backoff, max-attempt cap, and operator-visible failure tray. Batch-level success metric reported.

**Pillar E — Operator-perceived UI return time vs. end-to-end completion.** UI returns "queued" confirmation in sub-second time across the full batch. End-to-end completion (all platforms confirmed) reported in real-time via a status board, not blocking the operator's next action.

### §3.2 — Platform adapter pattern (preliminary)

```
amg_os_next/distribution/platforms/
  __init__.py
  base.py                  # PlatformAdapter ABC
  aebn.py                  # one adapter per platform
  ade.py
  slr.py
  ... (one file per supported platform)
  registry.py              # auto-discovery of adapters
```

`PlatformAdapter` ABC defines: `submit(release, asset_bundle) -> SubmissionResult`, `authenticate() -> Session`, `validate_assets(asset_bundle) -> ValidationResult`, `submission_method` (api / form / ftp / email — determines which transport pipeline runs), `rate_limit_policy`, `retry_policy`.

Per-platform overlay (from Phase 3 §2 — banned terms, deny lists, format rules) loaded from `compliance/platforms/<platform_id>.yaml` at adapter init. Overlay drift detected by CI guard.

### §3.3 — Submission method classification

Each platform classified into one of four submission methods. Method determines which transport pipeline executes:

- **api** — direct REST / GraphQL API call. Fastest, sub-second per site. Preferred where available.
- **form** — web form fill via headless browser (Playwright). 3-30 seconds per site depending on platform JS complexity.
- **ftp** — FTP / SFTP upload of asset bundle. Bandwidth-bound; concurrent uploads allowed.
- **email** — email submission with attachments. Slowest; used only where no other method exists.

Stream 2 TSS research will populate the per-platform classification table at `docs/competitive/tss_site_inventory.md` and feed it into `compliance/platforms/<platform_id>.yaml`.

### §3.4 — Asset transformation pipeline

Pre-Phase 4 produces every asset variant a downstream platform might request. Phase 4 confirms all variants are present before queueing submission; missing variants block at Gate 3 (preship).

Variants generated per release:
- 1 master scene file (highest resolution available)
- N down-scaled scene files (1080p, 720p, 480p) — generated lazily on first platform request, cached
- 1 cover JPG (high-res master, 1920x2880 default)
- N cropped cover variants per platform spec (vertical, horizontal, square, ultra-wide)
- 1 GIF preview (animated, 6-12 seconds, looped)
- N trailer cuts (30s, 60s, 90s, 120s default lengths)
- 1 contact sheet (visual scene index)
- 1 metadata bundle (title, description, tags, cast, runtime, release date — multi-platform variants)

All variants content-addressed (SHA256). Deduplication across releases when same asset reused.

**AI-generated metadata parity with TSS (per L0.10 + L0.12):**

TSS ships AI title + description + tag generation in beta (XBIZ press release 295709, commit 69ca048 in `docs/competitive/tss_business_analysis.md`). AMG must match this baseline at minimum across both Basic and Pro tiers; AMG-differentiating AI features (cover, GIF, trailer) live above the parity floor.

Parity floor (Phase 4 Track C must implement):
- AI title generation from video — vision model produces ranked title candidates with confidence scores; operator preview before commit
- AI description generation from video — vision model produces 1-3 paragraph description with confidence
- AI tag generation from video — vision model produces tag set with per-tag confidence; per-platform tag-count cap applied
- Token consumption metering per `usage_metrics.ai_tokens_consumed_this_month` (per L0.12 schema)
- Quality threshold gating per CLAUDE.md §18 (>=0.85 auto-apply, 0.60-0.85 review tray, <0.60 hard-flag)

AMG-differentiating asset AI (Pro tier per L0.12):
- AI cover / thumbnail generation with brand-consistent prompting
- AI GIF preview with motion-aware key-frame selection
- AI trailer cuts at 30s / 60s / 90s / 120s lengths with scene-boundary detection

### §3.5 — Throughput perf budget (preliminary — finalize after TSS research)

Numbers below are placeholders pending Stream 2 research findings. Final values lock when TSS measured throughput is documented.

| Metric | TSS measured (TBD) | AMG_OS_NEXT target | Hard floor (CI gate) |
|---|---|---|---|
| Operator-perceived UI return for 100-site batch | TBD | < 2s | < 5s |
| End-to-end completion for 100-site batch (api-method sites only) | TBD | < 60s | < 180s |
| End-to-end completion for 100-site batch (mixed methods) | TBD | < 5min | < 12min |
| Per-platform median submission latency (api) | TBD | < 800ms | < 2s |
| Per-platform median submission latency (form) | TBD | < 8s | < 25s |
| Batch success rate (no platform downtime) | TBD | > 98% | > 92% |
| Batch success rate (one platform down) | TBD | > 97% | > 90% |

Perf budget enforced by `perf/throughput_budget.yaml` and CI guard `tools/perf_budget_check.py`.

### §3.6 — Concurrency limits and backpressure

Per-platform concurrency cap (default 3 simultaneous submissions to same platform; tunable per adapter based on platform-stated rate limits). Global concurrency cap based on system resources (default 32 simultaneous submissions across all platforms; configurable).

Backpressure: when global cap hit, new submissions queued in Redis; queue depth surfaced in operator console; queue-clearing rate displayed real-time.

### §3.7 — Failure surfaces

Three failure surfaces, each with distinct operator-action UI:

- **Real-time failure tray** — shows failures as they happen during a batch run. Operator can retry individual submissions or skip.
- **Post-batch failure report** — summary at batch completion: succeeded / failed / skipped per platform.
- **Persistent failure registry** — tracks recurring failures for the same release / platform combination over time. Surface for systemic issues (deprecated API, changed form schema, banned account).

---

## §4. Vendor management (preliminary outline — to expand in §4.1-§4.7)

Vendor registry covers two distinct counterparty roles:

**Licensor side** (rights holders supplying content to AMG):
- Studios (e.g., AMG-owned shells, third-party studios selling masters)
- Individual creators (per Phase 3 catalog)
- Brand IP holders (for cross-licensed material)

**Licensee side** (entities receiving content from AMG):
- Tube sites and VOD platforms (destinations of `platform_revenue_share` flows)
- Redistributors (downstream `licensing_intermediary` licensees)
- B2B buyers (buyers in `catalog_buyer` and `enterprise_b2b` flows)

Schema sketches for §4.1 (Licensor registry) and §4.2 (Licensee registry) to follow. Both encrypt all PII at rest. Both join to `licensing_intermediary`, `platform_revenue_share`, and other transaction tables via foreign key.

[§4 to be expanded after §3 throughput research lands]

---

## §5. Phase 4 track structure (preliminary)

Mirroring Phase 3's A-G pattern. Each track is a self-contained unit of work with its own paste-ready Cursor prompt, deliverables, validation chain, and FAILURE PROTOCOL reporting.

- **Track CS — Scheduled submissions + AI cadence suggestion** (after Track C lands; per L0.13 — submission_schedules + scheduled_submissions tables, ScheduleStrategy enum, AISchedulerService with per-platform velocity heuristics + LLM-augmented suggestions, Dramatiq scheduled_at integration, operator calendar surface, conflict detection, per-platform scheduling overlays in compliance/platforms/*.yaml)
- **Track A — Customer & vendor schema land + onboarding tables** (Alembic migrations for 5 transaction types + 2 vendor sides + L0.11 onboarding artifacts: `id_documents`, `release_documents`, `performer_onboarding_record`; Pydantic models with subscription_status / onboarding_status enums; SOPS-encrypted YAML scaffolding)
- **Track B — Distribution platform adapter framework** (PlatformAdapter ABC, registry, three reference adapters: AEBN, ADE, SLR)
- **Track C — Throughput pipeline** (asyncio worker pool, session state cache, asset variant cache, perf budget CI guard)
- **Track D — Licensing intermediary lifecycle** (sublicense draft / review / execute / audit / renew / terminate state machine; chain-of-rights validator; Amy Lew beta gate L1 hookup)
- **Track E — Customer billing and reconciliation** (Stripe integration for `individual_subscriber`; invoice generation for `enterprise_b2b`; royalty statement reconciliation for `platform_revenue_share`; margin tracking for `licensing_intermediary`)
- **Track F — Documentation, ADRs, spec amendments** (Phase 4 ADRs 011-018; CLAUDE.md auto-update)
- **Track G — Operator-side prep** (Stripe account, AEBN/ADE/SLR credential procurement, Amy Lew formal beta-gate-L1 acknowledgment, perf budget operator-tuning session)

[Track-by-track paste-ready Cursor prompts to follow once Phase 3 Track B closes — pattern is identical]

---

## §6. Open questions (active — surface before lock)

- **Q4.1** — RESOLVED 2026-05-08: Stripe primary for individual_subscriber + enterprise_b2b inbound. AMG positions as SaaS-membership-service (not adult-content seller). Apple Pay + Google Pay + PayPal available natively. International coverage via Stripe's 135+ currencies + local payment methods. Backup processor (CCBill or Segpay) held cold in case Stripe underwriting terminates post-launch. Per L0.14.

- **Q4.2** — RESOLVED 2026-05-08: Tier 2 payouts use multi-method stack: Stripe Connect (Custom) primary for international Tier 2 + 1099 reporting automation; Wise Business backup for high-value international with cheaper FX; direct ACH for large US studios; USDC stablecoin optional for crypto-preferring Tier 2 customers; legacy paper check via Amy for studios with specific banking constraints. Per L0.14.
- **Q4.2** — Crypto-stablecoin acceptance for `catalog_buyer` and `licensing_intermediary` international flows. Lowers fees and avoids processor adult-content rejection risk. Operator preference: USDC on Polygon? Solana? Ethereum L1 (high fees)?
- **Q4.3** — Renewal automation default for `licensing_intermediary`: auto-renew vs operator-confirm-each-renewal. Auto-renew faster but loses pricing leverage at renewal time.
- **Q4.4** — Royalty dispute resolution flow: arbitration clause in contracts vs direct operator-driven negotiation. Affects `licensing_intermediary` and `platform_revenue_share` schemas.
- **Q4.5** — Beta cohort selection for L1 Amy Lew review: which licensing transactions are first to land. Recommend: 2-3 low-stakes domestic-territory deals with established trusted counterparties.

- **§14.6** — RESOLVED 2026-05-08: AMG individual_subscriber pricing band locked at premium positioning per L0.12. Basic $49.99/mo or $499/yr; Pro $99.99/mo or $999/yr. TSS comparison anchor: $34.95/mo, $349/yr.

---

## §7. Phase 4 ADRs to author

- ADR-011 — Five-type customer schema decision rationale
- ADR-012 — `licensing_intermediary` chain-of-rights validation rules
- ADR-013 — TSS-parity throughput targets and CI perf budget enforcement
- ADR-014 — Platform adapter pattern (asyncio + Playwright + per-method transport)
- ADR-015 — Asset variant pre-rendering and content-address caching
- ADR-016 — Stripe vs alt-processor payment infrastructure (resolves Q4.1)
- ADR-017 — Beta gate L1 Amy Lew legal review process
- ADR-018 — Vendor registry split (licensor vs licensee) rationale

---

## §8. Reporting — Phase 4 status surface

`docs/phase4_status.md` tracks track-by-track completion. Updated after each track closes. Mirrors Phase 3 status doc format.

Phase 4 closes when:
- All five transaction types have green CRUD + business-rule tests
- `licensing_intermediary` lifecycle state machine passes Amy Lew L1 review
- Throughput perf budget green for 100-site batch
- All Phase 4 ADRs land
- Operator signs Phase 4 → Phase 5 handoff in `docs/decisions/decision_register.md`

---

## §9. Closure Criteria (v1.1 addendum)

Phase 4 is considered closed only when all items below are complete:

1. All 7 tracks (A through G) complete.
2. All 7 ADRs (014-020) signed off by operator.
3. Stripe account live with 4 price IDs configured.
4. Amy Lew beta gate L1 acknowledgment recorded.
5. Per-platform deny lists operator-verified.
6. `perf/throughput_budget.yaml` operator-tuned post-30-day traffic.
7. Test posture: 328+ tests green; Ruff clean; mypy 130+ files clean.

If any criterion regresses after closure, Phase 4 status reopens to "at risk" until remediated.

---

**END OF DRAFT v1.1** — Track structure and closure criteria locked; throughput figures and final operator-side confirmations remain tracked in Phase 4 status and decision register.
