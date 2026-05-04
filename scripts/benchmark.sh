#!/bin/bash
#
# AMG OS v11 Benchmark Script
# ============================
#
# Measures end-to-end processing time on a sample scene.
# Use to validate v11 hits performance targets (60-120 sec/scene on M4 Pro).
#

set -e

AMG_OS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "$1" ]]; then
    echo "Usage: $0 /path/to/test/scene/"
    echo ""
    echo "Pass a folder containing a single test video."
    echo "Recommended: a representative scene (~20-30 min, 1080p or 4K)."
    exit 1
fi

SCENE_PATH="$1"

if [[ ! -d "$SCENE_PATH" ]]; then
    echo "Scene path does not exist: $SCENE_PATH"
    exit 1
fi

echo "================================================================"
echo "AMG OS v11 Benchmark"
echo "================================================================"
echo "Scene: $SCENE_PATH"
echo ""

cd "$AMG_OS_ROOT"
source venv/bin/activate

# Pre-warm Ollama
echo "Pre-warming Ollama (loading model into memory)..."
curl -s http://127.0.0.1:11434/api/generate -d '{
    "model": "qwen2.5vl:7b",
    "prompt": "ready",
    "stream": false,
    "keep_alive": "24h"
}' > /dev/null
echo "Model warm."
echo ""

# Run benchmark
START=$(date +%s)
python -m amg.cli process "$SCENE_PATH"
END=$(date +%s)
DURATION=$((END - START))

echo ""
echo "================================================================"
echo "Benchmark complete: ${DURATION}s"
echo "================================================================"
echo ""

# Targets
if (( DURATION < 60 )); then
    echo "⚡ EXCELLENT — under 60s target"
elif (( DURATION < 120 )); then
    echo "✓ ON TARGET — within 60-120s budget"
elif (( DURATION < 300 )); then
    echo "⚠ SLOW — over 120s target. Check Ollama env vars (OLLAMA_MLX, etc.)"
else
    echo "✗ FAIL — over 300s. Significant misconfiguration."
    echo "  Run: amg verify"
fi
