# AMG OS — Resume Tomorrow

Last updated: 2026-05-08, after learning/performance/test-prep work.

Read `AGENT_CONTEXT_CURRENT.md` first. It now consolidates the active handoff:
live infrastructure, unpushed work, validation status, and next steps. This
file is only the short operator-facing reminder.

## Where Things Stand

AMG OS production is the cloud-hosted edition at
[https://amg.exoticplug.app](https://amg.exoticplug.app):

```text
Browser -> Hetzner controller -> Runpod RTX 4090 pod -> local Ollama on pod
```

The controller was rolled back to the stable GHCR pod image after a temporary
`ttl.sh` image caused a Runpod readiness problem (`RUNNING` with `runtime=null`).
The live stable pod image is:

```text
ghcr.io/marioarivera8-max/amg-pod:main-563036b
```

The current working tree contains unpushed but locally tested work for:

- text-generator/example-bank learning improvements
- premium published-success VOD seed ingestion
- review UI learning signals and rerun/reason fields
- PyAV + `6` parallel worker pod/runtime tuning
- tier-scan caps and richer decision-log telemetry
- Windows/test cleanup and this context consolidation

## First Thing To Do

Let Mario's current test scene finish on the stable pod. Then visually review
the covers in the UI. If quality is acceptable, organize and push the current
work.

Suggested push order:

1. Learning/text generation changes.
2. Review UI/operator loop changes.
3. Performance/runtime changes.
4. Documentation/context cleanup.

After GHCR builds the new pod image, update the live controller to the new
`ghcr.io/marioarivera8-max/amg-pod:main-<sha>` tag and run one short smoke
scene before touching any longer queue.

## Validation Already Done

Local Windows G14 test run:

```text
python -m pytest
403 passed, 2 skipped
```

Focused runtime/deployment test run:

```text
python -m pytest tests/test_job_backend.py tests/test_cloud_runpod.py tests/test_pipeline_integration_mocked.py
53 passed
```

## Current Planning Docs

- `AGENT_CONTEXT_CURRENT.md` is the canonical agent handoff.
- `docs/ROADMAP.md` is the priority direction.
- `docs/BACKLOG.md` is the execution list.
- `docs/cloud_edition_runbook.md` is the deployment runbook.
- `docs/WINDOWS_G14_SETUP.md` is the local Windows dev setup.
