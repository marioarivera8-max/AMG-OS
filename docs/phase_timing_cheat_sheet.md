# AMG Phase Timing Cheat Sheet

Quick operator guide for reading pipeline speed in live runs.

Use this with:
- UI dashboard recent runs / slowest phases
- decision logs (`execution.phases`)

## How to read this

- **Green**: expected for this phase
- **Yellow**: slower than normal; watch trend
- **Red**: investigate now

These are **default thresholds** for scene-4 style iteration while no timing ledger exists yet.

## Per-Phase thresholds (seconds)

| Phase | Green | Yellow | Red | What to tune first |
|---|---:|---:|---:|---|
| `calibration` | <= 20 | 21-40 | > 40 | source decode/backend sanity |
| `tier_scan` | <= 240 | 241-420 | > 420 | tier intervals, CV gates, dedup |
| `finish_hunter` | <= 60 | 61-120 | > 120 | hunter interval / early-stop |
| `buildup_hunter` | <= 60 | 61-120 | > 120 | hunter interval / early-stop |
| `cluster` | <= 90 | 91-180 | > 180 | cluster top-N/windows |
| `floor_enforcement` | <= 45 | 46-90 | > 90 | upstream candidate scarcity |
| `position_classifier` | <= 35 | 36-70 | > 70 | classifier candidate cap |
| `quota_fill` | <= 5 | 6-15 | > 15 | candidate list size growth |
| `output` | <= 75 | 76-150 | > 150 | cover fetch/polish/provided thumbs |
| `scene_insight` | <= 25 | 26-50 | > 50 | AI responsiveness (non-fatal) |
| `soft_thumbnail` | <= 30 | 31-60 | > 60 | sample_count / optional toggle |

## Auto-calibrated thresholds (from your run ledger)

Run:

```bash
amg timing-calibrate --update-doc
```

<!-- AUTO_THRESHOLD_TABLE_START -->
_Auto-calibrated table not generated yet. Run `amg timing-calibrate --update-doc` after a few scenes._
<!-- AUTO_THRESHOLD_TABLE_END -->

## Fast diagnosis sequence

1. **Check `tier_scan` first** (largest normal cost center).
2. Compare hunters (`finish_hunter` + `buildup_hunter` + `cluster`) vs `tier_scan`.
3. If `floor_enforcement` is high, treat as **quality scarcity** not just speed.
4. If `output` spikes, inspect nearby polish + provided thumbnail scoring.
5. Confirm total runtime remains below hard budget behavior expectations.

## Knob priority (least risk first)

1. Increase tier/hunter intervals modestly.
2. Reduce cluster breadth (`top_n`, windows).
3. Tighten CV gates slightly (dark/sharpness/motion).
4. Reduce optional extras during iteration (`soft_thumbnail`, provided thumbs scoring).

## Recalibrate thresholds from your own data

Once `data/logs/run_timings.jsonl` exists, derive thresholds from percentiles:

- **Green upper bound** = p50
- **Yellow upper bound** = p75
- **Red** = > p90

Run this from repo root:

```bash
python - <<'PY'
import json
from pathlib import Path
from collections import defaultdict

p = Path("data/logs/run_timings.jsonl")
if not p.exists():
    print("No run timing ledger found at data/logs/run_timings.jsonl")
    raise SystemExit(1)

vals = defaultdict(list)
for line in p.read_text().splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        row = json.loads(line)
    except Exception:
        continue
    phase_map = row.get("phase_durations_sec") or {}
    if not isinstance(phase_map, dict):
        continue
    for phase, sec in phase_map.items():
        try:
            x = float(sec)
        except Exception:
            continue
        if x > 0:
            vals[phase].append(x)

def pct(arr, p):
    arr = sorted(arr)
    i = (len(arr) - 1) * p
    lo = int(i)
    hi = min(lo + 1, len(arr) - 1)
    w = i - lo
    return arr[lo] * (1 - w) + arr[hi] * w

for phase in sorted(vals):
    a = vals[phase]
    print(
        f"{phase}: green<= {pct(a,0.50):.1f}s | "
        f"yellow<= {pct(a,0.75):.1f}s | red> {pct(a,0.90):.1f}s | "
        f"n={len(a)} max={max(a):.1f}s"
    )
PY
```

## Notes

- For long videos (30m+), expect `tier_scan` and hunter phases to scale most.
- `scene_insight` and `soft_thumbnail` are best-effort/non-fatal; treat their slowness separately from core cover throughput.
