#!/bin/bash
# AMG OS — Pod-side entrypoint.
#
# Runs as PID 2 under tini (PID 1) inside the Runpod GPU container.
# Lifecycle:
#   1. Start `ollama serve` in the background.
#   2. Poll http://127.0.0.1:11434/api/tags until Ollama responds (max 60s).
#   3. Pre-pull the vision model so the first scan doesn't pay the 5 GB
#      download cost. Skipped if the model is already cached on the
#      attached network volume (OLLAMA_MODELS=/data/ollama).
#   4. exec the command passed in by Docker CMD (default: amg pod-worker).
#      `exec` lets the worker take over PID 2 so SIGTERM from Runpod
#      propagates correctly through tini -> worker -> uvicorn shutdown.
#
# Failure modes:
#   * If Ollama doesn't come up in 60s, dump the last 30 lines of its log
#     and exit non-zero. The pod will be marked failed by Runpod and the
#     controller will see a connection-refused error from the pod-worker
#     health check.
#   * If `ollama pull` fails, we still exec the worker — the pipeline will
#     fail on the first AI call with a clearer error than "model not found".

set -euo pipefail

OLLAMA_HOST_BIND="${OLLAMA_HOST:-127.0.0.1:11434}"
OLLAMA_HEALTH_URL="http://${OLLAMA_HOST_BIND}/api/tags"
OLLAMA_LOG="/tmp/ollama.log"
MODEL="${AMG_VISION_MODEL:-qwen2.5vl:7b}"
READY_TIMEOUT_SEC="${AMG_OLLAMA_READY_TIMEOUT_SEC:-60}"

log() { echo "[pod-entrypoint $(date -u +%H:%M:%S)] $*"; }

log "starting ollama serve (host=${OLLAMA_HOST_BIND})"
ollama serve > "$OLLAMA_LOG" 2>&1 &
OLLAMA_PID=$!
log "ollama serve pid=${OLLAMA_PID}, waiting up to ${READY_TIMEOUT_SEC}s for ready..."

# Trap so the background ollama is killed if this script exits early
# (e.g. ollama crash before exec, or model pull failure with errexit).
trap 'kill -TERM "$OLLAMA_PID" 2>/dev/null || true' EXIT

ready=0
for i in $(seq 1 "$READY_TIMEOUT_SEC"); do
    if curl -fsS "$OLLAMA_HEALTH_URL" >/dev/null 2>&1; then
        ready=1
        log "ollama ready after ${i}s"
        break
    fi
    sleep 1
done

if [ "$ready" -ne 1 ]; then
    log "FATAL: ollama did not become ready within ${READY_TIMEOUT_SEC}s"
    log "last 30 lines of ${OLLAMA_LOG}:"
    tail -30 "$OLLAMA_LOG" || true
    exit 1
fi

# Pre-pull the vision model. Skips if already cached on the network volume.
# We tolerate failures here (||true) so a transient registry issue doesn't
# kill the pod outright — the first AI call will surface a clearer error.
if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$MODEL"; then
    log "model ${MODEL} already cached"
else
    log "pulling model ${MODEL} (this can take ~5 min on first boot, cached afterwards)"
    if ! ollama pull "$MODEL"; then
        log "WARNING: ollama pull ${MODEL} failed; proceeding anyway"
    fi
fi

# Drop the trap before exec so the worker owns ollama-shutdown via tini.
trap - EXIT

log "execing: $*"
exec "$@"
