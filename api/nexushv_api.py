#!/usr/bin/env python3
"""
NexusHV — Bare Metal Hypervisor Management API
Backend for the vSphere-compatible management console.

Requirements:
    pip install fastapi uvicorn libvirt-python psutil websockets

Deploy on bare metal Ubuntu/Debian:
    apt install qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils
    systemctl enable --now libvirtd
    python3 nexushv_api.py
"""

import asyncio, json, subprocess, psutil, time
from typing import Optional
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import libvirt

app = FastAPI(title="NexusHV API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── libvirt connection ─────────────────────────────────────────────────────
def get_conn():
    try:
        return libvirt.open("qemu:///system")
    except libvirt.libvirtError as e:
        raise HTTPException(503, f"Cannot connect to hypervisor: {e}")

# ── Models ─────────────────────────────────────────────────────────────────
class VMCreate(BaseModel):
    name: str
    os: str = "ubuntu22.04"
    cpu: int = 2
    ram_gb: int = 4
    disk_gb: int = 50
    network: str = "default"
    iso_path: Optional[str] = None

class VMAction(BaseModel):
    action: str  # start | stop | reboot | suspend | resume | force-stop

class MigrateRequest(BaseModel):
    dest_host: str          # e.g. "qemu+ssh://10.0.1.11/system"
    dest_uri: Optional[str] = None
    live: bool = True
    bandwidth_mbps: int = 0  # 0 = unlimited

class SnapshotCreate(BaseModel):
    name: str
    description: str = ""

# ── VM State mapping ───────────────────────────────────────────────────────
STATE_MAP = {
    libvirt.VIR_DOMAIN_RUNNING:    "poweredOn",
    libvirt.VIR_DOMAIN_BLOCKED:    "poweredOn",
    libvirt.VIR_DOMAIN_PAUSED:     "suspended",
    libvirt.VIR_DOMAIN_SHUTDOWN:   "poweredOff",
    libvirt.VIR_DOMAIN_SHUTOFF:    "poweredOff",
    libvirt.VIR_DOMAIN_CRASHED:    "error",
    libvirt.VIR_DOMAIN_PMSUSPENDED:"suspended",
}

def vm_info(dom):
    state, _ = dom.state()
    info = dom.info()  # [state, maxMem, mem, nrVirtCpu, cpuTime]
    try:
        stats = dom.getCPUStats(True)[0]
        cpu_pct = round(stats.get("cpu_time", 0) / 1e9 / psutil.cpu_count(), 2)
    except Exception:
        cpu_pct = 0
    return {
        "id":       dom.UUIDString(),
        "name":     dom.name(),
        "state":    STATE_MAP.get(state, "unknown"),
        "cpu":      info[3],
        "ram_mb":   info[1] // 1024,
        "cpu_pct":  cpu_pct,
        "ram_used_pct": round(info[2] / info[1] * 100, 1) if info[1] else 0,
        "persistent": dom.isPersistent(),
        "autostart":  dom.autostart(),
    }

# ── Routes: Virtual Machines ───────────────────────────────────────────────
@app.get("/api/vms")
def list_vms():
    conn = get_conn()
    try:
        domains = conn.listAllDomains()
        return [vm_info(d) for d in domains]
    finally:
        conn.close()

@app.get("/api/vms/{name}")
def get_vm(name: str):
    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        return vm_info(dom)
    except libvirt.libvirtError:
        raise HTTPException(404, f"VM '{name}' not found")
    finally:
        conn.close()

@app.post("/api/vms")
def create_vm(req: VMCreate):
    """
    Create a new VM using QEMU/KVM with sensible defaults.
    Disk image created at /var/lib/libvirt/images/<name>.qcow2
    """
    disk_path = f"/var/lib/libvirt/images/{req.name}.qcow2"

    # Create disk image
    result = subprocess.run(
        ["qemu-img", "create", "-f", "qcow2", disk_path, f"{req.disk_gb}G"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise HTTPException(500, f"Disk creation failed: {result.stderr}")

    # virt-install command
    cmd = [
        "virt-install",
        "--name",        req.name,
        "--memory",      str(req.ram_gb * 1024),
        "--vcpus",       str(req.cpu),
        "--disk",        f"path={disk_path},format=qcow2,bus=virtio",
        "--network",     f"network={req.network},model=virtio",
        "--graphics",    "vnc,listen=127.0.0.1",
        "--video",       "virtio",
        "--channel",     "unix,target_type=virtio,name=org.qemu.guest_agent.0",
        "--noautoconsole",
        "--import" if not req.iso_path else "--wait=0",
    ]
    if req.iso_path:
        cmd += ["--cdrom", req.iso_path, "--os-variant", req.os.replace(" ","").lower()]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(500, f"VM creation failed: {result.stderr}")

    return {"status": "created", "name": req.name, "disk": disk_path}

@app.post("/api/vms/{name}/action")
def vm_action(name: str, req: VMAction):
    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        action = req.action

        if action == "start":
            dom.create()
        elif action == "stop":
            dom.shutdown()         # graceful ACPI shutdown
        elif action == "force-stop":
            dom.destroy()          # immediate kill
        elif action == "reboot":
            dom.reboot()
        elif action == "suspend":
            dom.suspend()
        elif action == "resume":
            dom.resume()
        else:
            raise HTTPException(400, f"Unknown action: {action}")

        return {"status": "ok", "vm": name, "action": action}
    except libvirt.libvirtError as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

@app.delete("/api/vms/{name}")
def delete_vm(name: str, delete_disk: bool = False):
    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        state, _ = dom.state()
        if state == libvirt.VIR_DOMAIN_RUNNING:
            dom.destroy()
        if delete_disk:
            dom.undefineFlags(libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA |
                              libvirt.VIR_DOMAIN_UNDEFINE_NVRAM)
        else:
            dom.undefine()
        return {"status": "deleted", "vm": name}
    except libvirt.libvirtError as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

# ── vMotion: Live Migration ────────────────────────────────────────────────
@app.post("/api/vms/{name}/migrate")
def migrate_vm(name: str, req: MigrateRequest):
    """
    Live migrate a running VM to another host (vMotion equivalent).
    Both hosts must share storage (NFS/SAN) or use BTRFS/ZFS replication.

    Live migration uses libvirt's virDomainMigrateToURI3 with:
      - VIR_MIGRATE_LIVE        → zero-downtime pre-copy memory transfer
      - VIR_MIGRATE_PERSIST_DEST → persist VM definition on destination
      - VIR_MIGRATE_UNDEFINE_SOURCE → remove from source after success
      - VIR_MIGRATE_COMPRESSED  → compress memory pages in-flight
    """
    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        state, _ = dom.state()

        dest_conn = libvirt.open(req.dest_host)
        if not dest_conn:
            raise HTTPException(503, f"Cannot connect to destination: {req.dest_host}")

        flags = (
            libvirt.VIR_MIGRATE_LIVE |
            libvirt.VIR_MIGRATE_PERSIST_DEST |
            libvirt.VIR_MIGRATE_UNDEFINE_SOURCE |
            libvirt.VIR_MIGRATE_COMPRESSED
        )

        # Optional: peer-to-peer for direct host-to-host transfer
        if req.dest_uri:
            flags |= libvirt.VIR_MIGRATE_PEER2PEER

        params = {}
        if req.bandwidth_mbps:
            params[libvirt.VIR_MIGRATE_PARAM_BANDWIDTH] = req.bandwidth_mbps * 1024 * 1024

        # The actual migration — this is the vMotion equivalent
        new_dom = dom.migrate(
            dest_conn,
            flags=flags,
            dname=name,
            bandwidth=req.bandwidth_mbps
        )

        if new_dom is None:
            raise HTTPException(500, "Migration failed — no domain returned")

        dest_conn.close()
        return {
            "status":      "migrated",
            "vm":          name,
            "source":      "localhost",
            "destination": req.dest_host,
            "live":        True,
        }
    except libvirt.libvirtError as e:
        raise HTTPException(500, f"Migration error: {e}")
    finally:
        conn.close()

# ── Snapshots ──────────────────────────────────────────────────────────────
@app.get("/api/vms/{name}/snapshots")
def list_snapshots(name: str):
    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        snaps = dom.listAllSnapshots()
        return [{"name": s.getName(), "desc": s.getXMLDesc()} for s in snaps]
    except libvirt.libvirtError as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

@app.post("/api/vms/{name}/snapshots")
def create_snapshot(name: str, req: SnapshotCreate):
    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        xml = f"""<domainsnapshot>
          <name>{req.name}</name>
          <description>{req.description}</description>
        </domainsnapshot>"""
        snap = dom.snapshotCreateXML(xml, 0)
        return {"status": "created", "snapshot": snap.getName()}
    except libvirt.libvirtError as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

@app.post("/api/vms/{name}/snapshots/{snap}/revert")
def revert_snapshot(name: str, snap: str):
    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        snapshot = dom.snapshotLookupByName(snap)
        dom.revertToSnapshot(snapshot)
        return {"status": "reverted", "snapshot": snap}
    except libvirt.libvirtError as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

# ── Host / Hypervisor Info ─────────────────────────────────────────────────
@app.get("/api/hosts/local")
def local_host_info():
    conn = get_conn()
    try:
        info = conn.getInfo()  # [arch, mem_mb, cpus, mhz, nodes, sockets, cores, threads]
        return {
            "hostname":     subprocess.getoutput("hostname"),
            "arch":         info[0],
            "cpu_count":    info[2],
            "cpu_mhz":      info[3],
            "ram_total_gb": round(info[1] / 1024, 1),
            "ram_used_gb":  round((info[1] - psutil.virtual_memory().available // 1024**2) / 1024, 1),
            "cpu_pct":      psutil.cpu_percent(interval=1),
            "libvirt_ver":  conn.getLibVersion(),
            "hypervisor":   conn.getType(),
            "running_vms":  len(conn.listDomainsID()),
            "total_vms":    len(conn.listAllDomains()),
        }
    finally:
        conn.close()

# ── Storage Pools ──────────────────────────────────────────────────────────
@app.get("/api/storage")
def list_storage():
    conn = get_conn()
    try:
        pools = conn.listAllStoragePools()
        result = []
        for p in pools:
            p.refresh(0)
            info = p.info()  # [state, capacity, allocation, available]
            result.append({
                "name":       p.name(),
                "state":      "active" if info[0] == 2 else "inactive",
                "capacity_gb": round(info[1] / 1024**3, 1),
                "used_gb":     round(info[2] / 1024**3, 1),
                "free_gb":     round(info[3] / 1024**3, 1),
            })
        return result
    finally:
        conn.close()

# ── Networks ───────────────────────────────────────────────────────────────
@app.get("/api/networks")
def list_networks():
    conn = get_conn()
    try:
        nets = conn.listAllNetworks()
        return [{
            "name":   n.name(),
            "active": n.isActive(),
            "bridge": n.bridgeName() if n.isActive() else None,
        } for n in nets]
    finally:
        conn.close()

# ── Real-time metrics via WebSocket ───────────────────────────────────────
@app.websocket("/ws/metrics")
async def metrics_ws(websocket: WebSocket):
    """
    Push live host + per-VM metrics every 2 seconds to the dashboard.
    The frontend connects here to update sparklines in real-time.
    """
    await websocket.accept()
    conn = get_conn()
    try:
        while True:
            payload = {
                "ts":       time.time(),
                "host_cpu": psutil.cpu_percent(),
                "host_ram": psutil.virtual_memory().percent,
                "vms":      [],
            }
            for dom in conn.listAllDomains():
                st, _ = dom.state()
                if st == libvirt.VIR_DOMAIN_RUNNING:
                    try:
                        stats = dom.getCPUStats(True)[0]
                        payload["vms"].append({
                            "name":    dom.name(),
                            "cpu_pct": round(stats.get("cpu_time", 0) / 1e9, 2),
                        })
                    except Exception:
                        pass
            await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(2)
    except Exception:
        pass
    finally:
        conn.close()

# ── VNC Console Proxy ──────────────────────────────────────────────────────
@app.get("/api/vms/{name}/console")
def get_console(name: str):
    """
    Returns VNC connection details. Use noVNC in the frontend to embed console.
    """
    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        xml = dom.XMLDesc(0)
        # Parse VNC port from XML (in production use lxml)
        import re
        m = re.search(r"type='vnc'[^/]*/?>.*?port='(\d+)'", xml, re.DOTALL)
        if not m:
            m = re.search(r"port='(\d+)'", xml)
        port = int(m.group(1)) if m else 5900
        return {
            "host":     "localhost",
            "port":     port,
            "novnc_url": f"http://localhost:6080/vnc.html?host=localhost&port={port}&autoconnect=true"
        }
    except libvirt.libvirtError as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

# ── Health check ───────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "hypervisor": "kvm", "product": "NexusHV"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, reload=False)
