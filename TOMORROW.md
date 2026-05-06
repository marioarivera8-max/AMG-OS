# AMG OS — Resume Tomorrow

## Where things stand (end of session 2026-05-06, ~10:20 EDT)

We pivoted from "local Mac pipeline" to a **cloud-hosted edition** running on
Hetzner + Runpod, accessible at [https://amg.exoticplug.app](https://amg.exoticplug.app).
The full read-the-room context is in `AGENT_CONTEXT_2026-05-06_CLOUD_EDITION.md`.

HEAD is `8f9a40e`. Test suite collects 364 tests; targeted cloud-edition
suites (`test_pod_worker`, `test_cloud_runpod`, `test_ui_cloud_picker`) all
green (62/62). Source tree dirty with prior-agent UI WIP that hasn't been
investigated yet.

Mario is moving from his M4 Mac to a **Windows G14 (3070, 32 GB RAM, Win11)**
mid-stream. Setup steps for the new machine are in
`docs/WINDOWS_G14_SETUP.md`.

## What's working end-to-end (verified live this session)

- Login → Cloud picker → drill GDrive → click Process
- Controller provisions Runpod 4090 pod
- Pod cold-boots Ollama, pulls qwen2.5vl:7b, starts pod-worker
- Pod downloads scene from gdrive_amy via rclone
- `process_scene` runs end-to-end: `success=True, covers_saved=12`
- Controller pulls cover artifacts back as small zip *(fix shipped today)*
- Pod terminated, billing stops

The **only** thing not yet verified is "do the covers actually look right".
Mario's eye hasn't seen them yet because we kept hitting infra bugs above
that step. That's the first thing to do.

## The single most important next step

**Re-run the smoke test, look at the covers.** If they're the v11.1-quality
baseline, the cloud edition is GA. If they're not, we have a real (separate)
scoring conversation, but it's in the v11.x pipeline, not in any cloud-edition
code we wrote this session.

Procedure:
1. Confirm the controller is still healthy: `curl https://amg.exoticplug.app/healthz`
2. Log in as `mario` at `https://amg.exoticplug.app/login`
3. Cloud → drill `gdrive_amy:Copy of Public Links/Nico Grey Content/<some scene>`
4. Click **Process** on the .MOV file
5. Wait ~5-10 min for cold boot (no network volume → model pull cost every time
   right now; see `AGENT_CONTEXT_2026-05-06_CLOUD_EDITION.md` for why)
6. When job moves to "Recent completed", click into it. View the contact sheet.
7. Eyeball it.

## Resolved: network volume DC swap (2026-05-06 PM)

Old volume `higomno9lt` in **US-NE-1** deleted; new 20 GB volume
`ibrfa4p6o1` (`amg-ollama-cache`) created in **US-CA-2**. Env var
`AMG_RUNPOD_NETWORK_VOLUME_ID=ibrfa4p6o1` is live in
`/etc/amg/controller.env`; controller restarted and confirmed picking up
the new value (`docker exec amg-controller printenv | grep
AMG_RUNPOD_NETWORK_VOLUME_ID`).

Effect on next runs:
- **First pipeline run after the swap** still pays the model pull
  (~5 GB / 3–5 min) because the volume is empty — but the pull writes
  to the volume this time.
- **Every run after that** skips the model pull. Cold boot drops to
  ~2–3 min (just the docker image pull).

Validation step (do once on the next smoke test): after a successful
run, SSH into the running pod via Runpod's web terminal and
`ls /workspace/ollama/manifests/registry.ollama.ai/library/qwen2.5vl/`
— if you see a manifest there, the volume is being used.

## Other open items, in priority order

1. **Pod reuse for batched jobs.** Mario explicitly asked for this. Currently
   each video cold-boots its own pod. Need `RunpodBackend._run_with_lifecycle`
   to: provision-once → drain-queue → idle-timer → terminate. ½–1 day.

2. ~~Investigate the prior-agent UI WIP...~~ **Resolved 2026-05-06 PM.**
   The Mac agent triaged all four files (`_job_card.html`,
   `_insight_panel.html`, `scoring/scene_describer.py`,
   `tests/test_ui_kept_bundle.py`), pushed them to a `wip/` branch, and
   the G14 agent cherry-picked each file as its own commit onto `main`
   (`9e89c46`, `f5d3e51`, `616bd0d`, `6c5eeba`). The transient
   `wip/2026-05-06-mac-ui-leftovers` branch is deleted. Three follow-up
   holes were called out in the commit bodies and should be addressed
   when next touching those files:
   - `scene_describer.py`: gibberish-rejection rules have no test
     coverage; silent 110-char truncation; the twice-called sanitize
     papers over a suspected bug in `_enforce_lead_performer_in_titles`.
   - `_insight_panel.html`: leftover `style="grid-column: 1 / -1;"` on
     the bottom `<details>` is a no-op without a grid parent — clean up
     when next touching review-page layout.

3. **Cover-quality smoke test on a long-form scene.** The verified run was
   on a ~10 min scene (NG008/muvie.mp4, 2.84 GB). Try a 45-min scene to
   stress the time-budget cascade. Scene 7 from the local incoming folder
   would be ideal but it's local-only; pick the longest scene in
   `gdrive_amy:Copy of Public Links/Nico Grey Content/` instead.

4. **Pod `/jobs/{id}/zip` is unauthenticated.** Cosmetic security improvement.
   Bolt the same bearer-token middleware on that endpoint.

5. **Multi-user phase.** Auth scaffolding already supports `--role
   {operator,reviewer,admin}`. Don't actually wire it up until Mario onboards
   Amy or a contractor; over-building auth before there are real users invites
   bugs and confusion.

## Mac retirement — open ops items (raised by Mac data sweep, 2026-05-06 PM)

The Mac agent did a full sweep before the operator retires the Mac. Most
things are already on GitHub or in the cloud; these aren't:

1. **Back up `/etc/amg/controller.env` somewhere durable.** Operator does
   not have 1Password yet. `AMG_CREDENTIALS_KEY` is the only truly
   unrecoverable secret in there (losing it bricks the encrypted rclone
   configs). Plan: `scp` the env file off the Hetzner box and stash it in
   a private gdrive folder under `gdrive_amy:Copy of Public
   Links/AMG_OS_data/`. The G14 agent will do this; queued.
2. **Migrate four Mac-only data dirs** to the same gdrive folder so the
   learning loop has continuity later:
   - `data/studio_profiles/` (operator-tuned thresholds, ~1.7 KB)
   - `data/decision_logs/` (20 files, 228 KB)
   - `data/reviewed/` (9 operator decisions)
   - `data/operator_feedback/feedback.jsonl` (60 KB)
   Operator runs the `tar czf ... && rclone copy ...` on the Mac (one
   command, ~1 minute, ~1 MB total). G14 agent then `rclone copy`s back
   to the Hetzner box and untars to `/var/lib/amg/data/`.
3. **`data/training/`** (~8 MB) — preserve only if Mario plans to resume
   the May-4 training/scoring work. Otherwise skip.
4. **Mac-side SSH key revocation** — not strictly necessary while the
   Mac sits in a drawer; do this in 30 seconds if/when the Mac is
   actually wiped/sold/donated. Procedure: remove the Mac's pubkey from
   `/root/.ssh/authorized_keys` on the controller.

## Things that are NOT pending (deferred / done)

- v11.x local pipeline work (training/scoring) is paused but valid. The
  `AGENT_CONTEXT_2026-05-04_LATEST.md` doc describes it and is the right
  reference if Mario wants to resume that branch of work.
- Local Mac dev environment is no longer load-bearing. Mario can still run
  `amg process <video>` locally if he keeps the venv around, but it's not
  needed for ongoing operation.

## Quick command reference for cloud-edition admin

```bash
# Health
curl https://amg.exoticplug.app/healthz

# SSH to controller
ssh root@5.161.231.249

# View controller logs
ssh root@5.161.231.249 'docker logs --tail 100 amg-controller'

# Roll out a new image (after GHA build completes)
ssh root@5.161.231.249 'docker pull ghcr.io/marioarivera8-max/amg-controller:latest && systemctl restart amg-controller'

# Add a user
ssh -t root@5.161.231.249 'docker exec -it amg-controller amg user add <name>'

# List rclone remotes
ssh root@5.161.231.249 'docker exec amg-controller amg cloud-remote list'

# Add an rclone remote (from your local machine)
rclone config show <remote_name> | ssh root@5.161.231.249 'docker exec -i amg-controller amg cloud-remote add'

# Inspect Runpod state
ssh root@5.161.231.249 'docker exec amg-controller python /tmp/list_pods.py'  # if /tmp/list_pods.py still exists
```

## Cursor handoff notes (Windows G14 transition)

- See `docs/WINDOWS_G14_SETUP.md` for step-by-step.
- All AI handoff docs (this file, `AGENT_CONTEXT_2026-05-06_CLOUD_EDITION.md`,
  `AGENTS.md`, `CLAUDE.md`, `CLAUDE_CODE_HANDOFF.md`) are in the repo and
  Cursor's auto-loader will surface them.
- Secrets all live in 1Password — Mario should restore those onto the new
  machine via 1Password sync.
- The new agent on the new machine doesn't have the conversation transcript
  from this session, but the docs above should fully reconstruct intent.
