#!/usr/bin/env bash
set -euo pipefail

ROOT="${AMG_ROOT:-/Users/mariorivera/AMG_OS}"
PYTHON="${PYTHON:-python3}"
TOOL="$ROOT/scripts/amy_delivery.py"
MANIFEST="${1:-}"
DELIVERY_NAME="${2:-}"
WORKERS="${AMG_DVD_WORKERS:-3}"
REMOTE_BASE="${AMG_DVD_REMOTE_BASE:-Amy Deliveries}"
OUT_DIR="${AMG_DVD_OUT_DIR:-$ROOT/deliveries}"

if [[ -z "$MANIFEST" ]]; then
  cat <<USAGE
AMG DVD Scene Organizer

Usage:
  $0 "/path/to/Web VOD - Delivery 6.csv" ["Delivery 6"]

Environment:
  AMG_DVD_WORKERS      Parallel cloud transfers. Default: 3.
  AMG_DVD_REMOTE_BASE  Google Drive base folder. Default: Amy Deliveries.
  AMG_DVD_OUT_DIR      Local metadata/report folder. Default: /Users/mariorivera/AMG_OS/deliveries.

This creates Amy-ready Google Drive delivery folders, uploads spreadsheet
metadata/review files, streams valid scene videos into the matching DVD folders,
then audits cloud layout and video specs.
USAGE
  exit 2
fi

args=(--manifest "$MANIFEST" --out-dir "$OUT_DIR")
if [[ -n "$DELIVERY_NAME" ]]; then
  args+=(--delivery-name "$DELIVERY_NAME")
fi

set +e
"$PYTHON" "$TOOL" "${args[@]}" cloud-copyurls \
  --remote-base "$REMOTE_BASE" \
  --retries 10 \
  --low-level-retries 30 \
  --drive-chunk-size 128M \
  --tpslimit 2 \
  --tpslimit-burst 2 \
  --sleep-between 1 \
  --stats 60s \
  --require-resume-scan \
  --transfer-timeout 900 \
  --parallel "$WORKERS"
fast_rc=$?
set -e

if [[ "$fast_rc" -ne 0 ]]; then
  echo "Fast pass exited with status $fast_rc; continuing to the conservative rescue pass."
fi

"$PYTHON" "$TOOL" "${args[@]}" cloud-copyurls \
  --remote-base "$REMOTE_BASE" \
  --retries 10 \
  --low-level-retries 30 \
  --drive-chunk-size 64M \
  --tpslimit 1 \
  --tpslimit-burst 1 \
  --sleep-between 3 \
  --stats 60s \
  --require-resume-scan \
  --transfer-timeout 1800 \
  --parallel 1

"$PYTHON" "$TOOL" "${args[@]}" audit-cloud-layout --remote-base "$REMOTE_BASE"
"$PYTHON" "$TOOL" "${args[@]}" audit-drive-roots --remote-base "$REMOTE_BASE"
"$PYTHON" "$TOOL" "${args[@]}" audit-video-specs
"$PYTHON" "$TOOL" "${args[@]}" link --remote-base "$REMOTE_BASE"
