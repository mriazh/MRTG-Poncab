#!/usr/bin/env bash
# ==============================================================================
# MRTG-Poncab - Automated Fast-Reload Deployment Script
# Target: Debian 13 (PC Kantor GMF AeroAsia Pondok Cabe)
# ==============================================================================
set -e

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

echo "=========================================================="
echo "🚀 [MRTG-Poncab] Deploying latest updates on Debian server"
echo "=========================================================="

echo "📥 1/4 Pulling latest commits from GitHub..."
MAX_ATTEMPTS=3
ATTEMPT=1
until git pull origin master; do
    if [ $ATTEMPT -ge $MAX_ATTEMPTS ]; then
        echo "❌ [DEPLOY ERROR] Git pull failed after $MAX_ATTEMPTS attempts. Deployment aborted."
        exit 1
    fi
    echo "⚠️ Network delay fetching from GitHub. Retrying in 3 seconds ($ATTEMPT/$MAX_ATTEMPTS)..."
    sleep 3
    ATTEMPT=$((ATTEMPT + 1))
done

echo "📦 2/4 Syncing dependencies with uv..."
if command -v uv >/dev/null 2>&1; then
    uv sync
elif [ -x "$HOME/.local/bin/uv" ]; then
    "$HOME/.local/bin/uv" sync
elif [ -x "$APP_DIR/.venv/bin/pip" ]; then
    "$APP_DIR/.venv/bin/pip" install -e .
fi

echo "🔄 3/4 Fast-restarting systemd services (~0.2s)..."
sudo systemctl restart mrtg-poncab-web mrtg-poncab-collector

echo "✅ 4/4 Verifying service health..."
sudo systemctl status mrtg-poncab-web --no-pager -n 2
sudo systemctl status mrtg-poncab-collector --no-pager -n 2

echo "=========================================================="
echo "🎉 Update complete! Web & Console are live at port 8000."
echo "=========================================================="
