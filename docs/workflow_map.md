# Workflow Map

Active workflow map for current system behavior.

## End-to-End States

`queued -> running -> done|error -> review`

Review remains mandatory before any downstream business action.

## Execution Paths

- Cloud path (primary): UI submission, remote pod execution, results returned to
  controller.
- Local path (secondary): local CLI scene processing for development validation.

## Constraints

- no auto-upload path
- no closed API vision path
- keep compliance/manual approval checkpoints explicit
