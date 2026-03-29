#!/usr/bin/env bash
# scripts/download_orpheus_model.sh
# Downloads the Orpheus 3B TTS GGUF model from HuggingFace.
# Run once before starting kiro-llama-server.
#
# Model: canopylabs/orpheus-3b-0.1-ft-GGUF (Q8_0 — best quality, ~3.5GB VRAM)
# Fallback: Q4_K_M — ~2GB VRAM, slightly lower quality
#
# Usage:
#   bash scripts/download_orpheus_model.sh          # Q8_0 (default)
#   bash scripts/download_orpheus_model.sh q4       # Q4_K_M (lower VRAM)

set -euo pipefail

QUANT="${1:-q8}"
DEST="/home/macklemoron/orpheus/models"
mkdir -p "$DEST"

if [[ "$QUANT" == "q4" ]]; then
    FILENAME="orpheus-3b-0.1-ft-Q4_K_M.gguf"
    URL="https://huggingface.co/canopylabs/orpheus-3b-0.1-ft-GGUF/resolve/main/orpheus-3b-0.1-ft-Q4_K_M.gguf"
else
    FILENAME="orpheus-3b-0.1-ft-Q8_0.gguf"
    URL="https://huggingface.co/canopylabs/orpheus-3b-0.1-ft-GGUF/resolve/main/orpheus-3b-0.1-ft-Q8_0.gguf"
fi

TARGET="$DEST/$FILENAME"

if [[ -f "$TARGET" ]]; then
    SIZE=$(du -sh "$TARGET" | cut -f1)
    echo "[OK] Already downloaded: $TARGET ($SIZE)"
    exit 0
fi

echo "[INFO] Downloading $FILENAME from HuggingFace..."
echo "       Destination: $TARGET"
echo "       Size: ~3.5GB for Q8_0, ~2.0GB for Q4_K_M — this will take a few minutes."
echo ""

# Use wget with progress bar; fall back to curl if wget not available
if command -v wget &>/dev/null; then
    wget --progress=bar:force -O "$TARGET" "$URL"
else
    curl -L --progress-bar -o "$TARGET" "$URL"
fi

SIZE=$(du -sh "$TARGET" | cut -f1)
echo ""
echo "[OK] Downloaded: $TARGET ($SIZE)"
echo ""
echo "Next steps:"
echo "  1. sudo cp /home/macklemoron/Projects/KiroMK4/audio/kiro-llama-server.service /etc/systemd/system/"
echo "  2. sudo cp /home/macklemoron/Projects/KiroMK4/audio/kiro-orpheus-api.service /etc/systemd/system/"
echo "  3. sudo systemctl daemon-reload"
echo "  4. sudo systemctl enable --now kiro-llama-server kiro-orpheus-api"
echo "  5. Wait ~30s, then: curl http://localhost:5005/health"
echo "  6. Set ORPHEUS_ENABLED=true in .env"
