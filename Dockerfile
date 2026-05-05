# AMG OS - Production container image.
#
# This image runs the AMG pipeline. It does NOT bundle Ollama; it talks to
# a remote (or sidecar) Ollama via OLLAMA_HOST. That separation is intentional:
# - On the cloud-hosted Runpod pod, Ollama runs in a separate process/container
#   alongside AMG and is reached via 127.0.0.1.
# - On a LAN GPU box / Tailscale topology, Ollama runs on a different machine
#   and OLLAMA_HOST points at it (e.g. 192.168.1.50:11434 or a tsnet IP).
# - On Runpod the proxy URL form (https://podid-11434.proxy.runpod.net) is
#   accepted directly by the OLLAMA_HOST parser added in v11.1.5.
#
# Default CMD: `amg pod-worker` — the FastAPI service that the controller VM
# pushes jobs to. This is what you want on Runpod. For local CLI use, override
# the CMD (see "Smoke test" below).
#
# Build:
#   docker build -t amg-os:dev .
#
# Run as a Runpod pod worker (cloud edition):
#   docker run --rm -p 8000:8000 \
#     -v amg-data:/data \
#     -e OLLAMA_HOST="127.0.0.1:11434" \
#     -e AMG_POD_AUTH_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
#     amg-os:dev
#
# Smoke test (CLI):
#   docker run --rm \
#     -v "$HOME/AMG_OS/data:/data" \
#     -v "$HOME/AMG_Processing:/incoming" \
#     -e OLLAMA_HOST="host.docker.internal:11434" \
#     amg-os:dev amg verify
#
# Run the local UI from the container (override CMD):
#   docker run --rm -p 8080:8080 \
#     -v "$HOME/AMG_OS/data:/data" \
#     -v "$HOME/AMG_Processing:/incoming" \
#     -e OLLAMA_HOST="host.docker.internal:11434" \
#     amg-os:dev amg ui --host 0.0.0.0 --port 8080

FROM python:3.12-slim-bookworm

# Runtime system deps:
#   ffmpeg            -> PyAV decode + ffprobe metadata
#   libgl1, libglib2  -> opencv-python runtime requirements
#   curl, ca-certs    -> rclone download + TLS roots
#   unzip             -> rclone install (pinned binary, see below)
#   tini              -> proper PID 1 / signal handling for amg ui (uvicorn)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        curl \
        ca-certificates \
        unzip \
        tini \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# rclone (cloud-storage transfers for Phase 2). Pinned + sha256-verified so
# the build is reproducible. Bumping version: update both the version arg
# and the SHA256SUMS-derived hash. Source:
# https://downloads.rclone.org/v<VERSION>/SHA256SUMS
ARG RCLONE_VERSION=1.74.0
ARG RCLONE_SHA256=61de0a78d8776fe3e080f8385ebe96d817f2ee6a6003fe36b2d9f3b49d3e36ea
RUN curl -fsSL "https://downloads.rclone.org/v${RCLONE_VERSION}/rclone-v${RCLONE_VERSION}-linux-amd64.zip" \
        -o /tmp/rclone.zip \
    && echo "${RCLONE_SHA256}  /tmp/rclone.zip" | sha256sum -c - \
    && cd /tmp \
    && unzip -q rclone.zip \
    && mv rclone-v${RCLONE_VERSION}-linux-amd64/rclone /usr/local/bin/rclone \
    && chmod +x /usr/local/bin/rclone \
    && rm -rf /tmp/rclone* \
    && rclone version | head -1

# Run as non-root in the container. UID 1000 is conventional for primary user.
RUN useradd --create-home --shell /bin/bash --uid 1000 amg

WORKDIR /app

# Install dependencies first so the layer is cached when only source changes.
# Editable install with a stub amg/__init__.py satisfies setuptools' package
# discovery; the real source is copied over the stub in the next step and is
# what gets imported at runtime (-e creates an .egg-link pointing at /app/amg).
COPY pyproject.toml ./
RUN mkdir -p amg && touch amg/__init__.py \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

# Real source replaces the stub. Layer cache breaks here on code changes only.
COPY amg/ ./amg/

# Default mount points - config.py reads these via AMG_DATA_DIR + AMG_INCOMING_ROOTS.
ENV AMG_DATA_DIR=/data \
    AMG_INCOMING_ROOTS=/incoming \
    OLLAMA_HOST=127.0.0.1:11434 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN mkdir -p /data /incoming \
    && chown -R amg:amg /data /incoming /app

USER amg

# Pod-worker port (default for cloud edition).
EXPOSE 8000
# UI port (override CMD with `amg ui --host 0.0.0.0 --port 8080` to use this).
EXPOSE 8080

# tini gives uvicorn a clean signal-handling parent so SIGTERM stops the server
# instead of being swallowed by Python's default PID-1 behavior.
ENTRYPOINT ["/usr/bin/tini", "--"]
# Default: launch the pod-worker. The container refuses to start unless
# AMG_POD_AUTH_TOKEN is provided — that's intentional, see amg/cloud/pod_worker.py.
CMD ["amg", "pod-worker", "--host", "0.0.0.0", "--port", "8000"]
