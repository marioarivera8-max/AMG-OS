# AMG OS — Cloud Edition: Agent Context

> **Archive note (2026-05-08):** read `AGENT_CONTEXT_CURRENT.md` first. This
> file remains valuable for cloud-cutover history and recovery details, but its
> "next step" and image/status notes may be stale.

**Updated:** 2026-05-06 (mid-day, after ~12h pivot to cloud-hosted edition)
**Supersedes:** `AGENT_CONTEXT_2026-05-04_LATEST.md` (which is about local-only
training/scoring work; that work is paused but valid).

---

## TL;DR for the next agent

We pivoted from "local-only Mac pipeline" to a **fully cloud-hosted** AMG OS
that the operator can drive from any browser. The whole stack is live except
for one optional optimization (Runpod network volume in the wrong DC) and the
final cover-quality smoke test.

**Live URL:** [https://amg.exoticplug.app](https://amg.exoticplug.app)
**Login:** `mario` / *(in Mario's 1Password)*
**Stack:**
- **Controller VM:** Hetzner CPX21 in Ashburn, VA — `5.161.231.249`
- **GPU pods:** Runpod RTX 4090 (on-demand, ~$0.69/hr), spun up per job
- **Cloud storage:** rclone pulling videos directly from Google Drive into the pod
- **Domain:** `amg.exoticplug.app` (Cloudflare DNS, DNS-only mode = grey cloud)
- **Images:** `ghcr.io/marioarivera8-max/amg-{controller,pod}:latest` (public)

**The previous v11.x local pipeline is unchanged.** The cloud edition wraps
`amg.pipeline.process_scene` rather than rewriting it; you can still
`amg process <video>` locally if you have the venv set up.

---

## What's known to work end-to-end (verified live this session)

1. **Login → dashboard → cloud picker → drill GDrive → click Process** ✓
2. **Controller calls Runpod API, provisions pod with bundled Ollama** ✓
3. **Pod cold-boots:** Ollama starts → `ollama pull qwen2.5vl:7b` → uvicorn ready ✓
4. **Controller polls `/healthz` over Runpod proxy URL until ready** ✓
5. **Controller submits job (POST `/jobs/cloud`) with rclone config in body** ✓
6. **Pod downloads from GDrive via rclone (~30-60 MB/s)** ✓
7. **Pipeline runs:** `process_scene` → `success=True, covers_saved=12` ✓
8. **Controller pulls artifacts back as zip (just covers, not source video)** ✓ *(fix shipped 2026-05-06; previously hung)*
9. **Pod terminated, billing stops** ✓

The only remaining smoke test: actually look at the covers and confirm
they're the v11.1-quality baseline Mario expects.

---

## The bug graveyard from this session (in chronological order)

These all got fixed and shipped. Listing here so the next agent doesn't
chase them again.

### 1. `index.html` referenced `_process_queues.html` that wasn't committed

Commit `3ca296b` from a prior session added an
`{% include "_process_queues.html" %}` directive but forgot to `git add`
the partial. Worked locally because the file existed in Mario's working
tree; deployed image crashed with `TemplateNotFound` on every `GET /`.

**Fixed in:** `da77d10` — added `_process_queues.html` to the repo.

### 2. Process button silently aborted

The form had `hx-target="#process-jobs-panel"`, an ID that exists on `/`
but not on `/cloud`. HTMX 1.9.12 fires `htmx:targetError` and **aborts
the request entirely** when the target selector misses. Result: clicking
Process did absolutely nothing — no POST ever reached the controller.

**Fixed in:** `601eb36` — `hx-target="this"` + `hx-swap="none"`. The
route returns `HX-Redirect: /?job_id=…` so HTMX navigates anyway.

Same commit added folder hover states + `hx-indicator` for click feedback
(GDrive listing takes 1-3s per click and the dead UI was making the app
feel broken).

### 3. `wait_until_running()` looped on a phantom field

Old GraphQL Runpod API had `portMappings` populated in the pod state; the
new REST `/v1/pods` API never populates it (live observation:
`portMappings` is null/absent for the pod's whole lifetime, only `ports`
is present). `provision_pod()` defaulted to `require_port_mappings=True`
so it waited 10 minutes then timed out — meanwhile the pod was healthy
and answering `/healthz` over the proxy URL the entire time.

**Fixed in:** `5fcf99f` — `provision_pod` now passes
`require_port_mappings=False`. Real readiness check is the `/healthz`
poll, which is a behavioral signal not a metadata signal.

Regression test: `tests/test_cloud_runpod.py::TestProvisionPod::test_provision_returns_when_running_even_without_port_mappings`.

### 4. Artifact zip endpoint hung because work_dir included the source video

The pod's pipeline runs against `/data/pod_uploads/<job>/<scene>/`
which contains both the multi-GB source video AND the small `out/`
folder with covers. `_stream_dir_as_zip` builds the entire archive into
a `BytesIO` before yielding the first byte — for a 2.84 GB MOV that took
multiple minutes, longer than the controller's HTTP read timeout.

**Fixed in:** `f55b401` — after `process_scene` returns, narrow the
tracker's `work_dir` to `result["work_dir"]` (the small `out/` folder).
Zip is now ~5-30 MB and streams in <1s.

Regression tests:
`tests/test_pod_worker.py::TestJobs::test_work_dir_repointed_to_pipeline_output_after_success`
`tests/test_pod_worker.py::TestJobs::test_work_dir_unchanged_if_pipeline_omits_work_dir`

### 5. Ollama model not actually getting cached on the network volume

`Dockerfile.pod` had `OLLAMA_MODELS=/data/ollama`, but Runpod attaches
network volumes at `/workspace`, not `/data`. So even when
`AMG_RUNPOD_NETWORK_VOLUME_ID` was set, the model was still being
re-pulled into ephemeral container scratch every cold boot.

**Fixed in:** `8f9a40e` — `OLLAMA_MODELS=/workspace/ollama`,
`pod_entrypoint.sh` does `mkdir -p $OLLAMA_MODELS` at start-time so the
dir is created on the volume itself on first boot.

---

## What's currently NOT working (the open issue)

~~The network volume Mario created is in `US-NE-1`...~~

**RESOLVED 2026-05-06 PM.** Volume now in EU-RO-1 after two iterations.

Iteration history:
- `higomno9lt` (US-NE-1) — deleted, no 4090 capacity.
- `ibrfa4p6o1` (US-CA-2) — deleted, also no 4090 capacity (first real
  pod-creation attempt failed with `HTTP 500 create pod: could not find
  any pods with required specifications`).
- `8cjir4q7lb` (EU-RO-1) — **current**. 4090 capacity confirmed via a
  throwaway probe pod that successfully provisioned and was terminated.

Env var `AMG_RUNPOD_NETWORK_VOLUME_ID=8cjir4q7lb` is live in
`/etc/amg/controller.env`; controller restart confirmed via
`docker exec amg-controller printenv`.

**Important correction to "How to recover the volume situation" below:**
The previously-listed common winners `US-GA-1` and `US-OR-1` are NOT
viable — Runpod's REST API now reports `US-GA-1` has no storage
clusters and `US-OR-1` doesn't support network volumes at all.
Available DCs as of 2026-05-06: `CA-MTL-3, CA-MTL-4, EU-CZ-1, EU-NL-1,
EU-RO-1, EUR-IS-3, EUR-NO-1, US-CA-2, US-IL-1, US-KS-2, US-MO-1,
US-MO-2, US-NC-2, US-NE-1, US-TX-3, US-WA-1`. The runbook should
iterate through these, not the stale list, and probe with a real pod
create before assuming GPU capacity exists.

The next pipeline run is the first cold boot against the empty new
volume — it will still pay the ~5 GB / 3-5 min model pull, but the
pull writes to the volume. Subsequent runs skip it.

**Volume info (current):**
- Volume ID: `8cjir4q7lb`
- Name: `amg-ollama-cache`
- Size: 20 GB
- DC: EU-RO-1
- Cost: ~$1.40/mo

**Latency note:** Hetzner controller is in Ashburn VA, pods now run in
Romania. Controller↔pod traffic is small (job dispatch, status polls,
final zip pull). The big traffic is pod↔gdrive for the source video,
which is independent of pod region. Expect modest steady-state overhead
but nothing that'll dominate cold-boot time.

---

## Architecture (the picture for new agents)

```
                                              ┌──────────────────────┐
       Browser                                  │ Hetzner CPX21        │
       (any device,                            │ Ashburn, VA          │
       any OS)                                  │ 5.161.231.249        │
       │                                        │                      │
       │  HTTPS  ┌───────────────────────┐     │ Caddy → reverse proxy│
       └────────►│ amg.exoticplug.app    ├────►│   (auto-LE TLS)      │
                 │ (Cloudflare DNS-only) │     │ amg-controller:latest│
                 └───────────────────────┘     │   port 8000          │
                                                │ ┌──────────────┐     │
                                                │ │ Postgres-less:│    │
                                                │ │   sqlite for: │    │
                                                │ │ - users       │    │
                                                │ │ - rclone creds│    │
                                                │ │   (Fernet-enc)│    │
                                                │ └──────────────┘     │
                                                └────────┬─────────────┘
                                                         │ POST /v1/pods
                                                         │ (Runpod REST API)
                                                         ▼
                                                ┌──────────────────────┐
                                                │ Runpod GPU pod        │
                                                │ amg-pod:latest        │
                                                │ RTX 4090, 24GB        │
                                                │                       │
                                                │  ┌─────────────────┐  │
                                                │  │ pod_entrypoint  │  │
                                                │  │  → ollama serve │  │
                                                │  │  → ollama pull  │  │
                                                │  │  → exec uvicorn │  │
                                                │  └────────┬────────┘  │
                                                │           │           │
                                                │           ▼           │
                                                │  amg pod-worker       │
                                                │  (FastAPI, port 8000) │
                                                │  ↓ rclone pull        │
                                                │  ↓ amg.pipeline       │
                                                │  ↓ process_scene()    │
                                                │  ↓ /jobs/{id}/zip     │
                                                │      ↑                │
                                                │      └─ controller    │
                                                │         pulls covers  │
                                                │                       │
                                                │  /workspace/ollama/   │  ← network volume
                                                │  (model cache)        │     (OPTIONAL)
                                                └──────────────────────┘
                                                         │ rclone copy
                                                         ▼
                                                ┌──────────────────────┐
                                                │ Google Drive          │
                                                │ (gdrive_amy)          │
                                                └──────────────────────┘
```

### Key files (cloud edition)

| File | Purpose |
|---|---|
| `Dockerfile` | Controller image (no GPU, no Ollama) |
| `Dockerfile.pod` | Runpod pod image (Ollama bundled, ~5 GB layer) |
| `scripts/pod_entrypoint.sh` | Pod-side: starts Ollama, pulls model, execs `amg pod-worker` |
| `.github/workflows/build-images.yml` | GHA → builds + pushes both images to GHCR |
| `amg/cloud/runpod.py` | REST API wrapper for Runpod (provision/wait/terminate) |
| `amg/cloud/pod_worker.py` | FastAPI app that runs inside the pod |
| `amg/cloud/job_backend.py` | Controller-side `RunpodBackend` (provision → submit → poll → fetch zip → terminate) |
| `amg/cloud/credentials.py` | Encrypted SQLite store for rclone configs (Fernet) |
| `amg/cloud/rclone.py` | Subprocess wrapper |
| `amg/ui/app.py` | FastAPI controller UI (HTMX + Jinja2) |
| `amg/ui/templates/cloud_picker.html` | Cloud Storage page (`/cloud`) |
| `amg/ui/templates/_cloud_browse.html` | Folder/file rows in the picker (HTMX partial) |
| `docs/cloud_edition_runbook.md` | **Full deployment runbook** (Hetzner setup, secrets, GHCR, Caddy, systemd) |

### Controller env vars (`/etc/amg/controller.env` on Hetzner)

| Key | Purpose | Where to recover if lost |
|---|---|---|
| `AMG_SESSION_SECRET` | Cookie signing | 1Password (operator's vault) |
| `AMG_POD_AUTH_TOKEN` | Bearer token for controller↔pod auth | 1Password |
| `AMG_CREDENTIALS_KEY` | **Critical** — Fernet key for rclone configs. **If lost, rclone configs become unrecoverable** | 1Password |
| `AMG_RUNPOD_API_KEY` | Runpod REST API key | https://runpod.io/console/user/settings → API Keys |
| `AMG_RUNPOD_IMAGE` | `ghcr.io/marioarivera8-max/amg-pod:latest` | hardcoded |
| `AMG_RUNPOD_GPU_TYPE` | `NVIDIA GeForce RTX 4090` | hardcoded |
| `AMG_RUNPOD_NETWORK_VOLUME_ID` | `8cjir4q7lb` — `amg-ollama-cache` 20 GB in EU-RO-1 (set 2026-05-06 PM, after US-CA-2 had no 4090 capacity either) | see "Volume info" above |
| `AMG_DATA_DIR` | `/data` (mounted from `/var/lib/amg/data` on host) | runbook |
| `AMG_JOB_BACKEND` | `runpod` | runbook |

---

## What was deliberately NOT done

These were tempting but rejected as scope creep (or punted to a later phase):

- **Pod reuse for batched jobs.** Today: 10 jobs queued = 10 pods, 10 cold-boots.
  Wanted: 1 pod, 10 jobs back-to-back, idle-timer teardown. Estimated ½–1 day.
  Lives in `RunpodBackend._run_with_lifecycle()` — current shape is
  `provision → run-one → terminate`. Needs to become
  `provision-once → drain-queue → idle-timer → terminate`.

- **Auth on the pod's `/jobs/{id}/zip` endpoint.** It's currently
  unauthenticated (the controller knows the proxy URL and that's the
  only practical way to reach it, but technically anyone who guessed
  `<pod-id>-8000.proxy.runpod.net` could pull the zip). Low priority —
  pod IDs are random, pods are short-lived, no PII in the artifacts —
  but worth bolting on the bearer-token middleware for tidiness.

- **The other prior-agent UI WIP** (uncommitted at session start):
  - `_job_card.html` had its HTMX polling removed locally — that change
    is intentional (some other rework was happening). NOT shipped.
  - `_insight_panel.html` had cosmetic tweaks (cap titles at 8, preserve
    form state). NOT shipped.
  - `tests/test_ui_kept_bundle.py` is new and untracked. NOT shipped.

  Investigate before shipping. The shadcn agent's session may have left
  these in a deliberately-broken-but-WIP state.

- **Cleaner /healthz field on the controller.** Currently shows
  `ollama_ok: false` because it's checking localhost (where there's no
  Ollama on the controller — Ollama lives on the pod). Cosmetic, not a
  bug. The controller's health is actually fine.

---

## Dev environment state

- **Branch:** `main` is deployed.
- **HEAD:** `8f9a40e` — "Wire Ollama model cache to /workspace…"
- **Tests:** `pytest` collects 364 tests. Last targeted runs of
  `test_pod_worker`, `test_cloud_runpod`, `test_ui_cloud_picker` all
  green (51 + 11 = 62 of the cloud-edition tests).
- **Untracked / dirty:** `_insight_panel.html`, `_job_card.html` (modified),
  `_process_queues.html` (now committed), `tests/test_ui_kept_bundle.py`,
  `AGENT_CONTEXT_2026-05-04_LATEST.md` (the prior-era doc), and the
  ones this commit adds.
- **GHA build status:** both images green on commit `8f9a40e`. GHA had a
  partial outage during this session; if a build looks stuck for >5 min,
  check `https://www.githubstatus.com` — it bit us once already.

---

## How to recover the volume situation

> **Note (2026-05-06 PM):** This was performed once already — the volume
> now lives in US-CA-2 as `ibrfa4p6o1`. Kept here as a runbook in case
> US-CA-2 ever loses 4090 capacity and the swap needs to be redone in
> another DC.

When you have a few minutes, do this once:

1. **Check Runpod GPU availability per DC** — Runpod console → "Deploy a
   Pod" page shows real-time inventory per region. Pick one that
   consistently has 4090s. Common winners: **US-CA-2**, **US-GA-1**,
   **US-OR-1**.
2. **Delete the existing volume:** Runpod console → Storage →
   `amg-ollama-cache` → Delete.
3. **Create a new 20 GB volume in the chosen DC**, name it the same.
4. **Copy the new volume ID.**
5. SSH to Hetzner:
   ```bash
   ssh root@5.161.231.249
   sed -i 's|^AMG_RUNPOD_NETWORK_VOLUME_ID=.*|AMG_RUNPOD_NETWORK_VOLUME_ID=<new-id>|' /etc/amg/controller.env
   systemctl restart amg-controller
   ```
6. Click Process. First run: pays model pull (writes to volume).
   Subsequent runs: skips it.

Validation: confirm the volume was used by SSHing into the running pod
via Runpod's web terminal and `ls /workspace/ollama/`. Should see model
manifest files after a successful pull.

---

## Where the operator wants this to go (priorities for next session)

1. **Quick:** confirm covers from a successful smoke run actually look
   like the v11.1 baseline. If yes, mark cloud edition GA. If no, that's
   a v11.x scoring conversation, not infrastructure.

2. **Pod reuse for batches.** Mario said: "if I added a long queue of
   videos, it wouldn't need to cold boot each video correct?" Currently
   yes — each video cold-boots its own pod. The fix is real and worth
   ~50% time savings on batches of 5+. See "What was deliberately NOT
   done" above for where to start.

3. **Multi-user (Phase 3).** Currently single operator (`mario`).
   Operator wants Amy + 1-2 contractors logging in within ~6 months.
   Auth scaffolding already supports it (`amg user add <name> --role
   <operator|reviewer|admin>`). Needs: per-user job history filtering,
   audit log of actions, role-gated routes. Don't build until Mario
   actually onboards Amy.

4. **Cost dashboard + DB backups.** Hetzner box runs hourly snapshots
   already (Hetzner platform feature). The only durable data is
   `/var/lib/amg/data/{auth.sqlite, credentials.sqlite, work_dirs/, logs/}`.
   `credentials.sqlite` is encrypted by `AMG_CREDENTIALS_KEY` so it's
   safe to back up. Do this before any operator other than Mario starts
   relying on the system.

5. **Cloudflare Tunnel (optional).** Currently Caddy + Let's Encrypt
   with the public IP exposed on 80/443. Cloudflare Tunnel would hide
   the origin IP and add CF's WAF. Not urgent — UFW only allows 22/80/443.

---

## Working agreements (kept from prior context, still apply)

- Mario runs a real business; vague output costs him time.
- Direct + honest. Show concrete output (file lists, JSON, log lines).
- One change per commit. No bundling.
- Mario's eye is the only quality metric that ships.
- Test on **scene 4** for short iteration. Scene 7 for long-form stress.
  But for cloud-edition iteration, **NG008** (~2.84 GB, ~10 min) on
  GDrive is the de-facto cloud smoke-test scene now.
- Never `amg batch` while iterating.
- Bash 3.2 compatible for any shell scripts (macOS native bash).
- Mario uses zsh on Mac, will use Windows on the next machine.
- Mario is in Eastern time.

## What NOT to propose

- Closed-API vision (Anthropic / OpenAI / Google). TOS-blocked for adult
  content. Local Ollama on the pod is the only path.
- Reversing `REQUIRE_2257_DOC=False`. Tracked manually for now.
- Auto-uploads. Amy hasn't approved automation scope.
- Frontend frameworks (React / Vue / Svelte). Stack is locked at
  HTMX + FastAPI + Jinja2.
