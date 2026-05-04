#!/bin/bash
# v11.1 upgrade script — extract over existing v11.0 install
# This script preserves data/ and updates code only.

set -euo pipefail

echo "=========================================="
echo "  AMG OS v11.1 Upgrade"
echo "=========================================="
echo

if [ ! -d "$HOME/AMG_OS" ]; then
    echo "  ⚠ ~/AMG_OS not found."
    echo "  This script upgrades an existing v11.0 install."
    echo "  For fresh install, use scripts/setup.sh instead."
    exit 1
fi

# Optional: backup v11.0 first
read -p "Backup current install to ~/AMG_OS_v11_0_backup? [Y/n]: " backup
if [[ ! "$backup" =~ ^[Nn]$ ]]; then
    if [ -d "$HOME/AMG_OS_v11_0_backup" ]; then
        rm -rf "$HOME/AMG_OS_v11_0_backup"
    fi
    cp -r "$HOME/AMG_OS" "$HOME/AMG_OS_v11_0_backup"
    echo "  ✓ Backup created at ~/AMG_OS_v11_0_backup/"
fi

echo
echo "Updating code (data/ preserved)..."

cd "$HOME"

# Replace code directories (data/ untouched)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(dirname "$SCRIPT_DIR")"

cp -r "$SOURCE_ROOT/amg" "$HOME/AMG_OS/"
cp -r "$SOURCE_ROOT/scripts" "$HOME/AMG_OS/"
cp -r "$SOURCE_ROOT/tests" "$HOME/AMG_OS/"
cp -r "$SOURCE_ROOT/docs" "$HOME/AMG_OS/"
cp "$SOURCE_ROOT/README.md" "$HOME/AMG_OS/"
cp "$SOURCE_ROOT/CHANGELOG_v11_1.md" "$HOME/AMG_OS/"
cp "$SOURCE_ROOT/requirements.txt" "$HOME/AMG_OS/"
cp "$SOURCE_ROOT/pyproject.toml" "$HOME/AMG_OS/"

echo "  ✓ Code updated"

# Create new data subdirectories if not present
echo
echo "Ensuring data subdirectories exist..."
for d in reviewed distribution_status batch_summaries performer_documents title_corpus title_patterns operator_history; do
    mkdir -p "$HOME/AMG_OS/data/$d"
done
echo "  ✓ Data subdirectories ready"

# Reinstall (in case dependencies changed)
echo
echo "Refreshing Python install..."
cd "$HOME/AMG_OS"
if [ -d "venv" ]; then
    source venv/bin/activate
    pip install -q -e . 2>/dev/null || pip install -q -e . --break-system-packages 2>/dev/null
    echo "  ✓ Reinstalled in venv"
else
    echo "  ⚠ venv not found, skipping pip install"
fi

# Verify
echo
echo "Running verification..."
amg version 2>&1 | head -3 || true

echo
echo "=========================================="
echo "  ✓ Upgrade complete: v11.0 → v11.1"
echo "=========================================="
echo
echo "Quick reference:"
echo "  amg verify         # Full health check"
echo "  amg dashboard      # Recent activity"
echo "  amg --help         # All commands"
echo
echo "New v11.1 commands:"
echo "  amg review <scene>       # Human review form"
echo "  amg ready <scene>        # Distribution-ready check"
echo "  amg find <filters>       # Search library"
echo "  amg dvd-compile <ids>    # Compile DVDs"
echo
echo "See CHANGELOG_v11_1.md for full details."
