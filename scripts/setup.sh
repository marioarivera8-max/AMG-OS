#!/bin/bash
#
# AMG OS v11 Setup Script
# =======================
#
# Idempotent: safe to run multiple times.
# Configures Ollama with MLX + flash attention + parallel scoring,
# pulls the required vision model, installs Python dependencies,
# and persists env vars via BOTH launchctl AND .zshrc.
#
# Usage:
#   ./scripts/setup.sh           Full setup
#   ./scripts/setup.sh --check   Verify current state without changes
#

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AMG_OS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK_ONLY=false

# Color output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
RESET='\033[0m'

ok()    { echo -e "${GREEN}✓${RESET} $1"; }
warn()  { echo -e "${YELLOW}⚠${RESET} $1"; }
fail()  { echo -e "${RED}✗${RESET} $1"; }
info()  { echo -e "${BLUE}→${RESET} $1"; }

# Parse args
for arg in "$@"; do
    case $arg in
        --check) CHECK_ONLY=true ;;
        --help|-h)
            grep '^#' "$0" | head -25
            exit 0
            ;;
    esac
done

echo "================================================================"
echo "AMG OS v11 Setup"
echo "================================================================"
echo "AMG_OS_ROOT: $AMG_OS_ROOT"
if $CHECK_ONLY; then
    echo "Mode: CHECK ONLY (no changes will be made)"
fi
echo ""

# -------------------------------------------------------------
# 1. Verify macOS + Apple Silicon
# -------------------------------------------------------------
info "Checking platform..."

if [[ "$(uname)" != "Darwin" ]]; then
    fail "AMG OS v11 requires macOS. Detected: $(uname)"
    exit 1
fi
ok "macOS detected"

ARCH=$(uname -m)
if [[ "$ARCH" != "arm64" ]]; then
    warn "Apple Silicon (arm64) recommended for MLX backend. Detected: $ARCH"
    warn "v11 will work but MLX speedup unavailable on Intel Macs."
else
    ok "Apple Silicon ($ARCH)"
fi

# -------------------------------------------------------------
# 2. Check Homebrew
# -------------------------------------------------------------
info "Checking Homebrew..."
if ! command -v brew &> /dev/null; then
    fail "Homebrew not found. Install from https://brew.sh"
    exit 1
fi
ok "Homebrew installed at $(which brew)"

# -------------------------------------------------------------
# 3. Check / install ffmpeg
# -------------------------------------------------------------
info "Checking ffmpeg..."
if ! command -v ffmpeg &> /dev/null; then
    if $CHECK_ONLY; then
        fail "ffmpeg not installed"
    else
        info "Installing ffmpeg via Homebrew..."
        brew install ffmpeg
        ok "ffmpeg installed"
    fi
else
    ok "ffmpeg installed: $(ffmpeg -version | head -1 | awk '{print $3}')"
fi

# -------------------------------------------------------------
# 4. Check / install Ollama
# -------------------------------------------------------------
info "Checking Ollama..."
if ! command -v ollama &> /dev/null; then
    if $CHECK_ONLY; then
        fail "Ollama not installed"
    else
        info "Installing Ollama via Homebrew..."
        brew install ollama
        ok "Ollama installed"
    fi
else
    OLLAMA_VERSION=$(ollama --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    ok "Ollama installed: v${OLLAMA_VERSION:-unknown}"

    # Check version >= 0.19 (MLX support)
    if [[ -n "$OLLAMA_VERSION" ]]; then
        MAJOR=$(echo "$OLLAMA_VERSION" | cut -d. -f1)
        MINOR=$(echo "$OLLAMA_VERSION" | cut -d. -f2)
        if (( MAJOR < 1 )) && (( MINOR < 19 )); then
            warn "Ollama v$OLLAMA_VERSION < v0.19 — MLX backend unavailable"
            warn "Upgrade with: brew upgrade ollama"
        else
            ok "Ollama version supports MLX (>=0.19)"
        fi
    fi
fi

# -------------------------------------------------------------
# 5. Configure Ollama env vars (CRITICAL)
# -------------------------------------------------------------
info "Configuring Ollama environment variables..."

# These need to be set in BOTH:
#   1. ~/.zshrc (for terminal-launched Ollama)
#   2. launchctl (for Ollama.app GUI)

# Use parallel arrays (bash 3.2 compatible — macOS ships bash 3.2)
ENV_VAR_NAMES=(
    "OLLAMA_MLX"
    "OLLAMA_FLASH_ATTENTION"
    "OLLAMA_NUM_PARALLEL"
    "OLLAMA_KV_CACHE_TYPE"
    "OLLAMA_KEEP_ALIVE"
    "OLLAMA_MAX_LOADED_MODELS"
    "OLLAMA_CONTEXT_LENGTH"
    "OLLAMA_HOST"
)
ENV_VAR_VALUES=(
    "1"
    "1"
    "4"
    "q8_0"
    "24h"
    "1"
    "2560"
    "127.0.0.1:11434"
)

ZSHRC="$HOME/.zshrc"
ZSHRC_SECTION_MARKER="# === AMG OS v11 — Ollama Configuration ==="
ZSHRC_END_MARKER="# === END AMG OS v11 ==="

if ! $CHECK_ONLY; then
    # Backup .zshrc if it exists
    if [[ -f "$ZSHRC" ]]; then
        cp "$ZSHRC" "$ZSHRC.amg_backup_$(date +%Y%m%d_%H%M%S)"
    fi

    # Remove existing AMG section (idempotent)
    if grep -q "$ZSHRC_SECTION_MARKER" "$ZSHRC" 2>/dev/null; then
        # Remove between markers
        sed -i.tmp "/$ZSHRC_SECTION_MARKER/,/$ZSHRC_END_MARKER/d" "$ZSHRC"
        rm -f "$ZSHRC.tmp"
    fi

    # Append fresh section
    {
        echo ""
        echo "$ZSHRC_SECTION_MARKER"
        for i in "${!ENV_VAR_NAMES[@]}"; do
            echo "export ${ENV_VAR_NAMES[$i]}=\"${ENV_VAR_VALUES[$i]}\""
        done
        echo "$ZSHRC_END_MARKER"
    } >> "$ZSHRC"
    ok "Updated $ZSHRC with Ollama env vars"

    # Set via launchctl (for Ollama GUI app)
    info "Setting launchctl env vars (for Ollama GUI app)..."
    for i in "${!ENV_VAR_NAMES[@]}"; do
        launchctl setenv "${ENV_VAR_NAMES[$i]}" "${ENV_VAR_VALUES[$i]}"
    done
    ok "launchctl env vars set"
else
    # Check mode
    for i in "${!ENV_VAR_NAMES[@]}"; do
        var="${ENV_VAR_NAMES[$i]}"
        expected="${ENV_VAR_VALUES[$i]}"
        actual="${!var:-}"
        if [[ "$actual" == "$expected" ]]; then
            ok "$var=$expected"
        else
            warn "$var: expected='$expected', actual='${actual:-unset}'"
        fi
    done
fi

# -------------------------------------------------------------
# 6. Restart Ollama service to pick up env vars
# -------------------------------------------------------------
if ! $CHECK_ONLY; then
    info "Restarting Ollama service..."
    if pgrep -x ollama > /dev/null; then
        brew services restart ollama 2>/dev/null || {
            # Fallback: kill + start
            pkill -x ollama 2>/dev/null || true
            sleep 2
            brew services start ollama
        }
    else
        brew services start ollama
    fi

    # Wait for it to come up
    info "Waiting for Ollama to be ready..."
    for i in {1..30}; do
        if curl -s http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
            ok "Ollama responding"
            break
        fi
        sleep 1
        if [[ $i -eq 30 ]]; then
            fail "Ollama did not start within 30 seconds"
            exit 1
        fi
    done
fi

# -------------------------------------------------------------
# 7. Pull vision model
# -------------------------------------------------------------
info "Checking vision model..."
MODEL_NAME="qwen2.5vl:7b"

if curl -s http://127.0.0.1:11434/api/tags 2>/dev/null | grep -q "$MODEL_NAME"; then
    ok "Model already loaded: $MODEL_NAME"
else
    if $CHECK_ONLY; then
        fail "Model not loaded: $MODEL_NAME"
    else
        info "Pulling $MODEL_NAME (this may take 5-10 minutes)..."
        ollama pull "$MODEL_NAME"
        ok "Model pulled: $MODEL_NAME"
    fi
fi

# -------------------------------------------------------------
# 8. Set up Python venv
# -------------------------------------------------------------
info "Setting up Python virtual environment..."

VENV_DIR="$AMG_OS_ROOT/venv"

if [[ ! -d "$VENV_DIR" ]]; then
    if $CHECK_ONLY; then
        fail "Python venv not created"
    else
        # Find a suitable Python (3.11+)
        PYTHON_CMD=""
        for cmd in python3.13 python3.12 python3.11 python3; do
            if command -v "$cmd" &> /dev/null; then
                VERSION=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
                MAJOR=$(echo "$VERSION" | cut -d. -f1)
                MINOR=$(echo "$VERSION" | cut -d. -f2)
                if (( MAJOR == 3 )) && (( MINOR >= 11 )); then
                    PYTHON_CMD="$cmd"
                    break
                fi
            fi
        done

        if [[ -z "$PYTHON_CMD" ]]; then
            fail "Python 3.11+ required. Install with: brew install python@3.12"
            exit 1
        fi

        info "Using $PYTHON_CMD ($(${PYTHON_CMD} --version))"
        "$PYTHON_CMD" -m venv "$VENV_DIR"
        ok "Created venv at $VENV_DIR"
    fi
else
    ok "Venv exists at $VENV_DIR"
fi

# -------------------------------------------------------------
# 9. Install Python dependencies
# -------------------------------------------------------------
if ! $CHECK_ONLY; then
    info "Installing Python dependencies..."
    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip --quiet
    pip install -r "$AMG_OS_ROOT/requirements.txt" --quiet
    ok "Dependencies installed"
fi

# -------------------------------------------------------------
# 10. Create data directories
# -------------------------------------------------------------
info "Creating data directories..."
DATA_DIRS=(
    "data/decision_logs"
    "data/studio_profiles"
    "data/performers"
    "data/logs/structured"
    "data/logs/runs"
    "data/backups"
)

for d in "${DATA_DIRS[@]}"; do
    mkdir -p "$AMG_OS_ROOT/$d"
done
ok "Data directories ready"

# -------------------------------------------------------------
# 11. Install `amg` shortcut alias
# -------------------------------------------------------------
if ! $CHECK_ONLY; then
    AMG_ALIAS_LINE="alias amg=\"cd $AMG_OS_ROOT && source venv/bin/activate && python -m amg.cli\""
    AMG_ALIAS_MARKER="# === AMG OS v11 — CLI alias ==="

    if grep -q "$AMG_ALIAS_MARKER" "$ZSHRC" 2>/dev/null; then
        # Already installed
        :
    else
        {
            echo ""
            echo "$AMG_ALIAS_MARKER"
            echo "$AMG_ALIAS_LINE"
        } >> "$ZSHRC"
        ok "Installed 'amg' shortcut alias in $ZSHRC"
    fi
fi

# -------------------------------------------------------------
# Final report
# -------------------------------------------------------------
echo ""
echo "================================================================"
if $CHECK_ONLY; then
    echo "Setup verification complete."
else
    echo "Setup complete!"
fi
echo "================================================================"

if ! $CHECK_ONLY; then
    cat <<EOF

NEXT STEPS:

  1. Open a NEW terminal (or run: source ~/.zshrc)
     This loads the env vars and the 'amg' alias.

  2. Verify everything works:
     amg verify

  3. Process a single scene:
     amg process /path/to/scene/

  4. Process a batch:
     amg batch /path/to/incoming/

  5. View performance after some scenes:
     amg analyze

For full docs: $AMG_OS_ROOT/docs/

EOF
fi
