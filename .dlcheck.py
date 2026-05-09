"""Drill into the latest Y_B_003 decision log: full execution + outcomes."""
import json
from pathlib import Path

p = sorted(Path("/data/decision_logs").glob("Y_B_003*.json"), key=lambda q: q.stat().st_mtime, reverse=True)[0]
d = json.loads(p.read_text())

print(f"=== {p.name} ===")
print(f"timestamp: {d.get('timestamp_processed')}")
print(f"total_duration_sec: {d['execution']['total_duration_sec']:.1f}")
print()

print("--- ALL phase durations + skipped flags ---")
for name, info in d["execution"]["phases"].items():
    if not isinstance(info, dict):
        continue
    dur = info.get("duration_sec", 0.0) or 0.0
    flags = []
    if info.get("skipped"):
        flags.append(f"SKIPPED({info.get('reason')})")
    if info.get("aborted"):
        flags.append(f"ABORTED({info.get('abort_reason')})")
    print(f"  {name:<25} {dur:>8.2f}s  {' '.join(flags)}")

print()
print("--- stream_scan full ---")
ss = d["execution"]["phases"].get("stream_scan", {})
for k, v in ss.items():
    if isinstance(v, dict):
        print(f"  {k}:")
        for kk, vv in v.items():
            print(f"    {kk}: {vv}")
    else:
        print(f"  {k}: {v}")

print()
print("--- floor_enforcement (if ran) ---")
fe = d["execution"]["phases"].get("floor_enforcement", {})
for k, v in fe.items():
    print(f"  {k}: {v}")

print()
print("--- output phase ---")
out = d["execution"]["phases"].get("output", {})
for k, v in out.items():
    print(f"  {k}: {v}")

print()
print("--- outcomes ---")
oc = d.get("outcomes", {})
for k, v in oc.items():
    if k == "covers":
        print(f"  covers ({len(v)}):")
        for c in v[:5]:
            print(f"    rank={c.get('rank')} score={c.get('score')} tier={c.get('tier')} ts={c.get('timestamp_sec')} type={c.get('type')}")
        if len(v) > 5:
            print(f"    ... and {len(v)-5} more")
    else:
        print(f"  {k}: {v}")
