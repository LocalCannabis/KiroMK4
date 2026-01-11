#!/usr/bin/env bash
# Kiro Run Script
# Convenient wrapper for running the daemon

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Load environment variables from .env if it exists
if [[ -f ".env" ]]; then
    set -a
    source .env
    set +a
    echo "✓ Loaded .env"
fi

# Fix for conda/system library conflicts (libstdc++ version mismatch)
# Force system libstdc++ to avoid GLIBCXX version errors with JACK/PulseAudio
export LD_PRELOAD="/usr/lib/x86_64-linux-gnu/libstdc++.so.6"

# Activate virtual environment if it exists
if [[ -f ".venv/bin/activate" ]]; then
    source .venv/bin/activate
    echo "✓ Activated .venv"
fi

# Check for API keys
if [[ -z "$OPENAI_API_KEY" ]]; then
    echo "⚠ OPENAI_API_KEY not set - STT/TTS will be disabled"
fi
if [[ -z "$ANTHROPIC_API_KEY" ]]; then
    echo "⚠ ANTHROPIC_API_KEY not set - using OpenAI for LLM"
fi

echo "Starting Kiro..."
echo ""

# Default to console mode with debug for development
if [[ "$1" == "--production" ]]; then
    shift
    exec kirod "$@"
else
    exec kirod --console --debug "$@"
fi
