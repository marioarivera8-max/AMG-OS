# Throughput Options — Audit Findings + Runbooks

Created: 2026-05-05. Companion to `.cursor/plans/amg_efficiency_options_*.plan.md`.

This doc is the result of executing all three todos from the throughput-options plan:
audit → Runpod trial recipe → decision sheet.

---

## 1. Audit findings (executed 2026-05-05)

### Method

Single scene 4 run on M4 Pro / 24 GB / Ollama 0.22.1 / qwen2.5vl:7b Q4_K_M
at the existing config (`OLLAMA_NUM_PARALLEL=4`, `AI_PARALLEL_WORKERS=4`),
followed by a comparison run at `NUM_PARALLEL=8`. Logs in `/tmp/amg_scene4_audit.log`
and `/tmp/amg_scene4_p8.log` if not yet rotated.

### Result 1 — Parallelism is NOT broken

The v11.2 hypothesis that `score_frames_parallel` was secretly serializing is
**false in v11.1.5**. Measured on scene 4:

| AI batch | calls | wall_sec | mean_call_sec | parallelism_factor |
|---|---|---|---|---|
| Tier 1 | 6 | 85.4 | 52.3 | 3.67 |
| Finish hunter | 4 | 41.5 | 41.4 | 3.99 |
| Buildup hunter | 10 | 111.3 | 44.5 | 4.00 |
| Cluster expansion | 9 | 115.8 | 49.1 | 3.82 |
| **Aggregate** | **29** | **354.0** | **46.9** | **~3.87 / 4.0** |

Parallelism factor is at **97% of theoretical maximum**. The thread pool in
[amg/scoring/orchestrator.py](../amg/scoring/orchestrator.py) is doing its job.

### Result 2 — AI scoring is 60% of wall time

Full scene 4 timing breakdown (10:34 video, 634 sec source, 619 sec wall):

| Phase | Wall sec | % of run |
|---|---|---|
| Ingest + open + calibration | ~58 | 9% |
| Tier 1 extract + dedup | ~29 | 5% |
| **AI scoring (all phases)** | **~354** | **57%** |
| Cover save + contact sheet + insight | ~36 | 6% |
| Tail (title gen, distribution gate, post) | ~132 | 21% |
| Other (PyAV opens, decode, dedup) | ~10 | 2% |

Inference is the dominant cost. Anything that accelerates per-call latency
moves the wall ~linearly.

### Result 3 — `OLLAMA_NUM_PARALLEL=8` is WORSE on Apple Silicon, not better

Bumping to 8 was tested (system restored after — see commands below in section
"Restore commands"). Per-call latency **doubled** while parallelism factor
scaled, leaving net throughput identical:

| Workers | mean_call_sec | parallelism_factor | calls/sec (effective) |
|---|---|---|---|
| 4 | 46.9 | 3.87 | 0.083 |
| 8 | 93.5 | 7.91 | 0.085 |

This is the well-documented Apple Silicon Ollama wall: the MLX runner
serializes through one GPU regardless of how many requests Ollama accepts in
parallel. Adding workers stretches each call's wait time but doesn't add
throughput. The NUM_PARALLEL=8 run also fell through tier 1 → 2 → 3 trying to
beat the time budget.

**Conclusion: NUM_PARALLEL=4 is the right setting for this hardware. Do not
bump it.**

### Result 4 — Per-call latency on M4 Pro is the floor

~47 sec per AI call at 4 concurrent on M4 Pro (273 GB/s memory bandwidth).
This number is the lever to attack with hardware. Every option in section 3
below is a per-call latency reduction.

### Code change kept

Added `AMG_AI_PARALLEL_WORKERS` env override to
[amg/config.py](../amg/config.py) line 76. Useful permanent operational
lever — lets future tests change parallelism without editing config. Default
stays 4. Set this to match `OLLAMA_NUM_PARALLEL` on whatever Ollama server
you're pointed at.

### Restore commands (already executed — for reference only)

```bash
# Restore parallelism to original
launchctl setenv OLLAMA_NUM_PARALLEL 4
brew services restart ollama
# Verify
launchctl getenv OLLAMA_NUM_PARALLEL  # → 4
curl -s http://127.0.0.1:11434/api/ps  # → qwen2.5vl:7b loaded
```

---

## 2. Runpod trial recipe (Option 2 from the plan)

Goal: confirm a 4090 actually delivers 3-4x scoring speedup on YOUR pipeline
before spending real money on hardware. Total cost of this trial: ~$0.50,
~30 min of your time.

### Step 1 — Account + credit

1. Sign up at https://runpod.io
2. Add $10 credit (way more than this trial needs; covers future tests too)
3. Generate an SSH public key on this Mac if you don't have one:
   ```bash
   test -f ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
   cat ~/.ssh/id_ed25519.pub  # copy this
   ```
4. Add the public key under Runpod → Account → Settings → SSH Public Keys

### Step 2 — Spin up a 4090 pod

Web console → "Deploy" → filter Community Cloud → RTX 4090 → pick a host
with ≥50 GB disk. Template: **"RunPod Pytorch 2.4"** (has CUDA + tools
pre-installed). Click Deploy. Cost meter starts (~$0.40/hr).

When it's running, click "Connect" and copy the SSH command. Looks like:
```
ssh root@<podip> -p <port> -i ~/.ssh/id_ed25519
```

### Step 3 — Install Ollama + pull the model on the pod

SSH in, then:

```bash
# Inside the pod
curl -fsSL https://ollama.com/install.sh | sh
# Tell Ollama to listen on all interfaces (so your Mac can reach it via the proxied port)
export OLLAMA_HOST=0.0.0.0:11434
export OLLAMA_NUM_PARALLEL=8        # 4090 has 24GB VRAM, can handle 8 easily
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KEEP_ALIVE=1h
nohup ollama serve > /tmp/ollama.log 2>&1 &
sleep 3
ollama pull qwen2.5vl:7b            # ~5 GB, takes 1-2 min on datacenter bandwidth
ollama list                         # verify
```

### Step 4 — Expose Ollama to your Mac

In the Runpod web console for this pod, expose **port 11434** as a TCP proxy.
Runpod gives you a public URL like `https://<podid>-11434.proxy.runpod.net`
(HTTPS) or a TCP host:port pair.

Quick test from your Mac:
```bash
curl https://<podid>-11434.proxy.runpod.net/api/version
# → {"version":"0.22.1"}
```

### Step 5 — Point AMG at the remote Ollama

On your Mac, in a fresh terminal (NOT the one running your local Ollama-using stuff):

```bash
cd ~/AMG_OS
source venv/bin/activate
export OLLAMA_HOST="<podid>-11434.proxy.runpod.net:443"   # or TCP equivalent
export AMG_AI_PARALLEL_WORKERS=8                          # match pod's OLLAMA_NUM_PARALLEL
# Sanity check the client points at the right place:
curl -s "http://${OLLAMA_HOST}/api/version" || curl -s "https://${OLLAMA_HOST}/api/version"

amg process "/Users/mariorivera/AMG_Processing/incoming/YasminaBrady/4 BG - bath teasing scene"
```

> **Note on `OLLAMA_HOST` URL form:** [amg/config.py](../amg/config.py)
> line 45-46 builds `http://{OLLAMA_HOST}/api/chat`. If Runpod gives you
> HTTPS, you may need to either (a) use the raw TCP host:port if Runpod
> exposes one, or (b) patch the URL builder for one run. Confirm with the
> sanity-check `curl` above before launching `amg process`.

### Step 6 — Read the numbers

Same `parallelism_factor` and `mean_call_sec` log lines. Compare to baseline:

| Metric | M4 Pro baseline | Runpod 4090 (your run) |
|---|---|---|
| mean_call_sec | 46.9 | _____ |
| parallelism_factor | 3.87 | _____ |
| Total wall time | 619 sec | _____ |
| AI scoring sub-time | 354 sec | _____ |

Plus: time the **upload** of the source video to the pod if you copied it
across (use `rsync -avh --progress` over SSH or Runpod's S3-compatible
volume mount).

### Step 7 — Tear down the pod

Web console → pod → "Stop" or "Terminate". Stopped pods bill at storage rate
only (~$0.005/hr). Terminated pods bill nothing further. **Always terminate
when done with the trial.** Total spend should be $0.30-0.80.

### What to expect

If 4090 + NUM_PARALLEL=8 works as advertised:
- mean_call_sec should drop to ~10-15 sec (3-4.5x reduction)
- AI scoring sub-time should drop to ~80-110 sec (vs 354 today)
- Wall time should drop to ~340-400 sec (~1.5-1.8x — capped because
  non-AI parts of the pipeline don't speed up)

If you see something weirder (e.g. mean_call_sec dropped only to 30 sec),
the pod may be a shared-tenant instance. Try a Secure Cloud 4090 (~$0.69/hr)
for a clean read.

---

## 3. Decision sheet (Option 3 from the plan)

Fill this in after running the Runpod trial. The decision logic is here so
future-you doesn't have to re-derive it.

### Numbers to plug in

```
[A] M4 Pro baseline wall:           619 sec  (measured 2026-05-05)
[B] Runpod 4090 wall:               ___ sec  (from trial)
[C] Speedup:                        [A]/[B] = ___
[D] Your batches per month:         ___      (current pace; full 10-scene batch ~3h on Mac)
[E] Hours saved per batch:          (3.2 hr × (1 - 1/[C])) = ___ hr
[F] Hours saved per month:          [E] × [D] = ___ hr
```

### Decision rules

- **If [C] < 1.8**: hardware swap isn't giving enough lift. Stay on M4 Pro,
  revisit when you'd otherwise upgrade the laptop. Skip both Option 2 and 3.
- **If [C] ≥ 1.8 AND [D] < 4 batches/month**: stay on Runpod. Recurring cost
  ~$0.40 × [D] = under $2/month. Less than a coffee.
- **If [C] ≥ 1.8 AND [D] ≥ 4 batches/month AND you're comfortable
  troubleshooting Linux**: build the home GPU box (Option 3). Used 3090 path
  is the cost-optimal one.
- **If [C] ≥ 1.8 AND [D] ≥ 4 batches/month AND you'd rather not run
  another machine**: stay on Runpod. The recurring cost is small; the
  operational simplicity is real.
- **Skip Mac Studio (Option 4) regardless** unless you'd buy one for other
  reasons. It's the worst $/speedup of the four options.

### Operational reminders for either path

If you go Runpod long-term:
- Encrypt source videos in flight (`rclone crypt` over `sftp:` to the pod's
  `/workspace/`). Wipes when pod terminates.
- Consider a Network Volume (~$0.10/GB/month) so the model doesn't re-pull
  every spin-up.
- Build a `scripts/runpod_up.sh` / `runpod_down.sh` pair to avoid manually
  installing Ollama every time.

If you go home GPU box:
- Tailscale (free for solo) is the cleanest way to reach the box from your
  Mac without exposing Ollama to the internet.
- Set `OLLAMA_HOST=<tailscale-ip>:11434` once in `~/.zshrc`; never think
  about it again.
- Log onto the box quarterly to update Ollama. That's it.

---

## 4. What's next (after Runpod measurement)

Whichever direction you choose, two pipeline-side improvements are worth
considering once the GPU question is answered:

1. **The 132-sec tail** at the end of every run (post-cover-save, before
   final summary) — this is title generation + distribution-readiness check
   running serially. With AI scoring 3-4x faster, this tail becomes a much
   bigger fraction of total wall. Worth profiling separately.
2. **Quota-fill rebuild from `TOMORROW.md`** — orthogonal to throughput;
   reduces total AI calls per scene by ~3x by structuring output around
   shot categories. Stacks multiplicatively with hardware speedup.
