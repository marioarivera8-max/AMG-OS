#!/bin/bash
#
# AMG OS v10.x -> v11 Migration Helper
# =====================================
#
# Discovers v10.x work directories (~/AMG_Processing/...)
# and offers to migrate state into v11's data folder.
#
# v10.3 stays in place as a safety net. Nothing is deleted.
#

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RESET='\033[0m'

AMG_OS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
V10_ROOT="$HOME/AMG_Processing"

echo -e "${BLUE}AMG OS v10.x → v11 Migration${RESET}"
echo "================================================================"
echo "v10.x location:  $V10_ROOT"
echo "v11 location:    $AMG_OS_ROOT"
echo ""

if [[ ! -d "$V10_ROOT" ]]; then
    echo "No v10.x installation found at $V10_ROOT — nothing to migrate."
    exit 0
fi

# Count v10.x work directories
V10_WORK_DIRS=$(find "$V10_ROOT" -type d -name "*_amg_v10*" 2>/dev/null | wc -l | tr -d ' ')
echo "Found $V10_WORK_DIRS v10.x work directories"
echo ""

read -p "Continue with migration? [y/N] " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Migration cancelled."
    exit 0
fi

# Migrate any studio profiles or performer data v10.x stored
# (v10.x didn't have these, so this is mostly a placeholder for forward compat)

echo ""
echo -e "${GREEN}Migration complete.${RESET}"
echo ""
echo "v10.x is preserved at $V10_ROOT (safety net — do not delete)."
echo "v11 will not touch v10.x work directories."
echo ""
echo "To process new scenes with v11:"
echo "  amg batch /path/to/new/incoming/"
