#!/bin/bash
# NexusHV — Bare Metal Installer
# Tested on Ubuntu 22.04 LTS / Debian 12
# Run as root: curl -fsSL https://raw.githubusercontent.com/gummihurdal/nexushv/main/scripts/install.sh | bash

set -euo pipefail

NEXUSHV_DIR="/opt/nexushv"
NEXUSHV_USER="nexushv"
OLLAMA_MODEL="nexushv-ai"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'; BOLD='\033[1m'
info()    { echo -e "${GREEN}[NexusHV]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
section() { echo -e "\n${BOLD}══════════════════════════════════════${NC}"; echo -e "${BOLD} $1${NC}"; echo -e "${BOLD}══════════════════════════════════════${NC}"; }

[ "$EUID" -ne 0 ] && error "Run as root: sudo bash install.sh"
[ "$(uname -m)" != "x86_64" ] && error "x86_64 required"

section "NexusHV Installer"
echo "This will install NexusHV on $(hostname) ($(lsb_release -ds 2>/dev/null || echo 'Linux'))"
echo ""

# ── 1. KVM prerequisites ──────────────────────────────────────────────────────
section "1/6 Installing KVM + libvirt"
apt-get update -qq
apt-get install -y -qq \
    qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils \
    virtinst virt-top cpu-checker numactl \
    python3 python3-pip python3-venv git curl wget

# Verify hardware virt support
if ! kvm-ok &>/dev/null; then
    warn "KVM hardware acceleration not available. Checking..."
    grep -Eqc '(vmx|svm)' /proc/cpuinfo || error "CPU does not support virtualization. Enable VT-x/AMD-V in BIOS."
fi
info "KVM: OK"

# ── 2. Enable libvirt ─────────────────────────────────────────────────────────
section "2/6 Configuring libvirt"
systemctl enable --now libvirtd
usermod -aG libvirt,kvm "$SUDO_USER" 2>/dev/null || true
virsh list --all &>/dev/null && info "libvirt: OK" || error "libvirt not responding"

# ── 3. Network bridge ─────────────────────────────────────────────────────────
section "3/6 Creating VM network bridge (vmbr0)"
if ! ip link show vmbr0 &>/dev/null; then
    MAIN_IFACE=$(ip route | grep default | awk '{print $5}' | head -1)
    info "Main interface: $MAIN_IFACE"

    cat > /etc/systemd/network/10-vmbr0.netdev << EOF
[NetDev]
Name=vmbr0
Kind=bridge
EOF
    cat > /etc/systemd/network/10-vmbr0.network << EOF
[Match]
Name=vmbr0
[Network]
Address=$(ip -4 addr show $MAIN_IFACE | grep inet | awk '{print $2}' | head -1)
Gateway=$(ip route | grep default | awk '{print $3}' | head -1)
DNS=1.1.1.1
EOF
    systemctl restart systemd-networkd || warn "Could not restart networkd — configure bridge manually"
    info "Bridge vmbr0 created (manual network config may be needed)"
else
    info "vmbr0 already exists"
fi

# ── 4. NexusHV application ────────────────────────────────────────────────────
section "4/6 Installing NexusHV"
mkdir -p "$NEXUSHV_DIR"

if [ -d "$NEXUSHV_DIR/.git" ]; then
    info "Updating existing installation..."
    git -C "$NEXUSHV_DIR" pull
else
    info "Cloning NexusHV..."
    git clone https://github.com/gummihurdal/nexushv "$NEXUSHV_DIR"
fi

python3 -m venv "$NEXUSHV_DIR/venv"
"$NEXUSHV_DIR/venv/bin/pip" install -q --upgrade pip
"$NEXUSHV_DIR/venv/bin/pip" install -q \
    fastapi uvicorn libvirt-python psutil websockets httpx \
    aiofiles pyghmi

# systemd service — API
cat > /etc/systemd/system/nexushv-api.service << EOF
[Unit]
Description=NexusHV Hypervisor API
After=network.target libvirtd.service

[Service]
Type=simple
User=root
WorkingDirectory=$NEXUSHV_DIR
ExecStart=$NEXUSHV_DIR/venv/bin/python3 api/nexushv_api.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable nexushv-api
systemctl start nexushv-api
sleep 2
curl -s http://localhost:8080/health | grep -q '"status":"ok"' && info "NexusHV API: OK (port 8080)" || warn "API not responding yet — check: journalctl -u nexushv-api"

# ── 5. Ollama + NEXUS AI ──────────────────────────────────────────────────────
section "5/6 Installing Ollama (local AI)"
if ! command -v ollama &>/dev/null; then
    info "Installing Ollama..."
    curl -fsSL https://ollama.ai/install.sh | sh
    systemctl enable ollama
    systemctl start ollama
    sleep 3
else
    info "Ollama already installed"
    systemctl start ollama || true
fi

# Pull base model if fine-tuned model not available
if ollama list | grep -q "$OLLAMA_MODEL"; then
    info "NEXUS AI model ($OLLAMA_MODEL): already installed"
elif ollama list | grep -q "llama3.1:8b"; then
    info "Base Llama3 model: already present"
else
    info "Pulling Llama3.1:8b base model (4.7GB — this takes a few minutes)..."
    ollama pull llama3.1:8b
    info "Base model ready. Fine-tune with: cd $NEXUSHV_DIR && python3 ai/training/nexushv_train.py"
fi

# ── 6. Firewall ───────────────────────────────────────────────────────────────
section "6/6 Firewall rules"
if command -v ufw &>/dev/null; then
    ufw allow 8080/tcp comment "NexusHV API" 2>/dev/null || true
    ufw allow 8081/tcp comment "NexusHV HA"  2>/dev/null || true
    ufw allow 11434/tcp comment "Ollama AI"  2>/dev/null || true
    # HA multicast — can't do this via ufw, use iptables
    iptables -I INPUT -p udp --dport 5405 -j ACCEPT 2>/dev/null || true
    info "Firewall: ports 8080, 8081, 11434, 5405/udp opened"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
section "Installation Complete"
HOST_IP=$(hostname -I | awk '{print $1}')
echo ""
echo -e "  ${GREEN}NexusHV API${NC}:  http://$HOST_IP:8080"
echo -e "  ${GREEN}Health check${NC}: http://$HOST_IP:8080/health"
echo -e "  ${GREEN}AI status${NC}:    http://$HOST_IP:8080/ai/health"
echo ""
echo "  Next steps:"
echo "  1. Open the NexusHV console UI in your browser"
echo "  2. To start HA daemon: python3 $NEXUSHV_DIR/ha/nexushv_ha.py --host $HOST_IP --peers <other-host-ips>"
echo "  3. To fine-tune NEXUS AI: python3 $NEXUSHV_DIR/ai/training/nexushv_train.py"
echo ""
echo -e "  ${YELLOW}Note${NC}: Log out and back in for libvirt group membership to take effect"
echo ""
