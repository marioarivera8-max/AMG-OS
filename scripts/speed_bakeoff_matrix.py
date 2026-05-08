#!/usr/bin/env python3
"""Print controlled speed bake-off configurations for AMG cloud runs."""
from __future__ import annotations

import argparse
import json


MATRIX = [
    {
        "name": "quality-4090-7b-p6",
        "env": {
            "AMG_PROCESSING_PROFILE": "quality",
            "AMG_RUNPOD_GPU_TYPE": "NVIDIA GeForce RTX 4090",
            "AMG_RUNPOD_OLLAMA_NUM_PARALLEL": "6",
            "AMG_RUNPOD_AI_PARALLEL_WORKERS": "6",
        },
    },
    {
        "name": "fast-4090-7b-p6",
        "env": {
            "AMG_PROCESSING_PROFILE": "fast",
            "AMG_RUNPOD_GPU_TYPE": "NVIDIA GeForce RTX 4090",
            "AMG_RUNPOD_OLLAMA_NUM_PARALLEL": "6",
            "AMG_RUNPOD_AI_PARALLEL_WORKERS": "6",
        },
    },
    {
        "name": "turbo-4090-7b-p8",
        "env": {
            "AMG_PROCESSING_PROFILE": "turbo",
            "AMG_RUNPOD_GPU_TYPE": "NVIDIA GeForce RTX 4090",
            "AMG_RUNPOD_OLLAMA_NUM_PARALLEL": "8",
            "AMG_RUNPOD_AI_PARALLEL_WORKERS": "8",
        },
    },
    {
        "name": "turbo-4090-3b-p8",
        "env": {
            "AMG_PROCESSING_PROFILE": "turbo",
            "AMG_RUNPOD_GPU_TYPE": "NVIDIA GeForce RTX 4090",
            "AMG_RUNPOD_OLLAMA_NUM_PARALLEL": "8",
            "AMG_RUNPOD_AI_PARALLEL_WORKERS": "8",
            "AMG_VISION_MODEL_OVERRIDE": "qwen2.5vl:3b",
        },
    },
    {
        "name": "fast-l40s-7b-p8",
        "env": {
            "AMG_PROCESSING_PROFILE": "fast",
            "AMG_RUNPOD_GPU_TYPE": "NVIDIA L40S",
            "AMG_RUNPOD_OLLAMA_NUM_PARALLEL": "8",
            "AMG_RUNPOD_AI_PARALLEL_WORKERS": "8",
        },
    },
    {
        "name": "turbo-h100-7b-p12",
        "env": {
            "AMG_PROCESSING_PROFILE": "turbo",
            "AMG_RUNPOD_GPU_TYPE": "NVIDIA H100 SXM",
            "AMG_RUNPOD_OLLAMA_NUM_PARALLEL": "12",
            "AMG_RUNPOD_AI_PARALLEL_WORKERS": "12",
        },
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.json:
        print(json.dumps(MATRIX, indent=2))
        return 0

    print("AMG speed bake-off matrix")
    print("=" * 80)
    for item in MATRIX:
        print(f"\n{item['name']}")
        for key, value in item["env"].items():
            print(f"  {key}={value}")
    print("\nRun each candidate on the same scene, then compare with scripts/scene_timing_report.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
