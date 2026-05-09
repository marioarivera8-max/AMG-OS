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
        "name": "fast-h100-7b-p8-lowres",
        "env": {
            "AMG_PROCESSING_PROFILE": "fast",
            "AMG_RUNPOD_GPU_TYPE": "NVIDIA H100 80GB HBM3",
            "AMG_RUNPOD_OLLAMA_NUM_PARALLEL": "8",
            "AMG_RUNPOD_AI_PARALLEL_WORKERS": "8",
            "AMG_STREAMING_LOW_RES_ANALYSIS": "1",
            "AMG_STREAMING_SEGMENT_COUNT": "1",
        },
    },
    {
        "name": "fast-h100-7b-p12-lowres",
        "env": {
            "AMG_PROCESSING_PROFILE": "fast",
            # Runpod's gpuTypeId for H100 SXM 80GB. The display name is
            # "H100 SXM" but the actual id (returned by the GraphQL gpuTypes
            # query) is "NVIDIA H100 80GB HBM3". Using the display name as
            # the id is rejected at provision time.
            "AMG_RUNPOD_GPU_TYPE": "NVIDIA H100 80GB HBM3",
            "AMG_RUNPOD_OLLAMA_NUM_PARALLEL": "12",
            "AMG_RUNPOD_AI_PARALLEL_WORKERS": "12",
            "AMG_STREAMING_LOW_RES_ANALYSIS": "1",
            "AMG_STREAMING_SEGMENT_COUNT": "1",
        },
    },
    {
        "name": "fast-h100-7b-p12-seg4",
        "env": {
            "AMG_PROCESSING_PROFILE": "fast",
            "AMG_RUNPOD_GPU_TYPE": "NVIDIA H100 80GB HBM3",
            "AMG_RUNPOD_OLLAMA_NUM_PARALLEL": "12",
            "AMG_RUNPOD_AI_PARALLEL_WORKERS": "12",
            "AMG_STREAMING_LOW_RES_ANALYSIS": "1",
            "AMG_STREAMING_SEGMENT_COUNT": "4",
            "AMG_STREAMING_SEGMENT_MIN_DURATION_SEC": "900",
        },
    },
    {
        "name": "turbo-h100-7b-p16-seg4",
        "env": {
            "AMG_PROCESSING_PROFILE": "turbo",
            "AMG_RUNPOD_GPU_TYPE": "NVIDIA H100 80GB HBM3",
            "AMG_RUNPOD_OLLAMA_NUM_PARALLEL": "16",
            "AMG_RUNPOD_AI_PARALLEL_WORKERS": "16",
            "AMG_STREAMING_LOW_RES_ANALYSIS": "1",
            "AMG_STREAMING_SEGMENT_COUNT": "4",
            "AMG_STREAMING_SEGMENT_MIN_DURATION_SEC": "900",
        },
    },
    {
        "name": "fast-h100-prefetch2-pipeline1",
        "env": {
            "AMG_PROCESSING_PROFILE": "fast",
            "AMG_RUNPOD_GPU_TYPE": "NVIDIA H100 80GB HBM3",
            "AMG_RUNPOD_OLLAMA_NUM_PARALLEL": "12",
            "AMG_RUNPOD_AI_PARALLEL_WORKERS": "12",
            "AMG_STREAMING_LOW_RES_ANALYSIS": "1",
            "AMG_RUNPOD_CONTROLLER_MAX_ACTIVE_JOBS": "2",
            "AMG_RUNPOD_MAX_ACTIVE_PIPELINES": "1",
        },
    },
    {
        "name": "fast-h100-concurrent2-pipeline2",
        "env": {
            "AMG_PROCESSING_PROFILE": "fast",
            "AMG_RUNPOD_GPU_TYPE": "NVIDIA H100 80GB HBM3",
            "AMG_RUNPOD_OLLAMA_NUM_PARALLEL": "16",
            "AMG_RUNPOD_AI_PARALLEL_WORKERS": "8",
            "AMG_STREAMING_LOW_RES_ANALYSIS": "1",
            "AMG_RUNPOD_CONTROLLER_MAX_ACTIVE_JOBS": "2",
            "AMG_RUNPOD_MAX_ACTIVE_PIPELINES": "2",
        },
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--shell", choices=("bash", "powershell"), help="Print env assignment snippets")
    parser.add_argument("--only", help="Only show configs whose name contains this text")
    args = parser.parse_args()

    matrix = MATRIX
    if args.only:
        needle = args.only.lower()
        matrix = [item for item in MATRIX if needle in item["name"].lower()]

    if args.json:
        print(json.dumps(matrix, indent=2))
        return 0

    print("AMG speed bake-off matrix")
    print("=" * 80)
    for item in matrix:
        print(f"\n{item['name']}")
        if args.shell == "powershell":
            for key, value in item["env"].items():
                print(f"  $env:{key}='{value}'")
        elif args.shell == "bash":
            for key, value in item["env"].items():
                print(f"  export {key}='{value}'")
        else:
            for key, value in item["env"].items():
                print(f"  {key}={value}")
    print("\nRun each candidate on the same scene, then compare with scripts/scene_timing_report.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
