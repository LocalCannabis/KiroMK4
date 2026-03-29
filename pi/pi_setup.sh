#!/usr/bin/env bash
# =============================================================================
# pi_setup.sh — Raspberry Pi 5 setup for Kiro thin client
# =============================================================================
# Run this on a fresh Raspberry Pi OS install to set up everything needed
# for the Kiro voice client.
#
# Usage:
#   chmod +x pi_setup.sh
#   ./pi_setup.sh
#
# What it does:
#   1. Installs system dependencies (portaudio, etc.)
#   2. Installs Tailscale (if not already installed)
#   3. Creates Python venv and installs packages
#   4. Installs the systemd service
#   5. Prints next steps

set -euo pipefail

KIRO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${KIRO_DIR}/venv"

echo "============================================================"
echo "  Kiro Pi 5 Thin Client Setup"
echo "============================================================"
echo "  Directory: ${KIRO_DIR}"
echo ""

# ---------------------------------------------------------------------------
# 1. System dependencies
# ---------------------------------------------------------------------------
echo "▸ Installing system dependencies..."
sudo apt update -qq
sudo apt install -y -qq \
    python3-full \
    python3-venv \
    python3-pip \
    portaudio19-dev \
    libportaudio2 \
    libasound2-dev \
    alsa-utils \
    libsndfile1 \
    curl

echo "  ✓ System dependencies installed."

# ---------------------------------------------------------------------------
# 2. Tailscale
# ---------------------------------------------------------------------------
if command -v tailscale &> /dev/null; then
    echo "▸ Tailscale already installed."
    tailscale ip -4 2>/dev/null && echo "  ✓ Tailscale is connected." || echo "  ⚠ Run: sudo tailscale up"
else
    echo "▸ Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
    echo "  ✓ Tailscale installed."
    echo ""
    echo "  ⚠ IMPORTANT: Run 'sudo tailscale up' to authenticate."
    echo "    Then note your Tailscale IP: tailscale ip -4"
    echo ""
fi

# ---------------------------------------------------------------------------
# 3. Python virtual environment
# ---------------------------------------------------------------------------
if [ -d "${VENV_DIR}" ]; then
    echo "▸ Virtual environment already exists at ${VENV_DIR}"
else
    echo "▸ Creating Python virtual environment..."
    python3 -m venv "${VENV_DIR}"
    echo "  ✓ venv created."
fi

echo "▸ Activating venv and installing Python packages..."
source "${VENV_DIR}/bin/activate"

# PyTorch CPU-only for ARM64 (Silero VAD dependency)
echo "  Installing PyTorch (CPU-only for ARM64)..."
pip install --quiet torch --index-url https://download.pytorch.org/whl/cpu

echo "  Installing Kiro client packages..."
pip install --quiet -r "${KIRO_DIR}/requirements-client.txt"

echo "  ✓ Python packages installed."

# ---------------------------------------------------------------------------
# 4. Systemd service
# ---------------------------------------------------------------------------
echo ""
read -p "▸ Install systemd service for auto-start on boot? [y/N] " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    # Update paths in service file
    SERVICE_FILE="/etc/systemd/system/kiro-client.service"
    CURRENT_USER=$(whoami)

    sudo tee "${SERVICE_FILE}" > /dev/null << EOF
[Unit]
Description=Kiro Voice Client (Pi Thin Client)
After=network-online.target tailscaled.service sound.target
Wants=network-online.target

[Service]
Type=simple
User=${CURRENT_USER}
Group=audio
WorkingDirectory=${KIRO_DIR}
ExecStart=${VENV_DIR}/bin/python kiro_client.py --config kiro_client_config.yaml
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=XDG_RUNTIME_DIR=/run/user/$(id -u)
Environment=PULSE_SERVER=unix:/run/user/$(id -u)/pulse/native

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable kiro-client
    echo "  ✓ Systemd service installed and enabled."
    echo "    Start with: sudo systemctl start kiro-client"
    echo "    Logs:       journalctl -u kiro-client -f"
else
    echo "  Skipped. Install manually later with:"
    echo "    sudo cp kiro-client.service /etc/systemd/system/"
    echo "    sudo systemctl daemon-reload && sudo systemctl enable kiro-client"
fi

# ---------------------------------------------------------------------------
# 5. Summary & next steps
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "  ✓ Setup Complete!"
echo "============================================================"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Connect Tailscale (if not already):"
echo "       sudo tailscale up"
echo "       tailscale ip -4     # Note your Pi's IP"
echo ""
echo "  2. Edit config with Beast's Tailscale IP:"
echo "       nano ${KIRO_DIR}/kiro_client_config.yaml"
echo "       # Set beast.host to the Beast's 100.x.x.x IP"
echo ""
echo "  3. Test audio hardware:"
echo "       source ${VENV_DIR}/bin/activate"
echo "       python audio_test.py"
echo "       python audio_test.py --record"
echo "       # Note the device indices, update config if needed"
echo ""
echo "  4. Test connection to Beast:"
echo "       ping \$(grep 'host:' kiro_client_config.yaml | head -1 | awk '{print \$2}' | tr -d '\"')"
echo ""
echo "  5. Start the client:"
echo "       source ${VENV_DIR}/bin/activate"
echo "       python kiro_client.py"
echo ""
echo "  6. Or start via systemd:"
echo "       sudo systemctl start kiro-client"
echo "       journalctl -u kiro-client -f"
echo ""
