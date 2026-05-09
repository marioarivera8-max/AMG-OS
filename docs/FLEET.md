# Fleet Coordination

This document now covers active coordination scope only.

## Active Fleet Model

- Cloud production is centralized through the Hetzner controller.
- Local machines are development/validation clients, not parallel production
  authorities.
- Git remains code synchronization only; `data/` remains local and gitignored.

## Practical Workflow

1. Make code changes locally.
2. Run targeted tests.
3. Deploy image updates through the cloud runbook.
4. Validate one cloud scene run.
5. Review visual quality before rollout.

## Recovery Principle

If cloud and local behavior diverge, prioritize cloud runtime facts from:

- `/etc/amg/controller.env`
- controller service logs
- current deployed image tags

Then update docs to match observed runtime.
