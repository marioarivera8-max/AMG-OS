# AMG OS Architecture

Canonical architecture reference for active operation modes.

## Two Active Paths

1. Cloud production path:
   - browser UI
   - Hetzner controller (`amg ui --auth`)
   - Runpod GPU pod worker
   - Ollama running on the GPU pod

2. Local development path:
   - local CLI (`amg process`)
   - same core pipeline modules

## Core Pipeline

`amg/pipeline.py::process_scene` orchestrates scene processing and output:

- inventory and metadata
- compliance gate (operator-configured)
- calibration and scan flow
- candidate scoring and fallback cascade
- output generation and decision logging

## Runtime Invariants

- `amg/config.py` is the source of truth for tunables.
- decision logs are required output for every run.
- quality remains the release gate (human review), even under speed work.
- no automatic downstream upload behavior in v11.x.

## Current Cloud Runtime Defaults

Follow `AGENT_CONTEXT_CURRENT.md` for exact live values. Current baseline:

- runpod backend
- fast profile
- streaming scan enabled
- video backend auto
- 6/6 parallelism for Ollama and AI worker concurrency

## Historical Notes

Historical local-only design assumptions were removed from this active
architecture file. Use dated context docs only for postmortem history.
