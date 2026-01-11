#!/usr/bin/env bash
# Kiro Development Installation Script
# Sets up the development environment

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "🤖 Kiro Development Setup"
echo "========================="

cd "$PROJECT_DIR"

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1,2)
REQUIRED_VERSION="3.11"

if [[ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]]; then
    echo "❌ Python $REQUIRED_VERSION+ required, found $PYTHON_VERSION"
    exit 1
fi

echo "✓ Python $PYTHON_VERSION detected"

# Create virtual environment if it doesn't exist
if [[ ! -d ".venv" ]]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate virtual environment
source .venv/bin/activate

# Upgrade pip
echo "📦 Upgrading pip..."
pip install --upgrade pip

# Install package in development mode
echo "📦 Installing Kiro with development dependencies..."
pip install -e ".[dev]"

# Create user directories
echo "📁 Creating user directories..."
mkdir -p ~/.kiro/config
mkdir -p ~/.kiro/data
mkdir -p ~/.kiro/logs

# Copy default config if user config doesn't exist
if [[ ! -f ~/.kiro/config/kiro.yaml ]]; then
    echo "📝 Creating default user config..."
    cp config/default.yaml ~/.kiro/config/kiro.yaml
fi

echo ""
echo "✅ Installation complete!"
echo ""
echo "To activate the environment:"
echo "  source .venv/bin/activate"
echo ""
echo "To run Kiro:"
echo "  kirod"
echo "  kirod --debug --console  # for development"
echo ""
echo "To run tests:"
echo "  pytest"
