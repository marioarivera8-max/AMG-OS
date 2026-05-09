# CLAUDE.md

Guidance for AI agents working in this repository.

## First Read

Read `AGENT_CONTEXT_CURRENT.md` first. It is the authoritative runtime context.

## Product Reality

AMG OS processes adult VOD scenes and currently runs in two active modes:

- Cloud production mode: Hetzner controller + Runpod GPU pod + Ollama on-pod.
- Local development mode: local CLI using the same pipeline.

Do not frame the project as local-only or cloud-only.

## Policy Constraints

- Do not propose Anthropic/OpenAI/Google vision APIs.
- Do not change `REQUIRE_2257_DOC=False` in `amg/config.py`.
- Do not implement automatic platform uploads.
- Keep explicit human review in the workflow.

## Architectural Invariants

- `amg/config.py` is the source of truth for pipeline tunables.
- Core pipeline orchestration lives in `amg/pipeline.py::process_scene`.
- Decision logs are required output and must remain usable for analysis.
- Runtime changes must preserve quality first, then speed.

## Operational Defaults (Production)

Follow live controller defaults unless explicitly overridden:

- runpod backend
- fast profile
- streaming scan enabled
- runpod video backend auto
- 6/6 parallelism (`OLLAMA_NUM_PARALLEL` and `AMG_AI_PARALLEL_WORKERS`)
- optional scene-insight and provided-thumbnail scoring disabled

## Iteration Rules

- Test one scene/job at a time.
- Avoid full `amg batch` for iteration.
- Validate with tests and one real scene run before declaring success.

## Recommended Validation Commands

```bash
python -m pytest tests/test_stream_scan.py tests/test_job_backend.py
```

```bash
ssh -i "$HOME/.ssh/id_ed25519" root@5.161.231.249 "journalctl -u amg-controller -n 120 --no-pager"
```
