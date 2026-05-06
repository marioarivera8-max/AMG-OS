# Windows G14 setup — picking up where the Mac left off

You're moving from the M4 Mac to the **2024 Asus G14 (RTX 3070, 32 GB RAM,
Windows 11)**. This walks you through getting back to a productive state.

**Good news:** because we pivoted to a cloud-hosted edition, you don't need
to recreate the heavy local Mac dev environment (Python 3.12, ffmpeg, Ollama,
qwen2.5vl model, rclone configs). The G14 only needs to be a **code-editing
+ git + ssh** machine. The actual pipeline runs on Hetzner + Runpod.

---

## Estimated time: 30-45 min

Most of it is downloading Cursor and Git for Windows.

---

## What you need before starting

- Your GitHub account credentials (you already have a PAT from the Mac
  session, or you can sign in to GitHub via Cursor's GitHub integration).
- Your 1Password access on the G14. **Critical:** all the secrets that
  matter live there:
  - `AMG_SESSION_SECRET`
  - `AMG_POD_AUTH_TOKEN`
  - `AMG_CREDENTIALS_KEY` (irreplaceable — losing this bricks the
    encrypted rclone configs on the controller)
  - `AMG_RUNPOD_API_KEY`
  - The Hetzner root password (used to set up the SSH key initially)
  - Your Cloudflare API token (if you ever need to change DNS)
- Your Hetzner SSH private key from the Mac. (Or, alternative: generate a
  new one and add the public key to Hetzner. See "SSH key" below for both
  paths — generating fresh is fine.)

---

## Step 1 — Install the basics on Windows

### 1a. Cursor

1. Download from [https://cursor.com/download](https://cursor.com/download).
2. Install. Sign in with the same account you use on the Mac (so your
   settings, MCPs, and chat history sync).
3. After install: Settings → Features → MCP — check that `cursor-ide-browser`
   and `plugin-shadcn-shadcn` are enabled (they should sync from the Mac).

### 1b. Git for Windows

1. Download from [https://git-scm.com/download/win](https://git-scm.com/download/win).
2. Install with mostly defaults. Two recommendations during the wizard:
   - **"Override the default branch name for new repositories"** → set to
     `main` (matches GitHub default).
   - **"Configure the line ending conversions"** → choose
     `Checkout as-is, commit as-is`. This avoids LF/CRLF churn that would
     show up as bogus diffs on every file when you push.
3. After install, open the **Git Bash** shell (NOT PowerShell — bash makes
   the cross-platform commands in our runbooks Just Work).

### 1c. (Optional but recommended) Windows Terminal

If you don't already have it: install from the Microsoft Store. It hosts
Git Bash, PowerShell, and WSL tabs side by side. Worth the 30 seconds.

### 1d. (Optional) Python 3.12

Only needed if you want to **run tests locally** or **run `amg verify`**
to inspect things from the G14.

1. Download Python 3.12 (NOT 3.13, NOT 3.14 — the PyAV/Pillow wheel
   constraints from `CLAUDE.md` still apply if you ever do anything
   with the local pipeline).
2. Install. Check **"Add python.exe to PATH"** during install.
3. Confirm: `python --version` should show 3.12.x.

If you don't install Python: you can still edit the codebase and push to
git, and GHA will run all tests on every push. You just won't have a
local pytest loop.

### 1e. (Optional) WSL2

If you want a more Mac-like dev experience, install WSL2 + Ubuntu:
```powershell
wsl --install
```
Reboot, set up an Ubuntu user, then everything Bash/Linux Just Works.
The G14 has plenty of RAM for it. The rest of this guide assumes Git
Bash, but every command is identical in WSL.

You do NOT need WSL to use Cursor — Cursor's terminal in Git Bash works fine.

---

## Step 2 — SSH key for Hetzner

You need to be able to `ssh root@5.161.231.249` to manage the controller.

### Option A — Generate a fresh key on the G14

Cleaner than copying from the Mac. In Git Bash:

```bash
ssh-keygen -t ed25519 -C "mario@g14"
# Accept default location (~/.ssh/id_ed25519). Set a passphrase or skip.
cat ~/.ssh/id_ed25519.pub
```

Copy the output (the line starting with `ssh-ed25519 …`).

Then add it to the Hetzner box. There are two ways:

1. **From the Mac while you still have access:**
   ```bash
   ssh root@5.161.231.249 "echo '<paste-pubkey-here>' >> ~/.ssh/authorized_keys"
   ```

2. **From the Hetzner console (web)** if you don't have Mac access:
   - Hetzner Cloud Console → your project → server → **Console** button
     (opens a web terminal).
   - Log in with the root password (in 1Password).
   - `nano ~/.ssh/authorized_keys`, paste the new pubkey on a new line,
     save.

Test from the G14:
```bash
ssh root@5.161.231.249 'echo connected'
```

### Option B — Copy the existing key from the Mac

If you still have the Mac handy:

```bash
# On the Mac:
cat ~/.ssh/id_ed25519        # private key
cat ~/.ssh/id_ed25519.pub    # public key
```

On the G14 (Git Bash):

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
# Paste private key contents into ~/.ssh/id_ed25519
# Paste public key contents into ~/.ssh/id_ed25519.pub
chmod 600 ~/.ssh/id_ed25519
chmod 644 ~/.ssh/id_ed25519.pub
```

Test:
```bash
ssh root@5.161.231.249 'echo connected'
```

Option A is cleaner long-term. Pick that one if there's no time pressure.

---

## Step 3 — Clone the repo

In Git Bash:

```bash
mkdir -p ~/dev
cd ~/dev
git clone https://github.com/marioarivera8-max/AMG_OS.git
cd AMG_OS
```

(If `marioarivera8-max` isn't right, double-check the GHCR image path
`ghcr.io/marioarivera8-max/amg-controller:latest` — the GitHub username
and the GHCR namespace are the same.)

Authenticate. Cursor or Git for Windows will prompt; use your GitHub
PAT or sign in via the browser (Git Credential Manager handles this
automatically on Windows).

Then open in Cursor:

```bash
cursor .
```

(If `cursor` isn't on PATH, just open Cursor's GUI and File → Open Folder.)

---

## Step 4 — Recover Cursor agent context

Cursor's chat history is per-machine, NOT synced across devices. The agent
on the G14 will start fresh — no memory of this session. **The handoff
docs are how it catches up.**

When you start a new chat in Cursor on the G14, your **first message** to
the agent should be exactly:

> Read AGENTS.md, then TOMORROW.md, then AGENT_CONTEXT_2026-05-06_CLOUD_EDITION.md
> in that order. We pivoted to a cloud-hosted edition; the live URL is
> https://amg.exoticplug.app . I'm now on a Windows G14 instead of the Mac.

That gives the new agent the same loaded context the Mac agent had.

You can also bookmark these in Cursor for quick re-reference:

- `AGENTS.md` — cross-tool entry point, lists the read-first docs
- `TOMORROW.md` — what's open, what's next
- `AGENT_CONTEXT_2026-05-06_CLOUD_EDITION.md` — full state of the cloud edition
- `docs/cloud_edition_runbook.md` — Hetzner/Runpod/GHA deployment runbook
- `CLAUDE.md` — project-wide standing rules

---

## Step 5 — (Optional) Local Python venv

Only do this if you want to run pytest locally. Skip otherwise — GHA runs
the full suite on every push to `main`.

```bash
cd ~/dev/AMG_OS
python -m venv venv
source venv/Scripts/activate          # Git Bash; in PowerShell it's venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -e .
pip install pytest pytest-asyncio
pytest tests/test_pod_worker.py tests/test_cloud_runpod.py tests/test_ui_cloud_picker.py
```

If those three pass, you're set for local iteration on cloud-edition code.
You do NOT need to install ffmpeg, PyAV, or Ollama on the G14 — those are
only needed if you want to run the local v11.x pipeline, which the cloud
edition doesn't depend on.

---

## Step 6 — Sanity checks

Before you call it done, run through these to make sure the new setup
actually works:

```bash
# 1. Repo is up to date
git pull origin main
git log -1 --oneline    # should match what's on the Mac

# 2. SSH to controller works
ssh root@5.161.231.249 'docker ps --format "{{.Names}}\t{{.Status}}"'
# expect: amg-controller   Up X hours

# 3. Live site is up
curl https://amg.exoticplug.app/healthz

# 4. Login works in browser
# Open https://amg.exoticplug.app/login → sign in as mario
```

If all four pass, you're fully back online from the G14.

---

## Things you can leave behind on the Mac

- Local `venv/` (you'll make a new one if needed)
- Local `~/AMG_Processing/incoming/` (the videos can stay on the Mac;
  cloud-edition pulls from GDrive instead)
- Local `data/` directory (`decision_logs/`, `studio_profiles/`,
  `audit_logs/`, etc — these are per-machine and gitignored anyway)
- Local rclone configs (`~/.config/rclone/rclone.conf`) — already imported
  into the controller's encrypted credential store. You don't need them on
  the G14.
- Local Ollama install — not needed.

If you ever come back to local Mac iteration, all of that is still there.

---

## Things to bring with you (to the G14)

- 1Password access (for the secrets listed at the top)
- Cursor account login
- GitHub login
- The **GHCR PAT** if you want to manually pull images locally — mostly
  unnecessary now that GHA handles publishing.

That's it. The cloud-edition architecture means the G14 doesn't need to
reproduce any of the local Mac heavy machinery.

---

## What to ask the new agent first

Once you're set up on the G14:

1. Start a new Cursor chat.
2. Use the bootstrap message from Step 4.
3. Then ask one of:
   - "Run the smoke test plan from TOMORROW.md and confirm covers look
     like the v11.1 baseline." (most likely first task)
   - "Move the Runpod network volume from US-NE-1 to US-CA-2." (the open
     infra issue from this session)
   - "Implement pod reuse for batched jobs (the priority-2 item in
     TOMORROW.md)." (the next architectural step)

Pick whichever matches your mood. They're all independent and any of
them is a valid first task.

---

## If something is broken

- **Can't push to git from the G14:** GitHub's Git Credential Manager on
  Windows opens a browser auth flow on first push. If that doesn't work,
  generate a PAT at https://github.com/settings/tokens and use it as the
  password.
- **SSH to Hetzner fails:** double-check your public key is in
  `~/.ssh/authorized_keys` on the Hetzner box. Hetzner's web console is
  the recovery path if SSH itself is broken.
- **Live site down:** SSH to Hetzner, `docker logs --tail 200 amg-controller`,
  check what's actually wrong. Most common cause from this session was
  GHA pushing a bad image; rolling back is `docker pull
  ghcr.io/marioarivera8-max/amg-controller:<previous-sha>` then
  `systemctl restart amg-controller`.
- **Cursor agent has no idea what's going on:** point it at this file
  again. The handoff docs are the single source of truth for catching up.
