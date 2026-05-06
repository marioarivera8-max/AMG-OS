# Session notes — 2026-05-06

This file captures conversational context from the 2026-05-06 session
that the commit log + AGENT_CONTEXT_2026-05-06_CLOUD_EDITION.md don't
fully capture: the rejected proposals, the misdiagnoses, the ordering
of decisions, the "why now" framing.

## Session arc

The session started where 2026-05-04 left off (local-first training/scoring
work) but pivoted hard at the operator's request: **"I need a 2x increase
in efficiency, give me options for things like running this on a cloud
server."**

By the end of the session, AMG OS had been transformed from a local Mac
pipeline into a fully cloud-hosted edition accessible at
`https://amg.exoticplug.app`. Mario verified the full pipeline ran
end-to-end on a Runpod 4090 against a real GDrive scene.

## The decision arc (in order)

### 1. Why cloud at all

The critique was framed as: M4 Pro qwen2.5vl:7b inference is the bottleneck.
We considered several alternatives:

- **Bigger Mac (M4 Max / M4 Ultra):** Would help inference but doesn't
  scale beyond one operator and needs Mario to physically be in front of
  it. Rejected for the latter reason — Amy and contractors will need
  access within ~6 months.
- **Smaller / quantized model:** The 2026-05-04 bake-off already showed
  qwen2.5vl:3b was within 3% of qwen2.5vl:7b on wall time (decode is the
  driver, not parameter count). So a smaller model wouldn't move the needle.
- **Cloud GPU (Runpod / Lambda / Vast):** Real 2-3x inference speedup on
  RTX 4090 over M4 Pro. This is what got chosen.

Mario picked **Runpod** because of:
- Predictable pricing ($0.69/hr for 4090 community cloud)
- Pay-per-second billing
- Easy on-demand provisioning via REST API
- No long-term contracts

### 2. Why a controller VM (not just "log directly into Runpod")

The naive flow is "one VM that's always on plus pods that spin up". The
better flow turned out to be:

- **Controller (Hetzner CPX21, ~$5/mo):** Always on, holds the SQLite
  databases (users + encrypted rclone configs), serves the FastAPI UI,
  orchestrates pod lifecycle, holds rclone configs, terminates idle pods.
- **Pods (Runpod, $0.69/hr ON DEMAND):** Spin up only when there's a
  scene to process. Controller terminates them when done.

This means the operator can drop in any browser, log in, queue a job,
walk away, and come back to covers — without any VM sitting idle at
GPU prices.

### 3. Why GHA + GHCR (rejected: local Docker build)

We initially planned for Mario to `docker build` the images locally and
push to a registry. Three problems:

1. Mario doesn't have Docker Desktop installed and didn't want to install it.
2. Mac M4 builds linux/arm64 by default; Hetzner + Runpod are linux/amd64.
   `docker buildx --platform linux/amd64` works but the cross-build is slow.
3. The whole point was to make the codebase deployable from any machine,
   not to require Mario's specific Mac setup.

Solution: **GitHub Actions builds both images on every push to main** and
publishes to GHCR. Mario only needs to commit + push; deployment is
automatic.

A separate question came up: **is it OK to publish the AMG image to GHCR
since it processes adult content?** The answer was yes, because:

- The images contain only the **pipeline code** (Python + ffmpeg + PyAV
  + a vision-model puller). No actual adult media is embedded.
- Even adult-themed prompts in the code aren't user-facing media, they're
  developer code. GitHub TOS allows that.
- The actual videos and covers stay on Mario's GDrive / on the controller's
  ephemeral storage / on his local disk. Nothing adult is published.

GHCR packages were made public so Hetzner and Runpod can pull without
needing a PAT.

### 4. Why HTMX, not React (re-confirmed)

This was already decided in the v11.3 plan. Re-confirmed because the
shadcn agent had been rolled in for some UI polish and could plausibly
have proposed React/Next/etc. The locked answer is HTMX + FastAPI +
Jinja2; shadcn-style components are fine as long as they're plain HTML
+ CSS + small bits of JS.

### 5. Why a single-tenant controller (not multi-tenant from day one)

Operator wants Amy + 1-2 contractors within ~6 months. We built the
**auth scaffolding** for it (`amg user add`, role flags, session cookies)
but didn't actually add per-user job filtering, audit logging, or
role-gated routes. Reasoning: those are easy to bolt on once there are
real users with real complaints, and over-building auth before there
are real users invites bugs and confusion.

## Misdiagnoses + recoveries

### "The Process button is broken"

Mario clicked Process and **nothing happened — no UI feedback, no error,
no log line on the controller**. Spent ~15 minutes on this thinking it
was a backend issue. The actual cause was an HTMX `hx-target` selector
miss: the form pointed at `#process-jobs-panel` which exists on `/` but
not on `/cloud`. HTMX 1.9.12 raises `htmx:targetError` and **aborts
the request entirely** when the target isn't found. So the POST never
left the browser.

Lesson: when an HTMX-driven UI is dead-silent on click, suspect
`hx-target` mismatch FIRST. Open browser devtools → Network tab and
confirm the request actually fires before debugging server-side.

Also fixed in the same commit: hover states + `hx-indicator` on the
folder rows so the picker feels alive while GDrive is responding.
Without it, every click takes 1-3 seconds and the UI feels broken.

### "Pod is stuck — controller times out after 10 minutes"

The controller log was `RUNNING; waiting for pod-worker /healthz`,
poll, poll, poll, then timeout. Spent ~20 minutes ruling out network /
firewall / image issues before realizing the wait was happening at the
**Runpod API level**, not the `/healthz` level. The bug was:
`wait_until_running` was looping waiting for `portMappings` to populate,
but Runpod's new REST API never populates that field — only the old
GraphQL API did. The pod was healthy and answering `/healthz` the entire
time; the controller just never proceeded to the `/healthz` polling
phase.

Lesson: when adopting a new API version of a service, audit any code
that depends on specific response field shapes. Don't trust the old
shape carries forward.

Fix: `provision_pod` now passes `require_port_mappings=False`. Real
readiness is a behavioral signal (`/healthz` 200 OK), not a metadata
signal (a field appears in the API response). Behavioral readiness
checks are more robust than metadata readiness checks.

### "Artifact pull hung — pipeline finished but covers never came back"

The pod logs showed `success=True covers_saved=12` and Mario could see
a perfectly good `out/` folder by SSHing into the pod. But the
controller's "pulling artifacts" step ran for >60 seconds before timing
out. After enough digging:

`_stream_dir_as_zip` was zipping the **entire** `work_dir`, which the
pod-worker had set to `/data/pod_uploads/<job>/<scene>/` — and that
directory still contained the 2.84 GB source video. The zip was being
built into a `BytesIO` in memory before the first byte streamed out.
On a 2.84 GB input, that's 30-60 seconds just for the in-memory build.

Fix: after `process_scene` returns, narrow the tracker's `work_dir` to
`result["work_dir"]` (the much smaller `out/` folder containing only
covers + decision log + contact sheet). New zip size: ~5-30 MB. New
zip time: <1 second.

Lesson: when streaming large directories, what gets included matters as
much as the streaming method. The pipeline's notion of `work_dir`
(input + output) and the API's notion of `work_dir` (just output) are
different and need explicit narrowing.

### "Pod creation immediately fails with HTTP 500"

After all the above bugs were fixed, the next click on Process returned
`HTTP 500: "could not find any pods with required specifications"` instantly.
The cause: Mario had created a 20 GB Runpod network volume in `US-NE-1`
to cache the Ollama model and skip the 5-min cold-pull. We plugged
`AMG_RUNPOD_NETWORK_VOLUME_ID=higomno9lt` into the controller env. The
volume **pinned the pod to US-NE-1**, but US-NE-1 had zero 4090 / A6000
/ L40 capacity at the moment.

Fix (temporary): blanked `AMG_RUNPOD_NETWORK_VOLUME_ID`. Pod provisioning
now lets Runpod pick any DC where capacity is hot. Cost: every cold boot
re-pulls the 5 GB model.

Fix (permanent, deferred): delete the US-NE-1 volume, recreate in US-CA-2
or US-GA-1 where 4090 capacity is consistent. Documented in
AGENT_CONTEXT_2026-05-06_CLOUD_EDITION.md.

Lesson: Runpod network volumes are DC-pinned and DC-pinned means
GPU-capacity-pinned. Don't create a volume until you know which DC has
reliable inventory of the GPU class you want.

## What got rejected without a long debate

- **OAuth flows for GDrive / Dropbox.** Originally Phase 2 had separate
  OAuth implementations for each cloud. Rejected because rclone already
  handles all of them and the operator has a working rclone config on
  the Mac. We collapsed it down to: operator runs `rclone authorize <kind>`,
  pastes the resulting config into `amg cloud-remote add`, encrypted
  credential store handles the rest. Way less code, way less surface area
  for security bugs, supports any rclone backend not just three.

- **Postgres on the controller.** Considered but rejected — SQLite is
  fine for single-controller, low-write workload (one operator, ~10
  jobs/day, ~1000 jobs/year). Hetzner snapshots back up the entire
  filesystem hourly which covers the "what if SQLite is corrupted"
  scenario.

- **Always-on pod with idle-timer teardown.** Original Runpod plan was
  "one pod always running, terminate after N min idle". Rejected for
  v1 because it's harder to reason about (when does the timer start?
  what if a job comes in at second 59?) and less cost-predictable.
  Current model is simpler: provision-on-job, terminate-on-completion.
  The tradeoff is paying cold-boot per job. **Pod reuse is on the
  TOMORROW list** as the natural next architectural step once the simple
  model is stable.

- **Real-time progress updates from pod → controller.** The pod publishes
  progress through `tracker.append_log` which the controller polls every
  2 seconds. Considered websockets for real-time but the polling-with-
  HTMX-trigger approach is good enough for a single operator and stays
  consistent with the v11.x UI's already-established polling model.

## What Mario has noticed and we didn't address

He mentioned in the working session: "everything so far is working good,
a little slow and sudden sharp cuts but so far so good."

That comment is a **flag for cover-quality regression** — "sudden sharp
cuts" might be an artifact of frame-extraction timing or scoring on the
pod that doesn't match what the Mac was producing. We didn't dig because
he said "let's keep going" and the pivot to Hetzner came next. **The next
agent should inspect the covers from a successful run and ask Mario
specifically about the "sudden sharp cuts" comment.**

This is the kind of thing CLAUDE.md warns about: "covers that score 8.5
but look bad are a regression." The pod is producing the right *count*
of covers but Mario's eye hasn't validated the *quality* yet.

## Lessons (additions to the running list in TOMORROW.md)

- **Behavioral readiness > metadata readiness.** When waiting on a service
  to be ready, prefer `GET /healthz → 200` over "field X appears in
  metadata."
- **HTMX `hx-target` misses fail silently.** Always wire `hx-indicator`
  for visible feedback, even when the action is fast.
- **Zip-streaming is RAM-bounded.** `BytesIO` builds the entire archive
  before streaming starts; for >100 MB content, use a `tempfile`
  spool or stream-as-you-go.
- **Runpod network volumes are GPU-capacity-pinned.** Don't create a
  volume until you've confirmed the DC has reliable inventory of the
  GPU class you want.
- **OAuth flows for cloud storage are usually unnecessary.** rclone
  already handled the hard part; just lift its config.
- **Operator's eye is still the only quality metric that ships.** Even
  on the cloud edition. "success=True covers_saved=12" doesn't mean the
  covers are good. Mario's "sudden sharp cuts" comment proved it.
