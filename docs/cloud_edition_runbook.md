# AMG Cloud-Hosted Edition — Deployment Runbook

> Status: Phase 1 + Phase 2 code complete. This runbook walks the operator
> through provisioning the infrastructure and wiring the components together
> for the first end-to-end smoke test.
>
> Architecture (Phase 1 + 2):
>
> ```
> Browser (you, anywhere)
>      ↓ HTTPS
>      ↓
> Caddy on Hetzner CX21  ←── auto-TLS via Let's Encrypt + Cloudflare DNS
>      ↓ localhost:8000
>      ↓
> AMG controller (Docker container running `amg ui --auth`)
>   - SQLite user store      (data/auth.sqlite)
>   - Encrypted cred store   (data/credentials.sqlite, Fernet/AMG_CREDENTIALS_KEY)
>   - Job dispatcher         (AMG_JOB_BACKEND=runpod)
>   - Work-dir artifacts     (data/work_dirs/<scene_id>/)
>      ↓ HTTPS                            ↓ HTTPS
>      ↓                                  ↓
> Runpod GPU pod (RTX 4090)          Google Drive / Dropbox / Mega
>   - amg pod-worker (FastAPI)         (videos live here, never touch
>   - amg.pipeline.process_scene        controller VM)
>   - Ollama + qwen2.5vl:7b              ↑
>   - rclone (cloud-source pulls) ───────┘
> ```
>
> Two Docker images get built:
>
> | Image                | Used by      | Built from        | Has Ollama? |
> |----------------------|--------------|-------------------|-------------|
> | `amg-controller:0.1` | Hetzner VM   | `Dockerfile`      | No (no GPU) |
> | `amg-pod:0.1`        | Runpod pod   | `Dockerfile.pod`  | Yes (CUDA)  |

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
python -c 'from cryptography.fernet import Fernet; print("AMG_CREDENTIALS_KEY=" + Fernet.generate_key().decode())'
```

| Secret                  | Purpose                                                  | Lose it = ?                                          |
|-------------------------|----------------------------------------------------------|------------------------------------------------------|
| `AMG_SESSION_SECRET`    | Signs cookie sessions for the UI login                   | All logged-in sessions invalidated; users sign in again |
| `AMG_POD_AUTH_TOKEN`    | Bearer token controller ↔ pod-worker                     | Re-deploy controller; old pods reject new requests   |
| `AMG_CREDENTIALS_KEY`   | Fernet key for the encrypted rclone credential store     | **Stored cloud-storage tokens unrecoverable** — re-run `rclone config` for each remote |

You'll also need:
- A long, random password for your operator account (`amg user add`).
- Your Runpod API key from https://www.runpod.io/console/user/settings.

### Step 2. Publish the AMG container images (controller + pod) via GitHub Actions

Two images get built — one for the always-on controller (no GPU, no Ollama),
one for the on-demand Runpod pods (with Ollama + CUDA libs bundled).

You don't need Docker installed on your Mac. The workflow at
`.github/workflows/build-images.yml` builds both images on GitHub's
linux/amd64 runners and pushes them to GHCR using the built-in
`GITHUB_TOKEN`. No PAT, no docker login, no local install.

**One-time repo setup** (only needed if your repo is brand new):

1. Push this branch / repo to GitHub if it isn't already:
   `git push origin main`
2. Open https://github.com/&lt;owner&gt;/AMG-OS/settings/actions and confirm
   "Workflow permissions" is set to **Read and write permissions** (default
   for new repos created after Feb 2023; older repos may need this flipped
   manually so the workflow can publish to GHCR).

**Trigger the build:**

```bash
# After committing the Dockerfiles + workflow:
git push origin main
```

Or run it manually from the GitHub UI:
**Actions tab → "build-images" workflow → Run workflow**.

The first run takes ~10 min (controller image ~ 4 min, pod image ~ 6 min
because it includes the 1.2 GB Ollama tarball). Subsequent runs are faster
thanks to the GHA cache (`type=gha`).

When it finishes you'll see two new packages on your profile at
https://github.com/&lt;owner&gt;?tab=packages :

- `amg-controller`
- `amg-pod`

**Make both packages public** (otherwise Runpod / Hetzner can't pull
without a registry secret — and these only contain application code, not
data or credentials):

For each package:

1. Click the package → **Package settings** (right rail)
2. Scroll to "Danger Zone" → **Change package visibility** → **Public**
3. Confirm.

Save these tags for later:

```text
ghcr.io/<owner>/amg-controller:latest    # used by Hetzner controller
ghcr.io/<owner>/amg-pod:latest           # used by Runpod pods (AMG_RUNPOD_IMAGE)
```

> **Already have Docker installed locally and prefer manual builds?** See the
> commented-out commands at the bottom of `Dockerfile.pod` — `docker buildx
> build --platform linux/amd64 -f Dockerfile.pod --push -t ghcr.io/...`
> works the same way. The GHA workflow is just the recommended path because
> it avoids local install + builds 5–10× faster on native amd64 hardware.

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

In Cloudflare (https://dash.cloudflare.com → your domain → DNS → Records):

- Click **Add record**
- Type: **A**
- Name: **amg** (creates `amg.yourdomain.com`)
- IPv4 address: **<controller IPv4>**
- Proxy status: **DNS only (grey cloud)** — important: leave Cloudflare proxy
  OFF for now. Caddy needs to terminate TLS itself for Let's Encrypt to work
  via HTTP-01 challenge. (You can switch to "Proxied" later for DDoS
  protection once Caddy has a cert; that's a Phase 4 item.)
- TTL: Auto

Wait ~30s, then verify from your Mac:

```bash
dig +short amg.yourdomain.com    # should print the controller IPv4
```

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

# 3. Log in to GHCR so the controller VM can pull a private package.
#    Skip this if you made the package public in step 2 (recommended for the
#    controller image too — it's just app code).
echo $GHCR_PAT | docker login ghcr.io -u $GH_USER --password-stdin

# 4. Pull the controller image you published in step 2
docker pull $CONTROLLER_IMAGE

# 5. Persistent data directory for the controller (survives container recycles).
#    Holds: data/auth.sqlite (users), data/credentials.sqlite (encrypted
#    rclone configs), data/work_dirs/* (per-scene outputs).
mkdir -p /var/lib/amg/data
chown -R 1000:1000 /var/lib/amg/data    # non-root amg user inside container

# 6. Caddyfile (replace amg.yourdomain.com)
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
    ghcr.io/<GH_USER>/amg-controller:0.1 \
    amg ui --auth --host 0.0.0.0 --port 8000
ExecStop=/usr/bin/docker stop amg-controller

[Install]
WantedBy=multi-user.target
EOF
```

Replace `<GH_USER>` with your GitHub username.

### Step 7. Drop the controller env file

```bash
mkdir -p /etc/amg
cat > /etc/amg/controller.env <<'EOF'
# --- UI auth (fail-open is impossible without these) ---
AMG_SESSION_SECRET=<from step 1>
AMG_AUTH_DISABLED=0

# --- Pod handshake (controller ↔ pod-worker bearer token) ---
AMG_POD_AUTH_TOKEN=<from step 1>

# --- Encrypted credential store (Phase 2 cloud-source picker) ---
# Without this set, the Cloud picker page errors out telling you to set it.
# Without this preserved across redeploys, the encrypted rclone tokens in
# data/credentials.sqlite become unrecoverable.
AMG_CREDENTIALS_KEY=<from step 1>

# --- Backend dispatch ---
AMG_JOB_BACKEND=runpod

# --- Runpod ---
AMG_RUNPOD_API_KEY=<paste from runpod.io console>
AMG_RUNPOD_IMAGE=ghcr.io/<GH_USER>/amg-pod:0.1
AMG_RUNPOD_GPU_TYPE=NVIDIA GeForce RTX 4090
# Optional but RECOMMENDED: persistent network volume so the 5 GB qwen2.5vl
# model weights are cached between pods. Without it, every pod boot pulls
# the model again (~5 min cold-start tax per job).
# Create a 25 GB volume in the Runpod console first, then paste its ID here.
# AMG_RUNPOD_NETWORK_VOLUME_ID=

# --- Storage paths inside the container map to the host volume ---
AMG_DATA_DIR=/data

# --- Job timing ---
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

## Phase 2 — Wire the cloud-source picker

After the Phase 1 upload smoke test passes, switch from "upload from Mac" to
"pull directly from Drive/Dropbox/Mega" so videos go datacenter-to-datacenter
instead of through your home internet.

### Step 11. OAuth your cloud-storage providers (one-time, on your Mac)

The OAuth dance opens a browser, so it has to happen on a machine with a
browser — not the headless Hetzner VM. On your **Mac**:

```bash
# Install rclone if you don't have it
brew install rclone

# Walk through the interactive setup — pick "drive" / "dropbox" / "mega"
# (and "Yes" to "Use web browser to autoauthenticate").
rclone config
```

Repeat `rclone config` once per provider you want to add. Output lands in
`~/.config/rclone/rclone.conf`.

> **Drive "Shared with me" gotcha**: rclone's default Drive remote sees only
> *My Drive*, not *Shared with me*. If your videos live in a shared folder,
> open Google Drive in the browser, right-click the shared folder → **Add
> shortcut to Drive** → put it under *My Drive*. The shortcut makes the
> content visible through the standard remote.

### Step 12. Import the rclone configs into the controller

SSH to the controller and run:

```bash
docker exec -it amg-controller amg cloud-remote add
# Paste the relevant [section] from your local ~/.config/rclone/rclone.conf
# Hit Ctrl-D on a blank line.
docker exec amg-controller amg cloud-remote list
```

(Repeat per provider.)

### Step 13. Verify and use the picker

1. Refresh the controller UI in your browser. You should see a new **Cloud**
   nav link.
2. Click **Cloud** → your remotes appear in the left column.
3. Click **Browse** on a remote → drill into folders → click **Process** on
   any video.
4. Watch the job card. Status flow: `queued → downloading → running → done`.
5. The downloaded video lives only on the GPU pod's ephemeral disk and is
   wiped when the pod terminates.

If `amg cloud-remote list` shows your remote but the Cloud page says
"AMG_CREDENTIALS_KEY not set", you forgot to add it to `controller.env`
or the systemd unit isn't reading the env file. Re-check Step 7.

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

## What's next (Phase 3+)

Phases 1 and 2 give you a working cloud-hosted single-operator deployment.
Phases 3+ add multi-user safety, observability, and cost controls before
this is shared beyond Mario.

- **Phase 3**: per-user accounts (Amy, contractors), per-user job history,
  audit log of significant actions.
- **Phase 5** (deferred): cloud-target output — push the generated covers
  back to the same Drive/Dropbox folder the source video came from, so your
  distribution workflow doesn't need a separate "download from controller"
  step.

---

## Cost-protection guardrails (Phase 4)

These are **not yet implemented** but will land before this is shared
beyond the operator:

- Pod auto-shutdown timer (force-terminate after N hours regardless of
  job state)
- Cost dashboard in the UI (cumulative Runpod credits this month)
- Per-job hard ceiling (refuse jobs longer than 4× expected duration)
- Login rate-limit + optional Cloudflare Tunnel front door
