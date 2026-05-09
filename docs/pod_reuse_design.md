# Pod Reuse Design Notes

Status: design note only, not authoritative runtime behavior.

## Why This Exists

This file captures an optimization direction (warm-pod reuse) that may reduce
cold-start overhead. It is not currently the source of truth for production
defaults.

## Current Production Truth

Use live runtime values in `AGENT_CONTEXT_CURRENT.md` and
`docs/cloud_edition_runbook.md` for actual behavior.

## Keep/Discard Policy

- Keep this doc as a scoped future design note.
- Do not treat any env vars here as active unless they are present in live
  controller env and reflected in primary docs.
