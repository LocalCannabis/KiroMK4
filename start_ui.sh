#!/usr/bin/env bash
# DEPRECATED — use: ./kiro start
# This script is kept for reference only. kiro_command.py is the unified entrypoint.
echo "⚠️  start_ui.sh is deprecated. Use: ./kiro start"
echo "Redirecting..."
exec "$(dirname "$0")/kiro" start "$@"
# start_ui.sh — Start the Kiro overlay UI
#
#   ./start_ui.sh                    # auto-detect DISPLAY, default zoom/position
#   ./start_ui.sh --zoom 1.5         # custom zoom
#   ./start_ui.sh --position tl      # top-left
#   ./start_ui.sh --display :0       # explicit display
#
# Flask backend logs → /tmp/kiro_ui.log
# Ctrl+C or closing the overlay window stops everything.
# ---------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONDA_ENV="kiro_asr"
CONDA_PYTHON="/home/macklemoron/miniconda3/envs/${CONDA_ENV}/bin/python"
SYSTEM_PYTHON="/usr/bin/python3"
PORT="${KIRO_UI_PORT:-5199}"
ZOOM="2.0"
POSITION="tr"
DISPLAY_ARG=""
LOG="/tmp/kiro_ui.log"
FLASK_PID=""

# ── Parse args ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --zoom)     ZOOM="$2";        shift 2 ;;
        --position) POSITION="$2";    shift 2 ;;
        --display)  DISPLAY_ARG="$2"; shift 2 ;;
        --port)     PORT="$2";        shift 2 ;;
        *) echo "[WARN] Unknown arg: $1"; shift ;;
    esac
done

# ── Detect display ───────────────────────────────────────────────────────────
if [[ -z "$DISPLAY_ARG" ]]; then
    if [[ -n "${DISPLAY:-}" ]]; then
        DISPLAY_ARG="$DISPLAY"
    elif ls /tmp/.X11-unix/X* 2>/dev/null | grep -q .; then
        # pick the first available X socket
        DISPLAY_ARG=":$(ls /tmp/.X11-unix/ | sed 's/X//' | sort -n | head -1)"
    else
        echo "[ERROR] No X display found. Set DISPLAY or pass --display :N"
        exit 1
    fi
fi
echo "[OK]   Display: $DISPLAY_ARG"

# ── Cleanup on exit ──────────────────────────────────────────────────────────
cleanup() {
    echo ""
    echo "[INFO] Shutting down..."
    if [[ -n "$FLASK_PID" ]] && kill -0 "$FLASK_PID" 2>/dev/null; then
        kill "$FLASK_PID" 2>/dev/null && echo "[OK]   Flask stopped (PID $FLASK_PID)"
    fi
    # Belt-and-suspenders: kill any stray app.py processes we own
    pkill -u "$USER" -f "python.*ui/app\.py" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── Load .env ────────────────────────────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/.env"
    set +a
    echo "[OK]   .env loaded"
else
    echo "[WARN] No .env found — LLM keys may be missing"
fi

# ── Kill any existing UI processes ───────────────────────────────────────────
pkill -u "$USER" -f "python.*ui/app\.py"  2>/dev/null || true
pkill -u "$USER" -f "python.*ui/launcher\.py" 2>/dev/null || true
pkill -u "$USER" -f "python.*ui/overlay\.py" 2>/dev/null || true
sleep 1

# ── Start Flask backend ──────────────────────────────────────────────────────
echo "[INFO] Starting Flask backend on port $PORT..."
cd "$SCRIPT_DIR"
nohup "$CONDA_PYTHON" ui/app.py > "$LOG" 2>&1 &
FLASK_PID=$!
echo "[OK]   Flask PID $FLASK_PID → logs: $LOG"

# ── Wait for Flask to be ready (up to 15s) ───────────────────────────────────
echo -n "[INFO] Waiting for Flask..."
for i in $(seq 1 15); do
    if curl -sf --max-time 1 "http://127.0.0.1:${PORT}/api/personas" > /dev/null 2>&1; then
        echo " ready (${i}s)"
        break
    fi
    if [[ $i -eq 15 ]]; then
        echo ""
        echo "[ERROR] Flask did not start in 15s. Check $LOG"
        cat "$LOG" | tail -20
        exit 1
    fi
    echo -n "."
    sleep 1
done

# ── Start overlay (foreground — Ctrl+C stops everything) ─────────────────────
echo "[INFO] Opening overlay on $DISPLAY_ARG (zoom=${ZOOM}, position=${POSITION})"
echo "[INFO] Press Ctrl+C or close the window to stop."
echo ""
DISPLAY="$DISPLAY_ARG" "$SYSTEM_PYTHON" ui/overlay.py \
    --port "$PORT" \
    --zoom "$ZOOM" \
    --position "$POSITION"
