# AMG OS Tomorrow Notes

Short operator reminder only. For authoritative defaults, read
`AGENT_CONTEXT_CURRENT.md`.

## Current Direction

- Cloud production path remains primary.
- Local CLI path remains active for development checks.
- Keep quality-first validation on single-scene iterations.

## First Checks Next Session

1. Confirm controller service healthy.
2. Confirm controller env still matches baseline in `AGENT_CONTEXT_CURRENT.md`.
3. If testing throughput, submit jobs while the pod is warm to avoid cold-start
   noise.
4. Review covers visually before expanding queue size.

## AMG_OS v1 Note

The 2026-05-09 H100 breakthrough is now the baseline: ffmpeg-cuda decode,
streaming scan, parallel Ollama, and 900s warm-pod reuse produced `130.15s`
and `140.45s` warm-pod pipeline runs on real scenes.

## Avoid

- stale image tags copied from old notes
- changing policy constraints (no closed API vision, no auto-upload)
