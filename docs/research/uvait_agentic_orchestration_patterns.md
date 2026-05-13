# UVAIT R5 — Agentic vision orchestration patterns (DR-045 working scope)

**Authority:** Research deliverable — external framework survey plus AMG-internal pattern mirroring where sourced.  
**Internal cross-references:** Numbered governance blocks (§5 authority discipline, §7 retractability, §11 multi-agent protocol, §16 cross-tool orchestration / “11 internal Cursor agents”, §17.7 append-only audit, §18 multi-band confidence, §19 versioned prompt registry, Pydantic v2 in §2 locked stack) appear in the sibling brief `~/AMG_OS_NEXT/CLAUDE.md`, not in root `AMG_OS/CLAUDE.md` (which documents the v11.x pipeline and operator constraints). **R1 IDV vendor schema docs:** not located under this repo scan — **internal doc TBD**.

---

## 1. Executive summary

High-stakes compliance vision is not a single prediction problem; it is a **chain of custody** problem linking pixels, model claims, policy bands, human disposition, and immutable audit. A monolithic “one big VLM in a SaaS box” optimizes vendor simplicity and latency at the expense of **calibration specificity**, **disagreement visibility**, and **operational retractability**. The thesis of UVAIT (unified vision orchestrator, DR-045) is that a **thin deterministic orchestrator** plus **specialized subworkers**—each with explicit JSON contracts, deadlines, and registry versioning—systematically outperforms single-model SaaS for compliance-grade vision because it separates concerns that a single endpoint necessarily collapses.

First, **risk is multi-dimensional**. Trademark exposure, minor cues, frame quality, perceptual-hash proximity, and face-match geometry do not share one latent space or one calibrated score; routing them through separate workers preserves **per-signal confidence bands** and avoids the false comfort of a single scalar “confidence.” That aligns with a multi-band threshold map (see internal §18 pattern in `AMG_OS_NEXT/CLAUDE.md`) rather than a unified “0.92 and ship.”

Second, **structure beats prose**. Tool use and structured outputs from major providers reduce format entropy, but compliance still needs an **application-level envelope** that records authority class per field (AUTHORITATIVE / REFERENCE / UNKNOWN-gap), model and prompt versions, and evidence pointers. Frameworks help with JSON conformance; they do not replace governance labels.

Third, **time-budgeted orchestration** mirrors mature media pipelines: hard deadlines, phase timeouts, skippable work past deadline, and **fallback cascades** when floors are not met (pattern from `amg/pipeline.py`: shared `deadline` from duration-scaled budget; `run_floor_enforcement_cascade` when `COVER_FLOOR` not satisfied). The analogue for UVAIT is not “more model,” it is **deterministic degradation** with explicit quality flags.

Fourth, **audit and retractability** are first-class. Append-only auditing (internal §17.7) and removable subworker registration (internal §7 “delete the feature folder + remove route registration” discipline, interpreted for vision plugins) make regressions **bisectable** and vendor pivots **mechanical**.

Claims (working scope):

- **C1.** Multi-agent / multi-tool patterns from Anthropic, OpenAI, LangGraph-class libraries, and application orchestrators improve reliability when each step has schema-bound I/O and deadlines—provided the app layer enforces policy bands, not the framework.
- **C2.** The closest commercial primitives to “JSON-only verdicts” are OpenAI Structured Outputs (strict JSON Schema), Anthropic strict tool use, and Pydantic v2 validators at the boundary; **none** ship UVAIT’s authority envelope or multi-band compliance semantics out of the box.
- **C3.** Vision compliance gains come from **heterogeneous ensembling** (VLM + classical CV + specialist embeddings / pHash) and **disagreement routing**, not from larger single VRAM footprints alone.
- **C4.** UVAIT’s moat is **cross-validation + structured verdict + immutable audit granularity**, not raw top-1 accuracy claims—numeric superiority should be demonstrated per domain with cited evaluation protocols, not asserted here.

---

## 2. Pattern catalog

Summary table:

| Framework / product | Core abstraction | Strengths for orchestration | Gaps vs UVAIT needs |
| ------------------- | ---------------- | --------------------------- | ------------------- |
| **Anthropic (Claude API)** | Tool use loop (client vs server tools), optional **strict** tool schemas | Strong agentic loop story; explicit tool result round-trips; schema strictness | No native authority bands; vision tool policy is usage/TOS constrained |
| **OpenAI** | Chat/Responses APIs, **function calling**, **Agents SDK**, Assistants (state + tools) | Structured Outputs (`json_schema` + `strict: true`) for faithful schemas; multi-agent cookbook patterns | Assistants lifecycle Adds complexity; still need app-level policy + audit |
| **LangGraph** | Graph/state machine over messages/tools | Branching, persistence, human-in-the-loop nodes are first-class | Graph complexity; still need schema governance external to graph |
| **LlamaIndex** | Agents + tool wrappers + workflows | Fast to wire RAG/tools; workflow abstractions | Less opinionated on compliance audit / multi-band routing |
| **AutoGen** | Multi-agent conversations / group chat | Explores role separation and group dynamics | Heavier runtime; discipline needed to avoid unconstrained natural-language exits |
| **CrewAI** | Role-based crews and task decomposition | Clear metaphor for worker specialization | Crew semantics ≠ compliance audit primitives |
| **Microsoft Semantic Kernel** | Plugins + planners + connectors in .NET/Python | Enterprise integration; planner patterns | Planner outputs need the same JSON envelope discipline |
| **Haystack** | Pipelines, nodes, Agents 2.x | Document/video pipeline thinking; composable nodes | Vision compliance specifics are user-land |
| **DSPy** | Declarative LM programs / optimizers | Strong when tasks are text/program optimizations | Vision + policy routing is indirect |
| **Cursor IDE** | Interactive agent, rules/skills, MCP, background/cloud agents | Excellent for engineering velocity; isolated agent branches/VMs for long tasks | Product is editor-centric, not a compliance verdict engine |

### 2.1 Anthropic — tool use, agentic workflows, parallelism, structured outputs

- **Tool use:** Claude emits `tool_use` blocks; the application executes tools and returns `tool_result`. Server-side tools (e.g., web_search) execute on Anthropic infrastructure. See: [Tool use with Claude](https://docs.anthropic.com/en/docs/build-with-claude/tool-use) and [How tool use works](https://docs.anthropic.com/docs/en/agents-and-tools/tool-use/how-tool-use-works).
- **Strict schemas:** `strict: true` on tool definitions constrains inputs to match schema. See: [Strict tool use](https://docs.anthropic.com/docs/en/agents-and-tools/tool-use/strict-tool-use).
- **Parallelism:** The API can return several `tool_use` blocks in one turn; the host may execute them concurrently when side-effect free—or serialize when ordering matters (document concurrency rules in UVAIT per tool contract).

### 2.2 OpenAI — Agents SDK, Assistants, function calling, Structured Outputs

- **Structured Outputs:** Guarantees model output conforms to supplied JSON Schema; `strict: true` recommended when supported. Distinct from JSON mode (valid JSON but not schema-faithful). See: [Structured model outputs](https://platform.openai.com/docs/guides/structured-outputs).
- **Function calling vs response schema:** Functions bridge to application capabilities; `response_format` / `text.format` structures end-user-visible answers. See same guide, “When to use Structured Outputs via function calling vs via text.format.”
- **Agents / multi-agent:** OpenAI documents multi-agent patterns using structured outputs; treat as architectural guidance, not a compliance framework. See: [Multi-agent structured outputs (cookbook)](https://developers.openai.com/cookbook/examples/structured_outputs_multi_agent).
- **Assistants API:** Thread/run state plus tool definitions; useful for long sessions but introduces **statefulness** UVAIT must map to append-only audit events.

### 2.3 LangGraph — state machines, branching, routing

LangGraph models orchestration as a graph with **state**, **nodes**, and **conditional edges**—a strong fit for UVAIT’s explicit gates (e.g., trademark band vs quality band disagreements). See: [LangGraph concepts](https://langchain-ai.github.io/langgraph/concepts/) (LangChain OSS site).

### 2.4 LlamaIndex agents

LlamaIndex provides **agents** that combine LLM reasoning with tools over indexes. Good for retrieval-augmented tool loops; compliance-specific audit fields remain application-defined. See: [LlamaIndex agents documentation](https://docs.llamaindex.ai/en/stable/module_guides/deploying/agents/).

### 2.5 AutoGen — multi-agent, group chat

AutoGen coordinates multiple agents with conversational patterns (including group chat). Useful for exploration; production compliance requires **hard schema exits** from chatty loops. See: [AutoGen documentation](https://microsoft.github.io/autogen/stable/).

### 2.6 CrewAI — role-based crews

CrewAI emphasizes **roles**, **tasks**, and **crews** for decomposing work—analogous to UVAIT subworkers but without built-in policy bands. See: [CrewAI docs](https://docs.crewai.com/).

### 2.7 Microsoft Semantic Kernel

Semantic Kernel frames LLM features as **plugins** combined with planners. Maps cleanly to a registry of subworkers if UVAIT standardizes plugin manifests. See: [Semantic Kernel overview](https://learn.microsoft.com/en-us/semantic-kernel/overview/).

### 2.8 Haystack

Haystack pipelines connect retrievers, readers, and generators; Agents 2.x adds agentic flows over those nodes. See: [Haystack documentation 🐉](https://docs.haystack.deepset.ai/docs/intro).

### 2.9 DSPy — declarative LM programs

DSPy treats prompts/programs as optimizable artifacts—synergistic with **versioned prompt registries** (internal §19 pattern) but not a substitute for vision+cross-check orchestration. See: [DSPy repository and docs](https://github.com/stanfordnlp/dspy).

### 2.10 Cursor IDE — multi-agent architecture (documented vs inferential)

**Documented (public):** Cursor provides an **Agent** integrated with the editor, **Rules** and **Skills** for persistent guidance, **MCP** for external tools, CLI flows, and **Background / Cloud agents** that run asynchronously against a cloned repository (often on isolated infrastructure) with PR-oriented workflows. See: [Cursor docs — Background Agent](https://docs.cursor.com/background-agent) and related Agent documentation at [docs.cursor.com](https://docs.cursor.com/).

**Product metaphor:** Multiple specialized agents (e.g., explore vs implementation vs review) coordinate via user-directed tasking and repository context—not via a single shared “compliance verdict” schema.

**Inferential (not official product spec):** Exact internal agent topology, scheduling between agents, and enterprise isolation guarantees evolve release-to-release; UVAIT should not couple its architecture to undocumented Cursor internals.

**Project-specific (governance brief, not Cursor Inc.):** `AMG_OS_NEXT/CLAUDE.md` §16 states AMG_OS_NEXT uses **three external AI tools plus 11 internal Cursor agents** with role separation—this is **operator project governance**, not an assertion about Cursor’s corporate architecture.

---

## 3. Vision-specific orchestration

### 3.1 Multi-stage VLM pipeline

Recommended stage chain (often parallelizable where read-only):

1. **Detect / localize** — regions of interest, faces, logos, text boxes.
2. **Classify** — policy categories (e.g., trademark presence class, scene type).
3. **Score / calibrate** — map to policy bands (never a lone scalar across policies).
4. **Report** — emit envelope + evidence links + worker versions.

Each stage should be a **pure function of inputs + config** with explicit timeouts.

### 3.2 VLM + classical CV + media stack

- **OpenCV / classical metrics** — sharpness, exposure, motion blur proxies; cheap gates before VLM spend.
- **MediaPipe** — face mesh / pose for geometry-heavy checks when policy warrants.
- **ffmpeg** — deterministic decode, keyframe extraction, uniform temporal sampling; pairs with duration-scaled budgets (as in `amg/pipeline.py`).

### 3.3 VLM + specialist ensemble

Combine outputs from:

- **Face embeddings** (e.g., ArcFace / InsightFace ecosystem; evaluation caveats apply across demographics and compression).
- **pHash / dHash** — intentional low-level perceptual matching with Hamming-band routing (mirrors internal §18 pHash band example in `AMG_OS_NEXT/CLAUDE.md`).
- **Scene / aesthetic scoring** — only as **one band among many**, not the verdict.

### 3.4 Confidence calibration, abstention, HITL

Align with **multi-band routing**: high / medium / low (and UNUSABLE) per **decision type**, not one global threshold. Disagreement examples called out in DR-045 framing:

- **Trademark high confidence** vs **frame quality blur** → forced human route because policies address different harms.
- **Abstention** is a **positive outcome** when it triggers review trays rather than silent auto-pass.

Internal reference pattern: §18 table in `AMG_OS_NEXT/CLAUDE.md` (trademark bands, frame quality bands, QAAS-CF tiers).

---

## 4. Structured-output contracts: frameworks → UVAIT envelope

| Mechanism | What it guarantees | Where it fits UVAIT |
| --------- | ------------------ | ------------------- |
| OpenAI Structured Outputs + `strict: true` | Output matches JSON Schema subset | Final LLM steps that must parse cleanly |
| Anthropic tool `input_schema` + `strict: true` | Tool call arguments match schema | Subworker invocations driven by Claude |
| Pydantic v2 | Runtime validation, coercion policy, custom validators | Ingress/egress boundary for every worker |
| JSON Schema in CI | Drift detection vs types | Mirror internal prompt-registry linter discipline (§19) |

### 4.1 Mapping §5 authority discipline

Per `AMG_OS_NEXT/CLAUDE.md` §5:

- **AUTHORITATIVE** — primary-source extracted with `# evidence:` citation.
- **REFERENCE** — informs intent; never becomes procedural rule in automation.
- **DEPRECATED** — legacy routing only.
- **UNKNOWN — gap** — queued; never fabricated.

UVAIT should emit these as **explicit enums on fields or field-groups**, not as adjectives in prose.

### 4.2 §18 — multi-band confidence (never a single scalar)

Represent confidence as **a struct per decision axis**, e.g. `trademark`, `frame_quality`, `minor_indicator`, `phash_proximity`, each with `{ "band": "...", "score": ..., "policy_version": "..." }` where `score` is permitted but **not sufficient** for routing—**band** + **policy_version** are authoritative for automation.

### 4.3 Recommended canonical UVAIT JSON envelope (field rationale)

| Field | Rationale |
| ----- | --------- |
| `schema_id` / `schema_version` | Registry evolution without silent breaking changes |
| `request_id`, `asset_id` | Correlation across workers and human review |
| `verdict` | Small enum: e.g., `AUTO_PASS`, `AUTO_BLOCK`, `REVIEW_TRAY`, `ABSTAIN` — machine routing only |
| `authority` | AUTHORITATIVE \| REFERENCE \| UNKNOWN_GAP per §5 |
| `signals[]` | One object per subworker output before fusion |
| `fusion` | How verdict was derived (rules version, voting, cascade step) |
| `confidence` | **Object** keyed by decision type (§18); forbid bare float at top level |
| `evidence[]` | Pointers (frame index, bbox, hash, crop URL/path) |
| `models` | model id + weight revision |
| `prompts` | prompt id + semver (§19 parallelism) |
| `audit` | append-only event ids, parent run id |
| `retraction` | nullable struct: which registry entries were disabled for this recompute |

**Pydantic v2 sketch:**

```python
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Authority(str, Enum):
    AUTHORITATIVE = "AUTHORITATIVE"
    REFERENCE = "REFERENCE"
    DEPRECATED = "DEPRECATED"
    UNKNOWN_GAP = "UNKNOWN_GAP"


class Verdict(str, Enum):
    AUTO_PASS = "AUTO_PASS"
    AUTO_BLOCK = "AUTO_BLOCK"
    REVIEW_TRAY = "REVIEW_TRAY"
    ABSTAIN = "ABSTAIN"


class AxisConfidence(BaseModel):
    """Per-axis confidence — band is routing-authoritative; score is diagnostic."""

    axis: str = Field(..., description="e.g. trademark, frame_quality, phash")
    band: str
    score: Optional[float] = None
    policy_version: str


class SignalPayload(BaseModel):
    worker_id: str
    worker_version: str
    tool_schema_id: Optional[str] = None
    payload: Dict[str, Any]
    authority: Authority = Authority.AUTHORITATIVE


class UvaitEnvelope(BaseModel):
    schema_id: str = "uvait.envelope"
    schema_version: str = "0.1.0"
    request_id: str
    asset_id: str
    verdict: Verdict
    authority: Authority
    signals: List[SignalPayload] = Field(default_factory=list)
    confidence: Dict[str, AxisConfidence] = Field(
        ...,
        description="At least one axis; never collapse to a single scalar at root.",
    )
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    models: Dict[str, str] = Field(default_factory=dict)
    prompts: Dict[str, str] = Field(default_factory=dict)
    fusion: Dict[str, Any] = Field(default_factory=dict)
    audit: Dict[str, Any] = Field(default_factory=dict)
    retraction: Optional[Dict[str, Any]] = None
```

**Equivalent JSON Schema fragment (illustrative core):**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://amg.local/schemas/uvait.envelope-0.1.0.json",
  "type": "object",
  "required": [
    "schema_id",
    "schema_version",
    "request_id",
    "asset_id",
    "verdict",
    "authority",
    "confidence"
  ],
  "properties": {
    "schema_id": { "const": "uvait.envelope" },
    "schema_version": { "type": "string" },
    "request_id": { "type": "string" },
    "asset_id": { "type": "string" },
    "verdict": {
      "type": "string",
      "enum": ["AUTO_PASS", "AUTO_BLOCK", "REVIEW_TRAY", "ABSTAIN"]
    },
    "authority": {
      "type": "string",
      "enum": [
        "AUTHORITATIVE",
        "REFERENCE",
        "DEPRECATED",
        "UNKNOWN_GAP"
      ]
    },
    "confidence": {
      "type": "object",
      "minProperties": 1,
      "additionalProperties": {
        "type": "object",
        "required": ["axis", "band", "policy_version"],
        "properties": {
          "axis": { "type": "string" },
          "band": { "type": "string" },
          "score": { "type": ["number", "null"] },
          "policy_version": { "type": "string" }
        },
        "additionalProperties": false
      }
    },
    "signals": {
      "type": "array",
      "items": { "type": "object" }
    }
  },
  "additionalProperties": true
}
```

---

## 5. Plugin / subworker registration

### 5.1 Retractability (§7 pattern)

`AMG_OS_NEXT/CLAUDE.md` §7 requires **feature-level retractability**: delete feature folder + remove route registration; no broad refactors. For UVAIT, map each vision subworker to:

- A **registered plugin id** (mirrors §19 “every prompt is a file” discipline → every worker manifest is a file).
- A **router table** entry removable in one edit.
- Tests that fail loudly if a worker id is orphaned.

### 5.2 Parallel `tools/uvait_registry` (mirror §19)

Mechanically parallel **versioned registry** artifacts:

- `worker_id.version.yaml` manifests (inputs, outputs schema id, latency class, side effects).
- CI linter: no inline prompt strings; no unregistered workers in hot paths.
- **Deprecation:** `deprecated_by` link; retain old versions for reproducibility (§19.3 pattern).

### 5.3 Dynamic vs static registration

- **Static (recommended default):** workers known at deploy; safest for audit and determinism.
- **Dynamic (controlled):** allow load at runtime only with **operator-signed manifest** + append-only `worker_loaded` audit events.

---

## 6. Performance + reliability

### 6.1 Concurrent subworkers + deadlines

`amg/pipeline.py` pattern: compute `hard_budget` from `min(duration_sec * TIME_BUDGET_HARD_PCT, TIME_BUDGET_ABSOLUTE_MAX)`, derive `deadline`, pass `deadline_sec` into scanning phases; phases abort early when over budget; optional parallel AI scoring via executor (see `CLAUDE.md` invariant on `AI_PARALLEL_WORKERS`).

UVAIT should adopt:

- **Global deadline** + **per-worker soft/hard timeouts**.
- **Partial results** with explicit `aborted: true` flags in envelope.

### 6.2 Retry / fallback cascade

`run_floor_enforcement_cascade` embodies **tiered degradation** until a floor is met. DR-045 narrative references **QAAS-CF** vocabulary (PRIMARY / RELAXED / FALLBACK / BEST_AVAILABLE / UNUSABLE) aligned with ADR-039 in `AMG_OS_NEXT` revision history—not fully duplicated here. Treat cascade labels as **risk bands**, not aesthetic preferences.

### 6.3 Audit every verdict (§17.7 pattern)

Map each final routing decision to an **append-only** event carrying worker ids, schema versions, and hashes of inputs where feasible. Database enforcement (revoke UPDATE/DELETE on audit tables) is internal infra guidance from `AMG_OS_NEXT/CLAUDE.md` §17.7.

### 6.4 Disagreement detection

Implement explicit **cross-signal contradiction rules** (e.g., high trademark confidence + low legibility / heavy blur → REVIEW_TRAY). Log **both** signals verbatim to preserve human triage.

---

## 7. Competitive questions (answered)

**Which frameworks come closest to “structured JSON only, multi-band confidence, no single scalar”?**

- **Closest primitives:** OpenAI Structured Outputs + Pydantic at the boundary; Anthropic strict tool use for intermediate steps; LangGraph for routing transparency.
- **Where they fall short:** None encode **authority discipline**, **append-only audit law**, or **per-axis policy bands** as first-class concepts—these are **application moat**.

**Canonical envelope vs future R1 vendor schemas**

- Until **R1 IDV vendor schema** artifacts are checked in (“internal doc TBD”), treat the UVAIT envelope as **vendor-agnostic**.
- **Interoperability strategy:** map `signals[]` entries to vendor fields bidirectionally; keep UVAIT `verdict`/`confidence` authoritative internally, export vendor-specific projections as **REFERENCE** authority unless sourced from vendor primary docs.

---

## 8. Tradeoff analysis — concurrent vs sequential subworkers

| Dimension | Concurrent | Sequential |
| --------- | ---------- | ---------- |
| Latency | Lower wall-clock when workers independent | Higher but simpler reasoning |
| Cost | Bursts GPU/API spend | Smoother spend |
| Determinism | Needs reproducible tie-breaks | Easier logs |
| Safety | Race conditions on shared mutable state | Safer defaults |
| Debugging | Harder causal traces | Easier |

**Rule of thumb:** parallelize **read-only(feature extraction)**; **serialize** steps that mutate shared policy state or write customer-visible artifacts.

---

## 9. Production disagreement routing

Patterns:

1. **Rule-based contradiction templates** (fast, auditable).
2. **Second-stage arbiter** only when templates insufficient—still schema-bound.
3. **Human tray** with **minimum context package** (conflicting signals + crops).
4. **Never auto-resolve** silent tie-breaks for Gate-1-class harms (internal §18 shows hardstop examples for certain detections).

Telemetry to capture: worker latency, refusal flags (OpenAI `refusal` field pattern), parse failures, schema drift counts.

---

## 10. Gap analysis / UVAIT moat

| Dimension | SaaS single-model | UVAIT orchestrator + workers |
| --------- | ----------------- | ---------------------------- |
| Calibration | Often one score | Per-axis bands + policy versions |
| False positives | Opaque tradeoffs | Cross-checks + explicit disagreement routing |
| Audit | Vendor log | Append-only, per-signal manifests |
| Retractability | Service-level only | Per-worker registry + route removal |
| Evidence | Endpoint output | Bounding evidence + hashes |

**Careful wording:** quantitative false-positive reductions require **domain-specific benchmarks** and **operational definitions** (e.g., NIST-style face PAD metrics, internal trademark ROC studies). This document avoids unverified percentages.

---

## References (external)

- Anthropic — Tool use: https://docs.anthropic.com/en/docs/build-with-claude/tool-use  
- Anthropic — Strict tool use: https://docs.anthropic.com/docs/en/agents-and-tools/tool-use/strict-tool-use  
- OpenAI — Structured outputs: https://platform.openai.com/docs/guides/structured-outputs  
- OpenAI — Multi-agent cookbook example: https://developers.openai.com/cookbook/examples/structured_outputs_multi_agent  
- LangGraph — Concepts: https://langchain-ai.github.io/langgraph/concepts/  
- LlamaIndex — Agents: https://docs.llamaindex.ai/en/stable/module_guides/deploying/agents/  
- AutoGen: https://microsoft.github.io/autogen/stable/  
- CrewAI: https://docs.crewai.com/  
- Microsoft Semantic Kernel: https://learn.microsoft.com/en-us/semantic-kernel/overview/  
- Haystack: https://docs.haystack.deepset.ai/docs/intro  
- DSPy: https://github.com/stanfordnlp/dspy  
- Cursor — Background Agent: https://docs.cursor.com/background-agent  

---

## References (internal, cited paths)

- `~/AMG_OS_NEXT/CLAUDE.md` — authority (§5), UI retractability pattern (§7), multi-agent protocol (§11), cross-tool orchestration / internal agent count (§16), AI-safe infra + append-only audit (§17), confidence threshold map (§18), versioned prompt registry (§19), Pydantic v2 lock (§2 stack list).  
- `amg/pipeline.py` (`~/AMG_OS/amg/pipeline.py`) — time-budgeted deadline, phased orchestration, cover floor + fallback cascade, audit hook.  
- `~/AMG_OS/CLAUDE.md` — pipeline architecture invariants (time budget, parallel scoring caveat, floor cascade).
