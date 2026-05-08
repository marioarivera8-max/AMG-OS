# Segment-Parallel Processing Evaluation

This is the stretch path for getting hot-pod processing near 3 minutes on
large 4K scenes. Do not implement this before the profile, single-pass scan,
decode, and model/GPU bake-offs have data.

## Concept

Split one scene into time windows, score each window independently, then merge
the candidate pool before quota fill and output.

```text
scene.mp4
  -> segment 1: 0-25%
  -> segment 2: 25-50%
  -> segment 3: 50-75%
  -> segment 4: 75-100%
  -> merge candidates
  -> dedup globally
  -> quota fill
  -> save covers/contact sheet once
```

## Options

### One Pod, Concurrent Segments

Run segment scanners concurrently inside one worker process.

Pros:

- No extra Runpod pod orchestration.
- Can share local downloaded source.
- Lower cost than multi-pod.

Cons:

- One GPU still limits AI inference.
- Multiple decode streams may compete for CPU/disk.
- Needs careful caps to avoid making contention worse.

### Multiple Pods, One Segment Each

Controller creates N pods and submits one segment per pod.

Pros:

- Best wall-time reduction if inference dominates.
- Lets a long scene use multiple GPUs temporarily.

Cons:

- Higher cost.
- More failure modes.
- Requires new result-merging protocol in `amg/cloud/job_backend.py`.
- Every pod may download the same source unless source staging is improved.

## Required Code Shape

- Add segment parameters to `process_scene` or a new scan-only entry point:
  `segment_start_sec`, `segment_end_sec`, `segment_id`.
- Make scanner outputs serializable without writing final covers.
- Add a merge function that deduplicates candidates across segment boundaries.
- Run `select_quota_fill` and `save_covers` once after merge.
- Decision logs must record segment timings and total cost.

## Acceptance Criteria

- Same source scene processed with normal fast profile and segment-parallel mode.
- Segment mode must reduce wall time by at least 40% before it is worth the
  extra complexity.
- Final cover set must not contain obvious duplicates around segment edges.
- Mario must visually approve quality before enabling this by default.
