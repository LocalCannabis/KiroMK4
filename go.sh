#!/usr/bin/env bash
# go.sh — full-stack launcher for Kiro voice assistant
#   ./go.sh               — voice pipeline (default)
#   ./go.sh --text-input  — text CLI (no mic/TTS required)
set -euo pipefail

CONDA_ENV="kiro_asr"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/logs/kiro.log"
REQ_FILE="$SCRIPT_DIR/requirements.txt"

# ── Parse --text-input early so we can skip audio checks ──────────────────
TEXT_MODE=0
PASSTHROUGH_ARGS=()
for arg in "$@"; do
    if [[ "$arg" == "--text-input" ]]; then
        TEXT_MODE=1
    else
        PASSTHROUGH_ARGS+=("$arg")
    fi
done

cd "$SCRIPT_DIR"
mkdir -p logs

if [[ $TEXT_MODE -eq 1 ]]; then
    LOG_FILE="$SCRIPT_DIR/logs/kiro_cli.log"
fi

# ── 1. Clear any active virtualenv so it doesn't shadow conda ──────────────
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    echo "[INFO] Deactivating active virtualenv ($VIRTUAL_ENV)"
    deactivate 2>/dev/null || true
    # Belt-and-suspenders: strip the venv bin from PATH in case deactivate
    # wasn't sourced into this shell.
    export PATH="${PATH//${VIRTUAL_ENV}\/bin:/}"
    unset VIRTUAL_ENV
fi

# ── 2. Activate conda ─────────────────────────────────────────────────────
CONDA_BASE="$(conda info --base 2>/dev/null)" || {
    echo "[ERROR] conda not found. Install Miniconda/Anaconda first."
    exit 1
}
source "$CONDA_BASE/etc/profile.d/conda.sh"

if ! conda env list | grep -qw "$CONDA_ENV"; then
    echo "[INFO] Conda env '$CONDA_ENV' not found — creating (Python 3.12)..."
    conda create -y -n "$CONDA_ENV" python=3.12
fi

conda activate "$CONDA_ENV"
echo "[OK]   Conda env '$CONDA_ENV' active  ($(python --version))"

# ── 3. Install / upgrade pip requirements if needed ────────────────────────
if [[ -f "$REQ_FILE" ]]; then
    # Quick check: try importing the heaviest deps; install only if missing.
    if ! python -c "import numpy, yaml, openai, faster_whisper" 2>/dev/null; then
        echo "[INFO] Installing pip requirements..."
        pip install --quiet --upgrade -r "$REQ_FILE"
    else
        echo "[OK]   Python requirements satisfied"
    fi
fi

# ── 4. Sanity checks ──────────────────────────────────────────────────────
if python -c "import torch; assert torch.cuda.is_available()" 2>/dev/null; then
    echo "[OK]   CUDA available ($(python -c 'import torch; print(torch.version.cuda)'))"
else
    echo "[WARN] CUDA not available — falling back to CPU (slower)"
fi

if [[ $TEXT_MODE -eq 0 ]]; then
    if arecord -l 2>/dev/null | grep -q "card"; then
        echo "[OK]   Audio input device(s) detected"
    else
        echo "[ERROR] No audio input devices found. Is the headset plugged in?"
        exit 1
    fi
else
    echo "[OK]   Text-input mode — audio checks skipped"
fi

# ── 5. Load ambient environment (OpenRouter key, etc.) ───────────────────
AMBIENT_ENV="$HOME/.kiro/ambient.env"
if [[ -f "$AMBIENT_ENV" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$AMBIENT_ENV"
    set +a
    echo "[OK]   Ambient env loaded ($AMBIENT_ENV)"
else
    echo "[WARN] $AMBIENT_ENV not found — ambient layer may be degraded"
fi

# ── 6. Check ambient services (non-blocking warning only) ─────────────────
AMBIENT_SERVICES=(
    kiro-ingest-gcal kiro-ingest-gmail kiro-ingest-feeds
    kiro-ingest-ynab kiro-ingest-grow kiro-whatsapp-listener
    kiro-process-tagger kiro-process-patterns kiro-process-bridger
    kiro-process-knowledge kiro-process-purger kiro-briefing-composer
)
INACTIVE=()
for svc in "${AMBIENT_SERVICES[@]}"; do
    if ! systemctl is-active --quiet "$svc" 2>/dev/null; then
        INACTIVE+=("$svc")
    fi
done
if [[ ${#INACTIVE[@]} -eq 0 ]]; then
    echo "[OK]   All 12 ambient services running"
else
    echo "[WARN] Ambient services not running: ${INACTIVE[*]}"
    echo "       Run: sudo systemctl start ${INACTIVE[*]}"
fi

# ── 6b. Check Orpheus TTS services ────────────────────────────────────────
ORPHEUS_ENABLED_FLAG="${ORPHEUS_ENABLED:-false}"
if [[ "$ORPHEUS_ENABLED_FLAG" == "true" || "$ORPHEUS_ENABLED_FLAG" == "1" ]]; then
    ORPHEUS_OK=1
    for svc in kiro-llama-server kiro-orpheus-api; do
        if ! systemctl is-active --quiet "$svc" 2>/dev/null; then
            echo "[WARN] Orpheus service not running: $svc"
            echo "       Run: sudo systemctl start $svc"
            ORPHEUS_OK=0
        fi
    done
    if [[ $ORPHEUS_OK -eq 1 ]]; then
        echo "[OK]   Orpheus TTS services running (llama-server:1234, orpheus-api:5005)"
    fi
fi

# ── 7. Launch ──────────────────────────────────────────────────────────────
echo ""
if [[ $TEXT_MODE -eq 1 ]]; then
    echo "Starting Kiro CLI (text mode)... (logs → $LOG_FILE)"
    echo "Type /help for commands, /quit to exit."
    echo ""
    python kiro_cli.py "${PASSTHROUGH_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"
else
    echo "Starting Kiro... (logs → $LOG_FILE)"
    echo "Ctrl+C to stop."
    echo ""
    python kiro.py "${PASSTHROUGH_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"
fi
