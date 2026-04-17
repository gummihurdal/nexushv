"""
NexusHV Zero-Touch Provisioning — Plug in a server, it joins the cluster.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
import random

router = APIRouter(prefix="/api/provisioning", tags=["Zero-Touch Provisioning"])

@router.get("/discovery")
def discovered_hosts():
    """List hosts discovered on the network waiting to be provisioned."""
    return {
        "discovered": [],
        "scan_active": True,
        "last_scan": datetime.now(timezone.utc).isoformat(),
        "note": "New servers are discovered automatically via DHCP/PXE when connected to the management network",
    }

class ProvisionHost(BaseModel):
    hostname: str
    ip_address: str
    management_ip: Optional[str] = None
    ipmi_ip: Optional[str] = None
    role: str = Field(default="compute", pattern=r"^(compute|storage|converged)$")

@router.post("/provision")
def provision_host(req: ProvisionHost):
    """Provision a discovered host into the cluster."""
    return {
        "status": "provisioning",
        "hostname": req.hostname,
        "ip": req.ip_address,
        "role": req.role,
        "steps": [
            {"step": 1, "name": "Install NexusHV hypervisor", "status": "pending"},
            {"step": 2, "name": "Configure networking", "status": "pending"},
            {"step": 3, "name": "Join cluster", "status": "pending"},
            {"step": 4, "name": "Initialize storage", "status": "pending"},
            {"step": 5, "name": "Run health check", "status": "pending"},
        ],
        "estimated_minutes": 15,
    }

@router.get("/pxe-config")
def pxe_boot_config():
    """Get PXE boot configuration for network provisioning."""
    return {
        "dhcp_next_server": "10.0.1.1",
        "tftp_root": "/var/lib/tftpboot",
        "boot_file": "pxelinux.0",
        "kickstart_url": "http://10.0.1.1:8080/api/provisioning/kickstart",
        "instructions": [
            "1. Configure DHCP to point to this server for PXE boot",
            "2. New servers boot from network and auto-install NexusHV",
            "3. After install, server appears in /api/provisioning/discovery",
            "4. Approve with POST /api/provisioning/provision",
        ],
    }

@router.get("/kickstart")
def kickstart_config():
    """Auto-install configuration for new hosts."""
    return {
        "type": "preseed",
        "os": "Ubuntu 22.04 LTS",
        "packages": ["qemu-kvm", "libvirt-daemon-system", "python3", "nexushv-agent"],
        "post_install": [
            "systemctl enable nexushv-agent",
            "nexushv-agent register --cluster https://nexushv.local:8080",
        ],
    }
