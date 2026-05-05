# AMG Cloud-Hosted Edition — Deployment Runbook

> Status: Phase 1 code complete (commits 70e810a → 95fc9b7). This runbook
> walks the operator through provisioning the infrastructure and wiring
> the components together for the first end-to-end smoke test.
>
> Architecture (Phase 1):
>
> ```
> Browser (you, anywhere)
>      ↓ HTTPS
>      ↓
> Caddy on Hetzner CX21  ←── auto-TLS via Let's Encrypt
>      ↓ localhost:8000
>      ↓
> AMG controller (Docker container running `amg ui --auth`)
>   - SQLite user store     (data/auth.sqlite)
>   - Job dispatcher        (AMG_JOB_BACKEND=runpod)
>   - Work-dir artifacts    (data/work_dirs/<scene_id>/)
>      ↓ HTTPS
>      ↓
> Runpod GPU pod (RTX 4090)  ←── on-demand, terminated after each job
>   - amg pod-worker (FastAPI)
>   - amg.pipeline.process_scene
>   - Ollama + qwen2.5vl:7b
> ```

---

## Prerequisites you provide

| Item | Where | Cost (USD) | Notes |
|---|---|---|---|
| Domain name | Any registrar (Namecheap, Cloudflare, Porkbun) | $8–15/yr | A-record `amg.<your-domain>` will point at the controller VM |
| Hetzner account | https://hetzner.com/cloud | — | Permissive on adult content; CX21 is €4.51/mo |
| Runpod account + API key | https://runpod.io | — | Pay-as-you-go GPU; ~$0.34/hr for RTX 4090 (Secure Cloud) |
| Container registry account | GitHub Container Registry (free) or Docker Hub | — | To publish the AMG image. GHCR recommended |

**Estimated monthly cost** (with usage of ~20 hours of GPU time per month):
- Hetzner CX21 (always on, 24/7): €4.51 / ~$5 USD
- Runpod RTX 4090 (20 hours/month): 20 × $0.34 ≈ $7
- Domain: $1 amortized
- **Total: ~$13/mo** for a fully cloud-hosted single-user deployment.

---

## Phase 1 — One-time setup

### Step 1. Generate secrets locally (before touching any cloud)

These get baked into env vars on the controller. Generate them once and
save them somewhere safe (1Password, etc.); they should never enter git.

```bash
python -c 'import secrets; print("AMG_SESSION_SECRET=" + secrets.token_urlsafe(48))'
python -c 'import secrets; print("AMG_POD_AUTH_TOKEN=" + secrets.token_urlsafe(48))'
```

`AMG_SESSION_SECRET` — signs the cookie sessions for the UI login.
`AMG_POD_AUTH_TOKEN` — bearer token between controller and pod-worker.

You'll also need:
- A long, random password for your operator account (`amg user add`).
- Your Runpod API key from https://www.runpod.io/console/user/settings.

### Step 2. Publish the AMG container image

The Dockerfile at the repo root is what Runpod pulls into each provisioned
pod. From the repo on your Mac:

```bash
# Pick a registry. GHCR example (replace <username>):
export REGISTRY=ghcr.io/<username>
export IMAGE=$REGISTRY/amg-os:0.1

# Log in (one-time; for GHCR you need a PAT with write:packages)
echo $GITHUB_TOKEN | docker login ghcr.io -u <username> --password-stdin

# Build for amd64 since Runpod GPUs are x86. Apple Silicon needs --platform.
docker buildx build --platform linux/amd64 -t $IMAGE --push .

# Verify
docker pull $IMAGE && echo "image is reachable"
```

Save `$IMAGE` — you'll set it as `AMG_RUNPOD_IMAGE` later.

### Step 3. Provision the Hetzner CX21 controller

In the Hetzner Cloud console:

1. Create a new project: "amg-os"
2. Add an SSH key (your Mac's `~/.ssh/id_ed25519.pub`)
3. Create a server:
   - Image: **Ubuntu 24.04 LTS**
   - Type: **CX21** (2 vCPU, 4 GB RAM, 40 GB disk, €4.51/mo)
   - Location: closest to you
   - SSH key: your key from above
   - Name: `amg-controller`

Note the IPv4 address.

### Step 4. Point your domain

In your DNS provider, create:
- `A`  record:  `amg.yourdomain.com`  →  `<controller IPv4>`

Wait for propagation (`dig amg.yourdomain.com` should return your IP).

### Step 5. Set up the controller VM

SSH in:

```bash
ssh root@amg.yourdomain.com
```

Then run, **one section at a time**, reading the output:

```bash
# 1. Update + tooling
apt update && apt upgrade -y
apt install -y docker.io caddy ufw

# 2. Firewall: only allow SSH + HTTP + HTTPS in
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# 3. Pull the AMG image you published in step 2
docker pull <your-image-from-step-2>

# 4. Persistent data directory for the controller
mkdir -p /var/lib/amg/data

# 5. Caddyfile (replace amg.yourdomain.com)
cat > /etc/caddy/Caddyfile <<'EOF'
amg.yourdomain.com {
    reverse_proxy 127.0.0.1:8000
    encode gzip
    # Big uploads: scenes can be 4GB+. No body cap.
    request_body {
        max_size 10GB
    }
}
EOF
systemctl reload caddy
```

### Step 6. Drop the AMG controller systemd unit

```bash
cat > /etc/systemd/system/amg-controller.service <<'EOF'
[Unit]
Description=AMG OS controller (FastAPI + auth + remote dispatcher)
After=docker.service network-online.target
Requires=docker.service

[Service]
Restart=always
RestartSec=5
EnvironmentFile=/etc/amg/controller.env
ExecStartPre=-/usr/bin/docker rm -f amg-controller
ExecStart=/usr/bin/docker run --rm --name amg-controller \
    --env-file /etc/amg/controller.env \
    -p 127.0.0.1:8000:8000 \
    -v /var/lib/amg/data:/data \
    <YOUR-IMAGE> \
    amg ui --auth --host 0.0.0.0 --port 8000
ExecStop=/usr/bin/docker stop amg-controller

[Install]
WantedBy=multi-user.target
EOF
```

Replace `<YOUR-IMAGE>` with the one from step 2.

### Step 7. Drop the controller env file

```bash
mkdir -p /etc/amg
cat > /etc/amg/controller.env <<'EOF'
# UI auth (fail-open is impossible without these)
AMG_SESSION_SECRET=<value from step 1>
AMG_AUTH_DISABLED=0

# Pod handshake (controller talks to pod with this token; pod requires it)
AMG_POD_AUTH_TOKEN=<value from step 1>

# Backend dispatch
AMG_JOB_BACKEND=runpod

# Runpod
AMG_RUNPOD_API_KEY=<your runpod api key>
AMG_RUNPOD_IMAGE=<image from step 2>
AMG_RUNPOD_GPU_TYPE=NVIDIA GeForce RTX 4090
# Optional: persistent volume so model weights survive between pods
# AMG_RUNPOD_NETWORK_VOLUME_ID=

# Storage paths inside the container map to the host volume
AMG_DATA_DIR=/data

# Job timing
AMG_JOB_PROVISION_TIMEOUT_SEC=600
AMG_JOB_RUN_TIMEOUT_SEC=14400
EOF
chmod 600 /etc/amg/controller.env
```

### Step 8. Start the controller

```bash
systemctl daemon-reload
systemctl enable --now amg-controller
systemctl status amg-controller       # should be 'active (running)'
journalctl -u amg-controller -n 50    # confirm AMG UI started, no errors
```

Visit `https://amg.yourdomain.com/login`. You should see the login page.

### Step 9. Create your operator account

On the controller:

```bash
docker exec -it amg-controller amg user add mario
# enter password twice; remember it; you'll log in to the web UI with it
docker exec -it amg-controller amg user list
```

Sign in at `https://amg.yourdomain.com/login`.

### Step 10. End-to-end smoke test

From the web UI:

1. Drop scene 4 (`~/AMG_Processing/incoming/YasminaBrady/4 BG - bath teasing scene/`)
   on the dropzone.
2. Watch the job card go `queued` → `running`. The first run will take
   ~10 min for the pod to pull the image + Ollama model. Subsequent
   runs (if you set up a network volume in step 7) skip the model pull.
3. When done, the covers should appear in `Library`. Open the scene to
   verify the contact sheet and decision log render.
4. Confirm the pod was terminated:
   - Runpod console → Pods → list should be empty (or only show pods you
     manually created for testing).
   - Runpod console → Billing → recent usage should show one short-lived
     pod for this job.

If all four are green, **Phase 1 is operational**.

---

## Troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| `/login` returns 502 from Caddy | controller container down | `journalctl -u amg-controller -n 100` |
| login form rejects your password | wrong password OR no users created | `docker exec amg-controller amg user list` |
| job stuck at `queued` forever | dispatcher not picking it up | `journalctl -u amg-controller --since "5 min ago"` |
| pod provision fails with `AMG_RUNPOD_API_KEY` rejected | bad API key | regenerate at runpod.io and re-write `controller.env` |
| pod times out before RUNNING | image too big or registry slow | bump `AMG_JOB_PROVISION_TIMEOUT_SEC` to 1200 |
| pod runs but `/jobs` returns 401 | token mismatch | confirm `AMG_POD_AUTH_TOKEN` in controller env matches what the pod gets via env |

---

## What's next (Phase 2 — fast cloud-storage transfers)

Right now the smoke test pushes the whole video file from your browser →
controller → pod. That's slow over residential internet for 4GB+ scenes.

Phase 2 adds rclone-driven downloads on the pod side: pick a video from
your Google Drive / Dropbox / Mega in the UI, the pod downloads it
directly from the cloud-storage provider's CDN at datacenter-to-datacenter
speeds (typically 10×–50× faster than your home upload).

That's a separate code drop; it builds on Phase 1, not into it.

---

## Cost-protection guardrails (Phase 4)

These are **not yet implemented** but will land before this is shared
beyond the operator:

- Pod auto-shutdown timer (force-terminate after N hours regardless of
  job state)
- Cost dashboard in the UI (cumulative Runpod credits this month)
- Per-job hard ceiling (refuse jobs longer than 4× expected duration)
- Login rate-limit + optional Cloudflare Tunnel front door
