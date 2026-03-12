#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON=/home/macklemoron/miniconda3/envs/kiro_asr/bin/python
PIPER=/home/macklemoron/Projects/KiroMKIII/bin/piper/piper

# ---------------------------------------------------------------------------
echo "[1/5] Checking prerequisites"

[[ -f ".env" ]] && set -a && source .env && set +a

command -v arecord >/dev/null || { echo "ERROR: alsa-utils missing (arecord). Run: sudo apt install alsa-utils"; exit 1; }
command -v aplay   >/dev/null || { echo "ERROR: alsa-utils missing (aplay). Run: sudo apt install alsa-utils"; exit 1; }
[[ -x "$PIPER" ]] || { echo "ERROR: piper binary not found at $PIPER"; exit 1; }
[[ -f "config.yaml" ]] || { echo "ERROR: config.yaml missing"; exit 1; }
[[ -n "${OPENAI_API_KEY:-}" ]] || { echo "ERROR: OPENAI_API_KEY not set. Add it to .env"; exit 1; }

# ---------------------------------------------------------------------------
echo "[2/5] Verifying Python deps in kiro_asr env"

$PYTHON -c "import faster_whisper, openai, silero_vad, numpy, yaml; print('  deps OK')"

# ---------------------------------------------------------------------------
echo "[3/5] TTS smoke test (no mic needed)"

VOICE="$ROOT_DIR/data/voices/en_US-amy-medium.onnx"
[[ -f "$VOICE" ]] || { echo "ERROR: voice model missing at $VOICE"; exit 1; }

echo "Kiro Sprint 1 smoke test. If you hear this, TTS and audio output are working." \
  | "$PIPER" --model "$VOICE" --output_file /tmp/kiro_smoke.wav 2>/dev/null
aplay -D default -q /tmp/kiro_smoke.wav
echo "  TTS + aplay OK"

# ---------------------------------------------------------------------------
echo "[4/5] Audio device listing"

$PYTHON kiro.py --config config.yaml --list-devices

# ---------------------------------------------------------------------------
echo "[5/5] LLM text-only test (no mic, no TTS)"

$PYTHON kiro.py --config config.yaml --once --text "Kiro, give me a one-sentence status report." --no-tts

# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo " Smoke test PASSED. Ready for live voice loop."
echo " Run the full voice loop with:"
echo "   source .env && $PYTHON kiro.py --config config.yaml"
echo "============================================================"
