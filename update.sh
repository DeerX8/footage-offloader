#!/usr/bin/env bash
# ┌─────────────────────────────────────────────┐
# │  Footage Offloader - Update from GitHub     │
# │  Run as root: sudo bash update.sh           │
# └─────────────────────────────────────────────┘

set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

APP_DIR="/opt/footage-offloader"
REPO_DIR="/tmp/footage-offloader-update"

info() { echo -e "${CYAN}[INFO]${NC} $1"; }
ok()   { echo -e "${GREEN}[OK]${NC} $1"; }

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR]${NC} Please run as root: sudo bash update.sh"
    exit 1
fi

# Check for git
if ! command -v git &> /dev/null; then
    info "Installing git..."
    apt-get install -y -qq git
fi

# Default repo URL - change this to your actual repo
REPO_URL="${1:-}"
if [ -z "$REPO_URL" ]; then
    echo "Usage: sudo bash update.sh <github-repo-url>"
    echo "Example: sudo bash update.sh https://github.com/youruser/footage-offloader.git"
    exit 1
fi

info "Pulling latest from $REPO_URL..."
rm -rf "$REPO_DIR"
git clone --depth 1 "$REPO_URL" "$REPO_DIR"
ok "Repository cloned"

info "Stopping service..."
systemctl stop footage-offloader.service 2>/dev/null || true

info "Updating application files..."
cp -r "$REPO_DIR"/app.py "$APP_DIR/"
cp -r "$REPO_DIR"/offloader "$APP_DIR/"
cp -r "$REPO_DIR"/static "$APP_DIR/"
cp -r "$REPO_DIR"/templates "$APP_DIR/"
cp "$REPO_DIR"/requirements.txt "$APP_DIR/"
ok "Files updated"

info "Updating dependencies..."
pip3 install -r "$APP_DIR/requirements.txt" --break-system-packages -q
ok "Dependencies updated"

info "Restarting service..."
systemctl restart footage-offloader.service
ok "Service restarted"

rm -rf "$REPO_DIR"

echo ""
echo -e "${GREEN}Update complete! 🎉${NC}"
echo ""
