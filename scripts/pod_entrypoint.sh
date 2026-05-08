#!/bin/bash
# AMG OS — Pod-side entrypoint.
#
# Runs as PID 2 under tini (PID 1) inside the Runpod GPU container.
# Lifecycle:
#   1. Start `ollama serve` in the background.
#   2. Poll http://127.0.0.1:11434/api/tags until Ollama responds (max 60s).
#   3. Spawn `ollama pull <vision model>` in the BACKGROUND so the
#      pod-worker FastAPI app can start immediately and serve /healthz.
#      The model pull is what blocks /readyz on a cold container without
#      a cached network volume — letting it run concurrently with the
#      worker means the controller's readiness probe sees /healthz=200
#      within ~30s instead of waiting 5–15 min for the pull to complete
#      before uvicorn even binds :8000.
#   4. exec the command passed in by Docker CMD (default: amg pod-worker).
#      `exec` lets the worker take over PID 2 so SIGTERM from Runpod
#      propagates correctly through tini -> worker -> uvicorn shutdown.
#
# Why background-pull (changed 2026-05-08):
#   The original v0 entrypoint blocked on `ollama pull` before exec'ing
#   the worker. With a Runpod network volume attached this was fine — the
#   pull was a no-op after the first cold boot. But H100 SXM is not in
#   the EU-RO-1 datacenter where the network volume lives, so H100 runs
#   pay a fresh 5 GB pull every cold-start. While the pull was running,
#   /healthz returned nothing (uvicorn hadn't bound yet), and the
#   controller's 10-minute readiness timeout would fire long before the
#   pull finished, terminating the pod and failing the user's job. The
#   /readyz endpoint (added in the same release) is the right place to
#   surface "model not loaded yet" — let the pull race with the worker
#   and have the controller poll /readyz instead.
#
# Failure modes:
#   * If Ollama doesn't come up in 60s, dump the last 30 lines of its log
#     and exit non-zero. The pod will be marked failed by Runpod and the
#     controller will see a connection-refused error from the pod-worker
#     health check.
#   * If the background `ollama pull` fails, the worker still serves
#     /healthz (so the controller doesn't think the pod is dead) and
#     /readyz keeps returning 503 with a clear "model_ok=false" payload
#     until the pod is replaced or the model is manually pulled.

set -euo pipefail

OLLAMA_HOST_BIND="${OLLAMA_HOST:-127.0.0.1:11434}"
OLLAMA_HEALTH_URL="http://${OLLAMA_HOST_BIND}/api/tags"
OLLAMA_LOG="/tmp/ollama.log"
MODEL="${AMG_VISION_MODEL:-qwen2.5vl:7b}"
READY_TIMEOUT_SEC="${AMG_OLLAMA_READY_TIMEOUT_SEC:-60}"
MODELS_DIR="${OLLAMA_MODELS:-/workspace/ollama}"

log() { echo "[pod-entrypoint $(date -u +%H:%M:%S)] $*"; }

# Ensure the model cache dir exists. When a Runpod network volume is
# attached at /workspace, the volume mount supersedes the empty directory
# baked into the image at build-time, so we have to mkdir at start-time
# to create the per-pod ollama subdir on the volume itself. No-op on
# subsequent boots once the dir already exists on the volume.
mkdir -p "$MODELS_DIR"
log "ollama models dir: ${MODELS_DIR} (volume-backed if AMG_RUNPOD_NETWORK_VOLUME_ID is set)"

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
# Runs in the BACKGROUND so we can exec the pod-worker immediately and
# get /healthz responding quickly. The /readyz endpoint reports whether
# the pull has finished (model_ok=true) so the controller knows when to
# submit work without making /healthz block on the pull.
#
# The background pull is reparented to tini (PID 1) once we exec the
# worker, so its lifetime is bounded by the container's. tini reaps any
# orphans on shutdown.
if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$MODEL"; then
    log "model ${MODEL} already cached"
else
    log "pulling model ${MODEL} in BACKGROUND (controller will see /healthz=200 immediately; /readyz=503 until pull finishes)"
    (
        if ollama pull "$MODEL"; then
            log "background ollama pull ${MODEL} OK"
        else
            log "WARNING: background ollama pull ${MODEL} failed; /readyz will keep returning 503"
        fi
    ) &
    PULL_PID=$!
    log "background pull pid=${PULL_PID}"
fi

# Drop the trap before exec so the worker owns ollama-shutdown via tini.
# The background pull (if any) has already been backgrounded with `&`
# above, so it survives the trap removal and gets reparented to tini.
trap - EXIT

log "execing: $*"
exec "$@"
