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

## Open issue: network volume in wrong DC

`AMG_RUNPOD_NETWORK_VOLUME_ID=higomno9lt` was working but the volume's pinned
to **US-NE-1** which currently has zero 4090 / A6000 / L40 capacity. Every pod
provision returned HTTP 500 instantly. We blanked the env var to unblock; now
pods provision wherever capacity is hot, but every cold boot pays the 5 GB
model pull (~3-5 min).

To fix (when not blocked on smoke test): delete the US-NE-1 volume, create a
new 20 GB volume in **US-CA-2** or **US-GA-1**, plug ID back into
`/etc/amg/controller.env`, restart controller. Procedure detailed in the
context doc under "How to recover the volume situation."

## Other open items, in priority order

1. **Pod reuse for batched jobs.** Mario explicitly asked for this. Currently
   each video cold-boots its own pod. Need `RunpodBackend._run_with_lifecycle`
   to: provision-once → drain-queue → idle-timer → terminate. ½–1 day.

2. **Investigate the prior-agent UI WIP** that's still untracked or modified:
   - `_job_card.html` — HTMX polling removed locally, intentional?
   - `_insight_panel.html` — cosmetic tweaks (cap titles at 8, preserve form state)
   - `tests/test_ui_kept_bundle.py` — new, untracked
   Don't ship blindly. Read the prior session notes if they exist; if not,
   evaluate against the live UI behavior.

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
