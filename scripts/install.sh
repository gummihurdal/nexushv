#!/usr/bin/env bash
# NexusHV v2.0 — Production Installer
# Installs NexusHV to /opt/nexushv with systemd services
# Run as root: sudo bash install.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

INSTALL_DIR="/opt/nexushv"

log() { echo -e "${BLUE}[NexusHV]${NC} $*"; }
ok()  { echo -e "${GREEN}[OK]${NC} $*"; }
warn(){ echo -e "${YELLOW}[WARN]${NC} $*"; }
err() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║     NexusHV v2.0 Production Installer    ║"
echo "║   Bare-Metal Hypervisor Management API   ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Check root
[[ $EUID -eq 0 ]] || err "This script must be run as root (sudo bash install.sh)"

# Detect OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    log "Detected: $PRETTY_NAME"
fi

# Install system dependencies
log "Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-venv python3-dev python3-pip \
    libvirt-dev pkg-config build-essential \
    curl wget git jq \
    qemu-kvm libvirt-daemon-system virtinst \
    2>/dev/null || warn "Some packages may not be available"
ok "System dependencies installed"

# Create install directory
log "Installing to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"/{data,logs,run}

# Copy source files
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ -d "$SCRIPT_DIR/api" ]; then
    cp -r "$SCRIPT_DIR"/api "$INSTALL_DIR/"
    cp -r "$SCRIPT_DIR"/ha "$INSTALL_DIR/"
    cp -r "$SCRIPT_DIR"/ai "$INSTALL_DIR/"
    cp -r "$SCRIPT_DIR"/ui "$INSTALL_DIR/"
    cp -r "$SCRIPT_DIR"/scripts "$INSTALL_DIR/"
    cp -r "$SCRIPT_DIR"/docs "$INSTALL_DIR/" 2>/dev/null || true
    cp -r "$SCRIPT_DIR"/observability "$INSTALL_DIR/" 2>/dev/null || true
    cp "$SCRIPT_DIR"/README.md "$INSTALL_DIR/" 2>/dev/null || true
    ok "Source files copied"
fi

# Create Python virtual environment
log "Setting up Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet \
    fastapi uvicorn[standard] psutil httpx aiofiles \
    pyjwt bcrypt prometheus-client aiosqlite python-multipart
pip install --quiet libvirt-python 2>/dev/null || warn "libvirt-python not installed (demo mode only)"
ok "Python environment ready"

# Install systemd services
log "Installing systemd services..."
cp "$INSTALL_DIR/scripts/nexushv-api.service" /etc/systemd/system/
cp "$INSTALL_DIR/scripts/nexushv-ha.service" /etc/systemd/system/
systemctl daemon-reload
ok "Systemd services installed"

# Install Ollama if not present
if ! command -v ollama &>/dev/null; then
    log "Installing Ollama..."
    curl -fsSL https://ollama.ai/install.sh | sh
    systemctl enable ollama
    systemctl start ollama
    ok "Ollama installed"
else
    ok "Ollama already installed"
fi

# Enable and start services
log "Starting NexusHV services..."
systemctl enable nexushv-api nexushv-ha 2>/dev/null || true
systemctl start nexushv-api || warn "API failed to start"
systemctl start nexushv-ha || warn "HA failed to start"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║         Installation Complete!           ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Services:"
echo "  API:     http://localhost:8080"
echo "  HA:      http://localhost:8081"
echo "  Docs:    http://localhost:8080/api/docs"
echo "  Metrics: http://localhost:8080/metrics"
echo ""
echo "Default login: admin / admin (change immediately!)"
echo ""
