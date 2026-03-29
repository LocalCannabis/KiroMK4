#!/bin/bash
# =============================================================================
# ambient/services/install_services.sh — Install all Kiro ambient systemd services
# =============================================================================
#
# Usage:
#   sudo bash ambient/services/install_services.sh
#
# This script:
#   1. Creates the ambient.env file if it doesn't exist
#   2. Copies all .service files to /etc/systemd/system/
#   3. Reloads systemd daemon
#   4. Enables (but does NOT start) all services
#
# Start services manually or with:
#   sudo systemctl start kiro-ingest-ynab kiro-ingest-grow ...
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="/home/macklemoron/.kiro/ambient.env"

echo "=== Kiro Ambient Intelligence — Service Installer ==="

# Create env file if needed
if [ ! -f "$ENV_FILE" ]; then
    echo "Creating $ENV_FILE (add your API keys here)"
    mkdir -p "$(dirname "$ENV_FILE")"
    cat > "$ENV_FILE" <<'EOF'
# Kiro Ambient Intelligence — Environment Variables
# Add your API keys here. This file is read by all ambient workers.

# OpenRouter API key (used for all LLM calls in the ambient layer)
OPENROUTER_API_KEY=

# OpenAI API key (fallback, also used by Kiro main pipeline)
OPENAI_API_KEY=

# Jack DB password (if set)
# JACK_DB_PASSWORD=
EOF
    chmod 600 "$ENV_FILE"
    echo "  → Created $ENV_FILE — EDIT THIS FILE to add your API keys!"
fi

# Copy service files
echo "Installing systemd service files..."
for svc in "$SCRIPT_DIR"/*.service; do
    if [ -f "$svc" ]; then
        fname="$(basename "$svc")"
        echo "  → $fname"
        cp "$svc" /etc/systemd/system/"$fname"
    fi
done

# Reload systemd
echo "Reloading systemd daemon..."
systemctl daemon-reload

# Enable services (don't start yet)
echo "Enabling services..."
SERVICES=(
    kiro-ingest-ynab
    kiro-ingest-grow
    kiro-ingest-gcal
    kiro-ingest-gmail
    kiro-ingest-whatsapp
    kiro-ingest-feeds
    kiro-process-tagger
    kiro-process-patterns
    kiro-process-bridger
    kiro-process-knowledge
    kiro-process-purger
    kiro-briefing-composer
)

for svc in "${SERVICES[@]}"; do
    if systemctl is-enabled "$svc" &>/dev/null; then
        echo "  → $svc (already enabled)"
    else
        systemctl enable "$svc"
        echo "  → $svc (enabled)"
    fi
done

echo ""
echo "=== Installation complete! ==="
echo ""
echo "Next steps:"
echo "  1. Edit $ENV_FILE and add your OPENROUTER_API_KEY"
echo "  2. Run the ambient migrations: cd /home/macklemoron/Projects/KiroMK4 && python -m ambient.migrate"
echo "  3. Start individual services:"
echo "     sudo systemctl start kiro-ingest-ynab"
echo "     sudo systemctl start kiro-ingest-grow"
echo "     sudo systemctl start kiro-process-tagger"
echo "     sudo systemctl start kiro-briefing-composer"
echo "  4. Or start all at once:"
echo "     sudo systemctl start ${SERVICES[*]}"
echo ""
echo "  Monitor with:"
echo "     journalctl -u kiro-ingest-ynab -f"
echo "     journalctl -u kiro-process-tagger -f"
echo "     journalctl -u kiro-briefing-composer -f"
