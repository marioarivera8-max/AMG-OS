# Pod-reuse design (warm pool) — `RunpodBackend._run_with_lifecycle`

**Status:** Designed, not yet implemented. Next iteration.

**Why:** Per-job pod cold-start is **~3.2 min** (provision ~60s → Ollama
+ qwen2.5vl model load ~60-90s → image-pull on cache miss). For the
operator running back-to-back jobs (typical batch use), this is dead
time we pay on every job. Reusing the same warm pod between jobs
amortizes the cold-start across all jobs in the working session.

**Why not first:** It only saves the 3.2 min cold-start. The 7.3 min
on-pod processing is the bigger speed lever and we don't have
phase-level timings yet. Ship phase-timing capture (already done in
this PR) → measure → decide where the next optimization lives.

## Constraints

1. **One warm pod at a time.** Ollama loads a single model (~16 GB
 VRAM); two concurrent jobs on one pod would compete for the GPU and
 slow each other. Serialize.
2. **Idle teardown.** Operator goes to lunch; we should NOT keep a
 4090 alive at $0.40/hr. Default idle timeout: 10 minutes, env-
 overrideable.
3. **Spec-compatible reuse only.** If a future enhancement lets the
 operator pick a model per-job (qwen2.5vl:3b vs 7b vs MiniCPM), only
 reuse a pod whose spec matches the new job's spec. v1: single spec,
 always-compatible.
4. **Crash safety.** If the controller crashes with a warm pod active,
 next start must NOT think the pod is gone forever. Persist the
 warm-pod-id to disk (state file under `DATA_DIR/cloud/warm_pods.json`)
 and reconnect on boot.
5. **Concurrent-job queueing.** If a second job submission arrives
 while a first is still running on the warm pod, queue the second
 (don't spin a second pod just because the first is busy). The
 simplest implementation is a per-pod `threading.Lock` — second job
 blocks until the first releases.

## Surface

```python
class WarmPodPool:
    def acquire(self, spec: PodSpec) -> WarmPod: ...
    def release(self, warm_pod: WarmPod) -> None: ...
    def shutdown(self) -> None: ...

class WarmPod:
    pod_id: str
    spec: PodSpec
    acquired_at: float
    busy: bool # set while a job is using it
    last_used_at: float
```

`RunpodBackend.__init__` takes an optional `pool: WarmPodPool = None`;
when None, behaves exactly as today (one pod per job, terminate after).
When set, `_run_with_lifecycle` becomes:

```python
warm = self._pool.acquire(self._spec) # blocks if pool is busy
try:
    self._wait_for_pod_worker_ready(warm.pod_id, ...)
    job_id = submit(warm.pod_id)
    result = self._wait_for_job(warm.pod_id, job_id, ...)
    paths = self._download_and_extract(warm.pod_id, job_id, ...)
    return result
finally:
    self._pool.release(warm) # marks idle; pool decides teardown
```

Lifecycle changes:
* Terminate-in-`finally` is moved into the pool's idle watchdog.
* `_wait_for_pod_worker_ready` is now idempotent — if the pod is
 already warm, /healthz returns 200 immediately, no waiting penalty.

## Idle watchdog

A daemon thread inside `WarmPodPool` checks every 30s. For each idle
pod where `now - last_used_at > idle_timeout_sec`, it:
1. Acquires the pod's lock (blocks new jobs from picking it up).
2. Calls `client.terminate_pod(pod_id)`.
3. Removes the pod from the pool.
4. Persists the updated state to disk.

## Configuration (new env vars)

| Var | Default | Purpose |
|---|---|---|
| `AMG_RUNPOD_POOL_ENABLED` | `false` | Master switch — opt in to pod-reuse |
| `AMG_RUNPOD_POOL_IDLE_TIMEOUT_SEC` | `600` | 10 min idle before teardown |
| `AMG_RUNPOD_POOL_STATE_PATH` | `$DATA_DIR/cloud/warm_pods.json` | Crash-safety state file |

**Default off.** Operator must opt in. Reason: if the operator forgets
to terminate the controller while a pod is warm, we bill until the
idle timeout fires. Better to make the cost-impact decision explicit.

## Tests to write

1. `test_warm_pool_reuses_existing_pod_for_compatible_spec` — fake
 client returns the same pod_id; second job doesn't trigger
 `provision_pod`.
2. `test_warm_pool_idle_teardown_after_timeout` — fast-forward fake
 clock past `idle_timeout_sec`, assert `terminate_pod` was called.
3. `test_warm_pool_concurrent_jobs_serialize` — two jobs submitted in
 parallel, one waits for the other; pod created exactly once.
4. `test_warm_pool_persists_state_across_restart` — pool writes state
 to disk on `release`; new `WarmPodPool` reads it on init.
5. `test_warm_pool_disabled_falls_back_to_per_job_lifecycle` — when
 `AMG_RUNPOD_POOL_ENABLED=false`, behavior is identical to today.

## Open questions for the operator

1. Default idle timeout: 10 min (current proposal) or 5 / 30? Trade-
 off is dollars-per-hour vs. cold-start pain on the next job.
2. Should the UI surface "warm pod active" status? Useful for the
 operator to see what's costing money. Trivial to add to the home
 page.
3. Manual "kill warm pod now" button? For the case where the operator
 finishes a session and wants to free the GPU immediately.

## Future work (post-v1)

* Multi-pod pool (parallel processing of distinct scenes).
* Spec-aware reuse (match on gpu_type + image + env_hash).
* Pre-warm: provision a pod ahead of an expected batch even before
 the first scene is queued.
