#!/usr/bin/env python3
"""
Model bake-off harness — v11.1.4 adapted, multi-scene.

Cherry-picked from v11.2's bake_off.py and adapted for v11.1.4's codebase
plus Mario's first-round model picks. The v11.2 harness was never actually
run (its bundle was rolled back before the bake-off was used).

Pulls each model via Ollama, runs `amg process` on every selected scene
with that model active (via AMG_VISION_MODEL_OVERRIDE env var), moves the
output into data/bake_off/<scene-id>/<model_slug>/, and writes both
per-scene SUMMARY.txt files and a top-level SUMMARY.txt with a model x
scene matrix.

Default scenes (when --scenes not given):
  - 4 BG - bath teasing scene           (10:34, fast canonical short test)
  - 7 BBBBBBG - birthday gangbang...    (46:47, large-cluster stress test)

Default models (currently hardcoded in MODELS):
  - qwen2.5vl:7b   (current v11.1.4 baseline — control)
  - qwen2.5vl:3b   (smaller sibling — speed vs quality)
  - minicpm-v:8b   (different family — DUAL hallucination axis)

Usage:
    python scripts/bake_off.py                          # default scenes, all models
    python scripts/bake_off.py --models qwen2.5vl_3b minicpm-v_8b
    python scripts/bake_off.py --scenes /path/scene1 /path/scene2

Result layout:
    ~/AMG_OS/data/bake_off/
        SUMMARY.txt                         <- cross-scene matrix
        <scene-id-1>/
            SUMMARY.txt                     <- per-scene side-by-side
            qwen2.5vl_7b/   covers_output/, decision_log.json
            qwen2.5vl_3b/   covers_output/, decision_log.json
            minicpm-v_8b/   covers_output/, decision_log.json
        <scene-id-2>/
            ... same structure ...

Notes:
- Scene-major loop (outer = scenes, inner = models). Per-scene results are
  written as soon as that scene completes, so eyeball comparison can start
  while later scenes are still running.
- AI counter fields (ai_calls_total, frames_extracted) in decision logs
  will be 0 — the v11.2 counter aggregation work was not cherry-picked
  into v11.1.4 (separate from PyAV decode). Trust covers + wall_clock.
- OLLAMA_MAX_LOADED_MODELS=1 means each model swap unloads + reloads
  (~30-60s overhead per switch). With scene-major ordering and N models
  on M scenes, that's M*(N-1) extra model loads. Acceptable price for
  per-scene summary clarity; revisit if M*N gets large.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Defaults — edit here for sticky changes
# ---------------------------------------------------------------------------

# First-round model picks (Mario's call from planning):
#   - baseline qwen2.5vl:7b (control)
#   - qwen2.5vl:3b   (smaller sibling — speed vs quality)
#   - minicpm-v:8b   (different family — DUAL hallucination axis)
# Deferred to round 2: llava-llama3:8b (control if family swap doesn't help)
# Skipped: qwen2.5vl:32b (24GB Mac memory pressure), internvl (tag uncertain)
MODELS = [
    {
        "slug": "qwen2.5vl_7b",
        "ollama_tag": "qwen2.5vl:7b",
        "approx_size_gb": 6,
        "notes": "Current v11.1.4 baseline (qwen2.5vl 7B)",
    },
    {
        "slug": "qwen2.5vl_3b",
        "ollama_tag": "qwen2.5vl:3b",
        "approx_size_gb": 3,
        "notes": "Smaller sibling — tests speed vs quality tradeoff. Same family as baseline.",
    },
    {
        "slug": "minicpm-v_8b",
        "ollama_tag": "minicpm-v:8b",
        "approx_size_gb": 5,
        "notes": "Different family (Tsinghua/OpenBMB). Tests whether DUAL hallucination is family-specific.",
    },
]

# Default scene set if --scenes is not passed. Hardcoded so the common case
# is just `python scripts/bake_off.py` with no flags.
DEFAULT_SCENES = [
    Path.home() / "AMG_Processing/incoming/YasminaBrady/4 BG - bath teasing scene",
    Path.home() / "AMG_Processing/incoming/YasminaBrady/7 BBBBBBG - birthday gangbang- valentino, jesus reyes, papi,jimmy, raul (Yasmina Khan Birthday Gangbang Triple penetration and six creampie)",
]


# ---------------------------------------------------------------------------
# Subprocess + Ollama helpers
# ---------------------------------------------------------------------------


def run(cmd: List[str], env: Optional[dict] = None, check: bool = True) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, env=full_env, check=check, text=True)


def ollama_pull(tag: str) -> bool:
    print(f"\n→ Pulling Ollama model: {tag}")
    try:
        run(["ollama", "pull", tag])
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ✗ Failed to pull {tag}: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Output dir + decision log helpers
# ---------------------------------------------------------------------------


def find_v11_output_dir(scene_dir: Path) -> Optional[Path]:
    """Find the CANONICAL _amg_v11 output dir, ignoring preserved siblings.

    Tightened from v11.2's "*_amg_v11*" glob, which now matches preserved
    baselines (_amg_v11_1_2, _amg_v11_1_4_first, etc.) created by tonight's
    rename convention.
    """
    for p in scene_dir.iterdir():
        if p.is_dir() and p.name.endswith("_amg_v11"):
            return p
    return None


def find_latest_decision_log(amg_os_root: Path, scene_id: str) -> Optional[Path]:
    logs_dir = amg_os_root / "data" / "decision_logs"
    if not logs_dir.exists():
        return None
    safe = "".join(c if c.isalnum() else "_" for c in scene_id)[:80]
    candidates = sorted(
        logs_dir.glob(f"{safe[:40]}*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_decision_log_summary(log_path: Path) -> dict:
    try:
        with open(log_path) as f:
            data = json.load(f)
        return {
            "covers_delivered": data.get("outcomes", {}).get("covers_delivered", 0),
            "top_pick_score": data.get("outcomes", {}).get("top_pick_score", 0),
            "scores_distribution": data.get("outcomes", {}).get("scores_distribution", {}),
            "total_duration_sec": data.get("execution", {}).get("total_duration_sec", 0),
            # These two will be 0 in v11.1.4 — counter aggregation not cherry-picked.
            "ai_calls_total": data.get("resource_usage", {}).get("ai_calls_total", 0),
            "frames_extracted": data.get("resource_usage", {}).get("frames_extracted", 0),
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Per-scene + per-model execution
# ---------------------------------------------------------------------------


def process_scene_with_model(
    scene_dir: Path,
    model: dict,
    scene_bake_root: Path,
    amg_os_root: Path,
) -> dict:
    print(f"\n{'='*64}")
    print(f"BAKE-OFF: {scene_dir.name[:50]}")
    print(f"          {model['slug']} ({model['ollama_tag']})")
    print(f"          {model['notes']}")
    print(f"{'='*64}")

    env = {"AMG_VISION_MODEL_OVERRIDE": model["ollama_tag"]}
    start = time.time()
    try:
        run(["amg", "process", str(scene_dir)], env=env)
    except subprocess.CalledProcessError as e:
        return {"slug": model["slug"], "error": f"process failed: {e}", "wall_clock_sec": time.time() - start}
    wall_clock = time.time() - start

    out_v11 = find_v11_output_dir(scene_dir)
    target_dir = scene_bake_root / model["slug"]
    target_dir.mkdir(parents=True, exist_ok=True)

    if out_v11:
        moved_dir = target_dir / "covers_output"
        if moved_dir.exists():
            shutil.rmtree(moved_dir)
        shutil.move(str(out_v11), str(moved_dir))
        print(f"  → moved {out_v11.name} → {moved_dir}")

    log_path = find_latest_decision_log(amg_os_root, scene_dir.name)
    if log_path:
        shutil.copy(log_path, target_dir / "decision_log.json")
        summary = load_decision_log_summary(log_path)
    else:
        summary = {"error": "decision log not found"}

    summary["slug"] = model["slug"]
    summary["ollama_tag"] = model["ollama_tag"]
    summary["wall_clock_sec"] = wall_clock
    return summary


# ---------------------------------------------------------------------------
# Summary writers
# ---------------------------------------------------------------------------


def _format_secs(s: float) -> str:
    """Format seconds as Mm:SSs."""
    if s <= 0:
        return "—"
    m, sec = divmod(int(s), 60)
    return f"{m}m{sec:02d}s"


def write_per_scene_summary(scene_bake_root: Path, scene_id: str, results: List[dict]) -> None:
    summary_path = scene_bake_root / "SUMMARY.txt"
    lines = []
    lines.append("=" * 80)
    lines.append(f"BAKE-OFF SUMMARY — {scene_id[:60]}")
    lines.append("=" * 80)
    lines.append("")
    lines.append("Note: ai_calls_total / frames_extracted are 0 in v11.1.4 (counter")
    lines.append("aggregation work from v11.2 was not cherry-picked). Trust covers + wall.")
    lines.append("")
    lines.append(f"{'Model':<20} {'Covers':>7} {'TopScore':>9} {'Wall':>9}")
    lines.append("-" * 80)
    for r in results:
        if "error" in r and "covers_delivered" not in r:
            lines.append(f"{r.get('slug',''):<20} ERROR: {r['error']}")
            continue
        lines.append(
            f"{r.get('slug',''):<20} "
            f"{r.get('covers_delivered',0):>7} "
            f"{r.get('top_pick_score',0):>9.1f} "
            f"{_format_secs(r.get('wall_clock_sec',0)):>9}"
        )
    lines.append("")
    lines.append("Score distributions (frames in each band):")
    for r in results:
        dist = r.get("scores_distribution", {})
        if dist:
            lines.append(f"  {r.get('slug','')}: " + ", ".join(
                f"{k}:{v}" for k, v in dist.items() if v
            ))
    lines.append("")
    lines.append("How to compare:")
    lines.append("  1. Open each model's covers_output/ folder side by side")
    lines.append("  2. Top 5 covers — which model picks better moments?")
    lines.append("  3. GAZE labels in filenames — which model gets DUAL/TRIPLE right?")
    lines.append("  4. Note any obviously bad picks (blurry, off-center, head-cut)")
    lines.append("")
    text = "\n".join(lines)
    summary_path.write_text(text)
    print("\n" + text)


def write_cross_scene_summary(
    bake_off_root: Path,
    scenes: List[Path],
    models: List[dict],
    all_results: dict,
) -> None:
    """Top-level matrix: rows = models, columns = scenes, cells = covers + wall."""
    summary_path = bake_off_root / "SUMMARY.txt"
    lines = []
    lines.append("=" * 80)
    lines.append("CROSS-SCENE BAKE-OFF SUMMARY")
    lines.append("=" * 80)
    lines.append("")
    lines.append("Cell format: <covers> / <wall_time>")
    lines.append("Note: ai_calls_total / frames_extracted are 0 in v11.1.4.")
    lines.append("")

    # Column widths: narrow scene labels (use truncated id)
    scene_labels = [s.name[:18] for s in scenes]
    col_w = max(20, max(len(s) for s in scene_labels) + 2)

    # Header row
    header = f"{'Model':<20}" + "".join(f"{lbl:<{col_w}}" for lbl in scene_labels)
    lines.append(header)
    lines.append("-" * len(header))

    # Body rows
    for m in models:
        slug = m["slug"]
        cells = []
        for scene in scenes:
            r = all_results.get((scene.name, slug))
            if r is None:
                cell = "—"
            elif "error" in r and "covers_delivered" not in r:
                cell = f"ERR ({r.get('wall_clock_sec', 0):.0f}s)"
            else:
                covers = r.get("covers_delivered", 0)
                wall = _format_secs(r.get("wall_clock_sec", 0))
                cell = f"{covers}c / {wall}"
            cells.append(f"{cell:<{col_w}}")
        lines.append(f"{slug:<20}" + "".join(cells))

    lines.append("")
    lines.append("Per-scene summaries (open for score distributions, etc.):")
    for scene in scenes:
        per_scene = bake_off_root / scene.name / "SUMMARY.txt"
        if per_scene.exists():
            lines.append(f"  {per_scene}")
    lines.append("")
    lines.append("Open all contact sheets at once:")
    for scene in scenes:
        for m in models:
            r = all_results.get((scene.name, m["slug"]))
            if r and "error" not in r:
                pat = bake_off_root / scene.name / m["slug"] / "covers_output" / "00_*_contact_sheet.jpg"
                lines.append(f"  open {pat}")
    lines.append("")

    text = "\n".join(lines)
    summary_path.write_text(text)
    print("\n" + text)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Run scenes through multiple vision models")
    parser.add_argument(
        "--scenes",
        nargs="+",
        type=Path,
        default=None,
        help=f"Scene directory paths. Default: {[s.name for s in DEFAULT_SCENES]}",
    )
    parser.add_argument(
        "--models",
        nargs="*",
        choices=[m["slug"] for m in MODELS],
        help="Subset of models to test (default: all)",
    )
    parser.add_argument(
        "--amg-os-root",
        default=os.environ.get("AMG_OS_ROOT", str(Path.home() / "AMG_OS")),
        help="AMG_OS install root",
    )
    parser.add_argument(
        "--skip-pull",
        action="store_true",
        help="Skip 'ollama pull' for each model (assume already cached)",
    )
    args = parser.parse_args()

    # Resolve scenes
    scenes: List[Path] = [Path(p).resolve() for p in (args.scenes or DEFAULT_SCENES)]
    missing = [s for s in scenes if not s.is_dir()]
    if missing:
        for m in missing:
            print(f"✗ Scene directory not found: {m}", file=sys.stderr)
        sys.exit(1)

    amg_os_root = Path(args.amg_os_root).resolve()
    if not amg_os_root.is_dir():
        sys.exit(f"AMG_OS root not found: {amg_os_root}")

    # Resolve models
    selected_models = MODELS if not args.models else [m for m in MODELS if m["slug"] in args.models]

    bake_off_root = amg_os_root / "data" / "bake_off"
    bake_off_root.mkdir(parents=True, exist_ok=True)

    print(f"Bake-off root:    {bake_off_root}")
    print(f"Scenes ({len(scenes)}):       {[s.name[:50] for s in scenes]}")
    print(f"Models ({len(selected_models)}):       {[m['slug'] for m in selected_models]}")
    total_dl = sum(m["approx_size_gb"] for m in selected_models)
    print(f"Approx download:  {total_dl} GB" + (" (skipping pull)" if args.skip_pull else ""))
    print()

    # Pull models upfront so any pull failure surfaces before we burn time on scoring
    if not args.skip_pull:
        for m in selected_models:
            if not ollama_pull(m["ollama_tag"]):
                print(f"\n✗ Stopping — could not pull {m['ollama_tag']}", file=sys.stderr)
                sys.exit(1)

    # Scene-major: complete one scene's full model sweep before moving to next
    all_results: dict = {}  # {(scene_id, model_slug): result_dict}
    for scene in scenes:
        scene_bake_root = bake_off_root / scene.name
        scene_bake_root.mkdir(parents=True, exist_ok=True)
        per_scene_results = []
        for model in selected_models:
            r = process_scene_with_model(scene, model, scene_bake_root, amg_os_root)
            all_results[(scene.name, model["slug"])] = r
            per_scene_results.append(r)
        write_per_scene_summary(scene_bake_root, scene.name, per_scene_results)

    # Cross-scene matrix at the end
    write_cross_scene_summary(bake_off_root, scenes, selected_models, all_results)


if __name__ == "__main__":
    main()
