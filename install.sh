#!/usr/bin/env bash
# ┌─────────────────────────────────────────────┐
# │  Footage Offloader - Installer              │
# │  Run as root: sudo bash install.sh          │
# └─────────────────────────────────────────────┘

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

APP_DIR="/opt/footage-offloader"
CONFIG_DIR="/etc/footage-offloader"
SSD_MOUNT="/mnt/ssd"
NAS_MOUNT="/mnt/nas"

info()  { echo -e "${CYAN}[INFO]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── Check root ────────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    error "Please run as root: sudo bash install.sh"
fi

echo ""
echo -e "${CYAN}╔═══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     Footage Offloader - Installation      ║${NC}"
echo -e "${CYAN}╚═══════════════════════════════════════════╝${NC}"
echo ""

# ── System packages ───────────────────────────────────────────────────────────
info "Updating packages..."
apt-get update -qq

info "Installing dependencies..."
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    cifs-utils smbclient \
    exfat-fuse exfatprogs ntfs-3g \
    rsync openssh-client \
    avahi-daemon \
    > /dev/null 2>&1
ok "System packages installed"

# ── Tailscale check ──────────────────────────────────────────────────────────
if command -v tailscale &> /dev/null; then
    ok "Tailscale is installed"
    TS_IP=$(tailscale ip -4 2>/dev/null || echo "not connected")
    info "Tailscale IP: $TS_IP"
else
    warn "Tailscale not installed. Installing..."
    curl -fsSL https://tailscale.com/install.sh | sh
    ok "Tailscale installed - run 'sudo tailscale up' to connect"
fi

# ── Create directories ───────────────────────────────────────────────────────
info "Creating directories..."
mkdir -p "$SSD_MOUNT" "$NAS_MOUNT" "$CONFIG_DIR" "$APP_DIR"

# ── Copy application ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
info "Installing application to $APP_DIR..."

# Copy all app files
cp -r "$SCRIPT_DIR"/app.py "$APP_DIR/"
cp -r "$SCRIPT_DIR"/offloader "$APP_DIR/"
cp -r "$SCRIPT_DIR"/static "$APP_DIR/"
cp -r "$SCRIPT_DIR"/templates "$APP_DIR/"
cp "$SCRIPT_DIR"/requirements.txt "$APP_DIR/"
ok "Application files copied"

# ── Python dependencies ──────────────────────────────────────────────────────
info "Installing Python dependencies..."
pip3 install -r "$APP_DIR/requirements.txt" --break-system-packages -q
ok "Python dependencies installed"

# ── Create default config ────────────────────────────────────────────────────
if [ ! -f "$CONFIG_DIR/config.json" ]; then
    info "Creating default configuration..."
    cat > "$CONFIG_DIR/config.json" <<EOF
{
  "smb_host": "",
  "smb_share": "",
  "smb_username": "",
  "smb_password": "",
  "smb_domain": "WORKGROUP",
  "discord_webhook_url": "",
  "discord_enabled": false,
  "ssd_mount_point": "$SSD_MOUNT",
  "smb_mount_point": "$NAS_MOUNT",
  "host": "0.0.0.0",
  "port": 5000
}
EOF
    ok "Default config created at $CONFIG_DIR/config.json"
else
    ok "Existing config preserved"
fi

# ── Create log file ──────────────────────────────────────────────────────────
touch /var/log/footage-offloader.log
chmod 644 /var/log/footage-offloader.log

# ── Sudoers for mount commands ───────────────────────────────────────────────
info "Configuring permissions..."
cat > /etc/sudoers.d/footage-offloader <<EOF
# Allow footage-offloader to mount/unmount without password
ALL ALL=(ALL) NOPASSWD: /usr/bin/mount
ALL ALL=(ALL) NOPASSWD: /usr/bin/umount
ALL ALL=(ALL) NOPASSWD: /bin/mount
ALL ALL=(ALL) NOPASSWD: /bin/umount
EOF
chmod 440 /etc/sudoers.d/footage-offloader
ok "Mount permissions configured"

# ── Systemd service ──────────────────────────────────────────────────────────
info "Installing systemd service..."
cp "$SCRIPT_DIR/systemd/footage-offloader.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable footage-offloader.service
systemctl restart footage-offloader.service
ok "Service installed and started"

# ── udev rules ───────────────────────────────────────────────────────────────
info "Installing udev rules..."
cp "$SCRIPT_DIR/udev/99-usb-ssd.rules" /etc/udev/rules.d/
udevadm control --reload-rules
udevadm trigger
ok "udev rules installed"

# ── Avahi/mDNS hostname ─────────────────────────────────────────────────────
info "Configuring hostname for local discovery..."
HOSTNAME=$(hostname)
systemctl enable avahi-daemon 2>/dev/null || true
systemctl restart avahi-daemon 2>/dev/null || true
ok "Device reachable at http://${HOSTNAME}.local:5000"

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        Installation Complete! 🎉          ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Web UI:     ${CYAN}http://${HOSTNAME}.local:5000${NC}"
if command -v tailscale &> /dev/null; then
    TS_IP=$(tailscale ip -4 2>/dev/null || echo "")
    if [ -n "$TS_IP" ]; then
        echo -e "  Tailscale:  ${CYAN}http://${TS_IP}:5000${NC}"
    fi
fi
echo ""
echo -e "  Service:    ${YELLOW}sudo systemctl status footage-offloader${NC}"
echo -e "  Logs:       ${YELLOW}sudo journalctl -u footage-offloader -f${NC}"
echo -e "  Config:     ${YELLOW}$CONFIG_DIR/config.json${NC}"
echo ""
echo -e "  ${YELLOW}Next steps:${NC}"
echo -e "    1. Open the web UI in your browser"
echo -e "    2. Go to Settings and configure your NAS connection"
echo -e "    3. Plug in your SSD and start offloading!"
echo ""
