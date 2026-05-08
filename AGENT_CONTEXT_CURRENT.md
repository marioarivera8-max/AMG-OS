# AMG OS Current Agent Context

Last updated: 2026-05-08, after cloud performance/test-prep session.

Read this first. It is the current handoff for AI agents working in this
repository. Older dated context files remain as history, but this file is the
source of truth for "where are we now and what should happen next."

## Current Production Shape

AMG OS processes adult VOD scenes for B2B distribution. The production path is
now the cloud-hosted edition:

```text
Browser at https://amg.exoticplug.app
  -> Hetzner controller VM (FastAPI + HTMX + auth)
  -> Runpod RTX 4090 pod (Ollama + qwen2.5vl:7b + amg pod-worker)
  -> Google Drive source videos via rclone
  -> controller stores returned covers, contact sheets, decision logs
```

The local `amg process <video>` workflow still exists for development and
baseline checks, but it is no longer the production operator path.

The vision pipeline must stay local-Ollama-on-GPU. Do not propose Anthropic,
OpenAI, Google, or other closed vision APIs for content analysis; adult content
policy makes those a non-starter.

## Non-Negotiable Rules

- Never auto-upload to platforms. Amy has not approved automation past the
  operator's click.
- Never change `REQUIRE_2257_DOC=False` in `amg/config.py`; 2257 tracking is
  manual for now.
- Preserve human-in-the-loop review. The operator's eye is the final quality
  metric.
- Avoid bundled mega-changes when possible. One clear reason per commit is the
  preferred operating style.
- Do not run `amg batch` while iterating. Use one scene/job at a time.
- Do not treat local data as portable. `data/` is machine-local and gitignored.

## Known-Good Quality Baseline

v11.1 is the known-good cover-quality baseline. v11.2 bundled too many changes
and was rolled back after cover-quality regression. When optimizing speed, do
not trade away cover quality without explicit visual approval from Mario.

Test convention:

- Short iteration scene: scene 4, about 10 minutes.
- Long stress scene: scene 7 / long-form scene when available.
- Cloud edition smoke tests should be visually reviewed in the UI, not judged
  only by JSON scores.

## Current Live Infrastructure

- Controller: Hetzner VM at `5.161.231.249`
- Public UI: `https://amg.exoticplug.app`
- Runpod GPU: RTX 4090, network volume `8cjir4q7lb`
- Cloud source: `gdrive_amy`
- Current stable pod image in live controller env:
  `ghcr.io/marioarivera8-max/amg-pod:main-563036b`

Important: during the 2026-05-08 speed-prep session, a temporary
`ttl.sh/amg-pod-22787:24h` image was tested. It produced a pod readiness issue
where Runpod marked the pod `RUNNING` but `runtime` was `null`; readiness
attempts climbed past normal. The bad pod was terminated, and the controller
was rolled back to the stable GHCR pod image above.

## Work Completed But Not Yet Pushed

The working tree currently includes a large set of related changes. They are
tested locally but have not been committed or pushed unless git history says
otherwise.

Performance/runtime changes:

- `pyproject.toml` adds `av` so PyAV can be available in pod/controller images.
- `Dockerfile.pod` defaults:
  - `OLLAMA_NUM_PARALLEL=6`
  - `AMG_AI_PARALLEL_WORKERS=6`
  - `AMG_VIDEO_BACKEND=pyav`
- `amg/config.py` defaults `AI_PARALLEL_WORKERS` to env-overridable `6`.
- Tier scan guardrails were added:
  - `AMG_TIER_SCAN_MAX_EXTRACTED_FRAMES_PER_TIER` default `220`
  - `AMG_TIER_SCAN_MAX_AI_FRAMES_PER_TIER` default `80`
  - `AMG_TIER_SCAN_MAX_WALL_SEC_PER_TIER` default `420`
- `amg/scanning/tiered.py`, `amg/pipeline.py`, and
  `amg/output/decision_log.py` now preserve detailed tier telemetry.
- `amg/cloud/job_backend.py` injects pod env for parallelism/backend and defaults
  warm-pod idle termination to 900 seconds.
- Tests now assert the pod env injection for `6` workers and `pyav`.

Learning/text-generation changes:

- Approved example bank and retrieval-augmented prompt path were added.
- Published-success seed handling treats VOD examples and operator document
  examples as premium signals, not weak sparse text.
- Rule Lab / rule-pack evaluator and promotion scaffolding were added.
- Review UI captures acceptance/reasoning/score signals used by the learning
  loop.
- Scripts exist for curated doc ingest and VOD cover ZIP ingest.

UI/operator-loop changes:

- Cloud picker supports multi-select/add-to-submit queue behavior.
- Review/library/scene pages include richer learning and rerun signals.
- Login/process queue UI had styling and layout work.

Cleanup/test changes made during this handoff pass:

- Temporary/debug artifacts are ignored via `.gitignore`.
- `AMG_INCOMING_ROOTS` now uses `os.pathsep`, fixing Windows drive-letter
  parsing.
- Stale tests were updated for the current cloud picker/process queue layout.

## Validation Already Run

On Windows G14 local Python:

```text
python -m pytest
403 passed, 2 skipped
```

Focused deployment/runtime tests also passed:

```text
python -m pytest tests/test_job_backend.py tests/test_cloud_runpod.py tests/test_pipeline_integration_mocked.py
53 passed
```

Linter diagnostics on recently edited files reported no errors.

## Next Recommended Steps

1. Let Mario's currently running test scene finish on the stable pod image.
2. Review covers visually. If quality is acceptable, commit the current work in
   small logical commits if practical:
   - learning/text generator changes
   - UI/review loop changes
   - performance/runtime changes
   - context/doc cleanup
3. Push to `main` only after the branch is organized. GitHub Actions
   `.github/workflows/build-images.yml` will build:
   - `ghcr.io/marioarivera8-max/amg-controller:latest`
   - `ghcr.io/marioarivera8-max/amg-pod:latest`
   - `main-<sha>` tags for both
4. After the pod image is built in GHCR, update live `/etc/amg/controller.env`
   to use the new stable `amg-pod:main-<sha>` tag, not `ttl.sh`.
5. Restart `amg-controller`, run one short scene, then compare:
   - pod readiness attempts
   - total wall time
   - tier telemetry in the decision log
   - cover quality by eye
6. If `6` workers is stable, optionally A/B test `8` workers on the same scene.
   Do not assume more parallelism is faster; verify wall time and error rate.

## Operational Docs

- `docs/cloud_edition_runbook.md`: deployment, secrets, controller setup, GHCR.
- `docs/RUNBOOK.md`: daily operator/CLI runbook.
- `docs/TROUBLESHOOTING.md`: recovery and debugging.
- `docs/WINDOWS_G14_SETUP.md`: Windows dev environment notes.
- `.github/workflows/build-images.yml`: controller/pod image build pipeline.
- `docs/ROADMAP.md` and `docs/BACKLOG.md`: current planning and task list.

## Historical Context

These files are archives, not first-read context:

- `AGENT_CONTEXT_2026-05-06_CLOUD_EDITION.md`: cloud pivot details and bug
  graveyard from cutover.
- `SESSION_NOTES_2026-05-06.md`: narrative context from the cloud cutover.
- `AGENT_CONTEXT_2026-05-04_LATEST.md` and `SESSION_NOTES_2026-05-04.md`:
  local training/scoring branch history. Use only if Mario explicitly resumes
  that branch.
- `CLAUDE_CODE_HANDOFF.md`: older strategic brief. Business constraints still
  matter, but its local-only/Tauri/v11.3 assumptions are superseded by this
  current context.

## Quick Commands

```bash
# Local tests
python -m pytest
python -m pytest tests/test_job_backend.py tests/test_cloud_runpod.py

# Live controller checks
ssh -i "$HOME/.ssh/id_ed25519" root@5.161.231.249 "systemctl status amg-controller --no-pager"
ssh -i "$HOME/.ssh/id_ed25519" root@5.161.231.249 "journalctl -u amg-controller -n 120 --no-pager"

# Image build path
git push origin main
# then watch GitHub Actions -> build-images
```

## Reminder For Future Agents

When the user asks "what next," prefer concrete operational next steps over
abstract plans. Mario values direct, verifiable status: file lists, command
outputs, image tags, job IDs, and visible UI results.
