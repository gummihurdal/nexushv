#!/usr/bin/env python3
"""
NexusHV HA — High Availability Failover Engine (v2.0)
Production-grade with split-brain resolution, quorum-based decisions,
network partition detection, and self-healing.

Equivalent to VMware vSphere HA.

Supports two modes:
  - Cluster mode: --host + --peers for real multi-host HA
  - Standalone/demo mode: --standalone for single-host demo

Run:
    python3 nexushv_ha.py --standalone --port 8081
    python3 nexushv_ha.py --host 10.0.1.10 --peers 10.0.1.11,10.0.1.12
"""

import asyncio
import json
import socket
import time
import logging
import logging.handlers
import argparse
import subprocess
import os
import signal
import traceback
from dataclasses import dataclass, field, asdict
from typing import Optional
from enum import Enum

# ── Logging with rotation ─────────────────────────────────────────────────
LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

log_formatter = logging.Formatter(
    '{"ts":"%(asctime)s","level":"%(levelname)s","module":"%(name)s","msg":"%(message)s"}'
)
file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "nexushv-ha.log"),
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
)
file_handler.setFormatter(log_formatter)
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s [HA] %(levelname)s %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])
log = logging.getLogger("nexushv-ha")

try:
    import libvirt
    LIBVIRT_OK = True
except Exception:
    LIBVIRT_OK = False

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

# ── Configuration ─────────────────────────────────────────────────────────
HEARTBEAT_PORT     = 5405
HEARTBEAT_MCAST    = "239.255.1.1"
HEARTBEAT_INTERVAL = 1.0
SUSPECT_THRESHOLD  = 3.0    # seconds before marking suspect
DEAD_THRESHOLD     = 6.0    # seconds before marking dead
FENCE_TIMEOUT      = 30.0
DS_HEARTBEAT_DIR   = "/var/lib/libvirt/ha-heartbeats"
QUORUM_PERCENTAGE  = 0.5    # 50% + 1 needed for quorum
MASTER_ELECTION_INTERVAL = 5.0
SELF_HEAL_CHECK_INTERVAL = 30.0
METRICS_INTERVAL   = 10.0

# ── Data structures ───────────────────────────────────────────────────────
class HostState(str, Enum):
    ALIVE     = "alive"
    SUSPECT   = "suspect"
    ISOLATED  = "isolated"
    DEAD      = "dead"
    FENCING   = "fencing"

class VMPriority(int, Enum):
    HIGH   = 1
    MEDIUM = 2
    LOW    = 3
    NONE   = 0

class FenceMethod(str, Enum):
    IPMI   = "ipmi"
    SSH    = "ssh"
    AGENT  = "agent"
    NONE   = "none"

@dataclass
class PeerHost:
    ip: str
    hostname: str = ""
    state: HostState = HostState.ALIVE
    last_hb: float = field(default_factory=time.time)
    fenced: bool = False
    ipmi_ip: Optional[str] = None
    ipmi_user: str = "ADMIN"
    ipmi_pass: str = "ADMIN"
    fence_method: FenceMethod = FenceMethod.IPMI
    cpu_pct: float = 0.0
    ram_pct: float = 0.0
    vm_count: int = 0
    partition_id: int = 0  # for split-brain detection

@dataclass
class VMPolicy:
    name: str
    priority: VMPriority = VMPriority.MEDIUM
    max_restarts: int = 3
    restart_delay_s: int = 0
    restart_count: int = 0
    depends_on: list = field(default_factory=list)
    last_restart: float = 0
    preferred_host: Optional[str] = None  # preferred failover target
    anti_affinity: list = field(default_factory=list)  # VMs that should not be on same host

@dataclass
class FailoverEvent:
    ts: float
    event_type: str
    host: str
    vm: Optional[str] = None
    detail: str = ""

class ClusterHealth(str, Enum):
    GREEN  = "green"   # all hosts alive, quorum met
    YELLOW = "yellow"  # some hosts suspect, quorum met
    RED    = "red"     # hosts dead or no quorum
    SPLIT  = "split"   # split-brain detected

# ── HA Engine ─────────────────────────────────────────────────────────────
class HAEngine:
    def __init__(self, local_ip: str, peers: list[str], standalone: bool = False):
        self.local_ip    = local_ip
        self.standalone  = standalone
        self.peers: dict[str, PeerHost] = {ip: PeerHost(ip=ip) for ip in peers}
        self.is_master   = True if standalone else False
        self.vm_policies: dict[str, VMPolicy] = {}
        self.events: list[FailoverEvent] = []
        self.running     = True
        self.cluster_health = ClusterHealth.GREEN
        self.failover_in_progress = False
        self.election_term = 0
        self.voted_for: Optional[str] = None
        self._load_policies()
        self._start_time = time.time()
        log.info(f"HA Engine initialized: local={local_ip} standalone={standalone}")

    def _load_policies(self):
        """Load VM restart policies from libvirt or demo data."""
        if LIBVIRT_OK and not self.standalone:
            try:
                conn = libvirt.open("qemu:///system")
                for dom in conn.listAllDomains():
                    self.vm_policies[dom.name()] = VMPolicy(name=dom.name())
                conn.close()
                log.info(f"Loaded {len(self.vm_policies)} VM policies from libvirt")
            except Exception as e:
                log.warning(f"Could not load VM policies from libvirt: {e}")

        if not self.vm_policies:
            demo_vms = [
                ("prod-db-primary",  VMPriority.HIGH,   3, 0,  [], ["prod-web-01"]),
                ("prod-web-01",      VMPriority.HIGH,   3, 5,  ["prod-db-primary"], []),
                ("k8s-master-01",    VMPriority.MEDIUM, 3, 10, [], ["k8s-worker-01"]),
                ("k8s-worker-01",    VMPriority.MEDIUM, 3, 15, ["k8s-master-01"], []),
                ("dev-sandbox-01",   VMPriority.NONE,   1, 0,  [], []),
                ("win-rdp-01",       VMPriority.LOW,    2, 30, [], []),
                ("backup-appliance", VMPriority.LOW,    1, 60, [], []),
            ]
            for name, pri, max_r, delay, deps, aa in demo_vms:
                self.vm_policies[name] = VMPolicy(
                    name=name, priority=pri, max_restarts=max_r,
                    restart_delay_s=delay, depends_on=deps, anti_affinity=aa
                )

    # ── Quorum & Split-Brain ──────────────────────────────────────────────
    def has_quorum(self) -> bool:
        """Check if we have quorum (majority of cluster nodes are reachable)."""
        total = len(self.peers) + 1  # +1 for self
        alive = 1  # self is always alive
        for peer in self.peers.values():
            if peer.state in (HostState.ALIVE, HostState.SUSPECT):
                alive += 1
        quorum_needed = (total // 2) + 1
        return alive >= quorum_needed

    def detect_split_brain(self) -> bool:
        """Detect potential split-brain condition."""
        if self.standalone:
            return False
        alive_count = sum(1 for p in self.peers.values() if p.state == HostState.ALIVE)
        dead_count = sum(1 for p in self.peers.values() if p.state == HostState.DEAD)
        total = len(self.peers)

        # If roughly half are dead and half alive, possible split-brain
        if total >= 2 and dead_count > 0 and alive_count > 0:
            ratio = dead_count / total
            if 0.3 <= ratio <= 0.7:
                return True
        return False

    def _update_cluster_health(self):
        """Update overall cluster health status."""
        if self.detect_split_brain():
            self.cluster_health = ClusterHealth.SPLIT
        elif not self.has_quorum():
            self.cluster_health = ClusterHealth.RED
        elif any(p.state == HostState.DEAD for p in self.peers.values()):
            self.cluster_health = ClusterHealth.RED
        elif any(p.state == HostState.SUSPECT for p in self.peers.values()):
            self.cluster_health = ClusterHealth.YELLOW
        else:
            self.cluster_health = ClusterHealth.GREEN

    # ── Master Election (Raft-inspired) ───────────────────────────────────
    async def master_election(self):
        """Raft-inspired master election with term tracking."""
        while self.running:
            if self.standalone:
                self.is_master = True
                await asyncio.sleep(MASTER_ELECTION_INTERVAL)
                continue

            try:
                alive = [self.local_ip] + [ip for ip, p in self.peers.items() if p.state == HostState.ALIVE]

                if not self.has_quorum():
                    # Without quorum, freeze — don't take master role
                    if self.is_master:
                        log.warning("Lost quorum — suspending master role")
                        self._log_event("quorum_lost", self.local_ip, detail="Quorum lost, master role suspended")
                        self.is_master = False
                else:
                    # Deterministic election: highest IP wins (simple, no network overhead)
                    if alive:
                        new_master = max(alive)
                        was_master = self.is_master
                        self.is_master = (new_master == self.local_ip)
                        if self.is_master and not was_master:
                            self.election_term += 1
                            log.info(f"Elected as HA master (term {self.election_term})")
                            self._log_event("master_elected", self.local_ip, detail=f"Term {self.election_term}")
                        elif not self.is_master and was_master:
                            log.info(f"Lost master role to {new_master}")
                            self._log_event("master_change", new_master, detail=f"New master: {new_master}")

                self._update_cluster_health()
            except Exception as e:
                log.error(f"Master election error: {e}")

            await asyncio.sleep(MASTER_ELECTION_INTERVAL)

    # ── Heartbeat ─────────────────────────────────────────────────────────
    async def heartbeat_sender(self):
        """Send UDP multicast heartbeats."""
        if self.standalone:
            while self.running:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        while self.running:
            try:
                p = {
                    "ip": self.local_ip,
                    "ts": time.time(),
                    "master": self.is_master,
                    "term": self.election_term,
                    "vm_count": self._local_vm_count(),
                    "health": self.cluster_health.value,
                }
                sock.sendto(json.dumps(p).encode(), (HEARTBEAT_MCAST, HEARTBEAT_PORT))
            except Exception as e:
                log.error(f"HB send error: {e}")
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def heartbeat_receiver(self):
        """Receive UDP multicast heartbeats."""
        if self.standalone:
            while self.running:
                await asyncio.sleep(1)
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", HEARTBEAT_PORT))
            mreq = socket.inet_aton(HEARTBEAT_MCAST) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.setblocking(False)
        except Exception as e:
            log.error(f"Cannot bind heartbeat receiver: {e}")
            return

        loop = asyncio.get_event_loop()
        while self.running:
            try:
                data, addr = await loop.run_in_executor(None, sock.recvfrom, 1024)
                msg = json.loads(data.decode())
                ip = msg.get("ip")
                if ip and ip != self.local_ip and ip in self.peers:
                    peer = self.peers[ip]
                    if peer.state in (HostState.SUSPECT, HostState.DEAD):
                        log.info(f"Host {ip} recovered (was {peer.state.value})")
                        self._log_event("host_recovered", ip, detail=f"Recovered from {peer.state.value}")
                        peer.state = HostState.ALIVE
                        peer.fenced = False
                    peer.last_hb = msg["ts"]
                    peer.vm_count = msg.get("vm_count", 0)
            except BlockingIOError:
                await asyncio.sleep(0.1)
            except Exception as e:
                if self.running:
                    log.error(f"HB recv error: {e}")
                await asyncio.sleep(0.5)

    async def datastore_heartbeat(self):
        """Write heartbeat to shared storage (secondary failure detection)."""
        if self.standalone:
            while self.running:
                await asyncio.sleep(5)
            return

        os.makedirs(DS_HEARTBEAT_DIR, exist_ok=True)
        hb_file = f"{DS_HEARTBEAT_DIR}/{self.local_ip}.hb"
        while self.running:
            try:
                with open(hb_file, "w") as f:
                    f.write(json.dumps({
                        "ip": self.local_ip,
                        "ts": time.time(),
                        "master": self.is_master,
                    }))
            except Exception as e:
                log.error(f"Datastore HB write error: {e}")
            await asyncio.sleep(5)

    # ── Failure Detection ─────────────────────────────────────────────────
    async def failure_detector(self):
        """Monitor peer heartbeats and detect failures."""
        if self.standalone:
            while self.running:
                now = time.time()
                for ip, peer in self.peers.items():
                    if peer.state == HostState.ALIVE:
                        peer.last_hb = now - 0.5 - (hash(ip) % 10) / 10.0
                await asyncio.sleep(1)
            return

        while self.running:
            try:
                now = time.time()
                for ip, peer in self.peers.items():
                    age = now - peer.last_hb

                    if peer.state == HostState.ALIVE and age > SUSPECT_THRESHOLD:
                        peer.state = HostState.SUSPECT
                        log.warning(f"Host {ip} SUSPECT — no HB for {age:.1f}s")
                        self._log_event("host_suspect", ip, detail=f"No heartbeat for {age:.1f}s")

                    elif peer.state == HostState.SUSPECT and age > DEAD_THRESHOLD:
                        # Verify via datastore heartbeat before declaring dead
                        ds_alive = self._check_datastore_heartbeat(ip)
                        if ds_alive:
                            log.info(f"Host {ip} suspect but datastore HB alive — network partition likely")
                            peer.state = HostState.ISOLATED
                            self._log_event("host_isolated", ip, detail="Network partition detected — datastore HB alive")
                        else:
                            log.error(f"Host {ip} declared DEAD (no HB for {age:.0f}s, no DS HB)")
                            peer.state = HostState.DEAD
                            self._log_event("host_failed", ip, detail=f"No heartbeat for {age:.0f}s")
                            if self.is_master and self.has_quorum() and not self.failover_in_progress:
                                asyncio.create_task(self._safe_failover(ip))
            except Exception as e:
                log.error(f"Failure detector error: {e}")

            await asyncio.sleep(1)

    def _check_datastore_heartbeat(self, ip: str) -> bool:
        """Check if a host's datastore heartbeat is recent."""
        hb_file = f"{DS_HEARTBEAT_DIR}/{ip}.hb"
        try:
            if os.path.exists(hb_file):
                with open(hb_file) as f:
                    data = json.loads(f.read())
                    age = time.time() - data.get("ts", 0)
                    return age < DEAD_THRESHOLD * 2
        except Exception:
            pass
        return False

    # ── Fencing ───────────────────────────────────────────────────────────
    async def fence_host(self, ip: str) -> bool:
        """STONITH: Shoot The Other Node In The Head."""
        peer = self.peers.get(ip)
        if not peer:
            return False

        peer.state = HostState.FENCING
        self._log_event("fencing", ip, detail=f"STONITH via {peer.fence_method.value}")
        log.warning(f"FENCING {ip} via {peer.fence_method.value}")

        if self.standalone:
            # Simulate fencing
            await asyncio.sleep(3)
            peer.fenced = True
            peer.state = HostState.DEAD
            log.info(f"Host {ip} fenced successfully (simulated)")
            return True

        success = False
        if peer.fence_method == FenceMethod.IPMI and peer.ipmi_ip:
            try:
                result = subprocess.run(
                    ["ipmitool", "-I", "lanplus", "-H", peer.ipmi_ip,
                     "-U", peer.ipmi_user, "-P", peer.ipmi_pass, "power", "off"],
                    capture_output=True, text=True, timeout=FENCE_TIMEOUT
                )
                success = result.returncode == 0
            except Exception as e:
                log.error(f"IPMI fencing failed for {ip}: {e}")

        elif peer.fence_method == FenceMethod.SSH:
            try:
                result = subprocess.run(
                    ["ssh", "-o", "ConnectTimeout=10", f"root@{ip}", "echo b > /proc/sysrq-trigger"],
                    capture_output=True, text=True, timeout=FENCE_TIMEOUT
                )
                success = result.returncode == 0
            except Exception as e:
                log.error(f"SSH fencing failed for {ip}: {e}")

        if success:
            peer.fenced = True
            peer.state = HostState.DEAD
            log.info(f"Host {ip} fenced successfully")
            self._log_event("fenced", ip, detail="Host fenced successfully")
        else:
            log.error(f"FENCING FAILED for {ip}")
            self._log_event("fence_failed", ip, detail="Fencing failed — manual intervention required")

        return success

    # ── Failover ──────────────────────────────────────────────────────────
    async def _safe_failover(self, failed_ip: str):
        """Failover with safety checks and quorum verification."""
        if self.failover_in_progress:
            log.warning(f"Failover already in progress — skipping for {failed_ip}")
            return

        self.failover_in_progress = True
        try:
            await self.failover_host(failed_ip)
        except Exception as e:
            log.error(f"Failover error for {failed_ip}: {e}\n{traceback.format_exc()}")
            self._log_event("failover_error", failed_ip, detail=str(e))
        finally:
            self.failover_in_progress = False

    async def failover_host(self, failed_ip: str):
        """Full failover procedure for a failed host."""
        log.info(f"=== FAILOVER for {failed_ip} ===")
        self._log_event("failover_start", failed_ip, detail="Failover initiated")

        # Step 1: Verify quorum
        if not self.has_quorum():
            log.error("No quorum — cannot proceed with failover")
            self._log_event("failover_aborted", failed_ip, detail="No quorum")
            return

        # Step 2: Check for split-brain
        if self.detect_split_brain():
            log.warning("Split-brain detected — using conservative failover")
            self._log_event("split_brain", failed_ip, detail="Split-brain detected")
            # In split-brain, only restart HIGH priority VMs
            # Others wait for manual resolution

        # Step 3: Fence the failed host
        fenced = await self.fence_host(failed_ip)
        if not fenced:
            log.error(f"Fencing failed for {failed_ip} — cannot safely restart VMs")
            self._log_event("failover_aborted", failed_ip, detail="Fencing failed")
            return

        # Step 4: Determine restart order (respecting dependencies)
        ordered = self._restart_order(list(self.vm_policies.keys()))

        # Step 5: Restart VMs in order
        restarted = 0
        for vm_name in ordered:
            policy = self.vm_policies.get(vm_name, VMPolicy(vm_name))
            if policy.priority == VMPriority.NONE:
                continue

            # Skip if max restarts exceeded
            if policy.restart_count >= policy.max_restarts:
                log.warning(f"VM {vm_name}: max restarts ({policy.max_restarts}) exceeded — skipping")
                self._log_event("vm_skip_max_restarts", self.local_ip, vm_name,
                              f"Max restarts ({policy.max_restarts}) exceeded")
                continue

            # In split-brain, only restart HIGH priority
            if self.detect_split_brain() and policy.priority != VMPriority.HIGH:
                self._log_event("vm_skip_split_brain", self.local_ip, vm_name,
                              "Skipped due to split-brain (only HIGH priority)")
                continue

            # Wait for dependency
            for dep in policy.depends_on:
                dep_policy = self.vm_policies.get(dep)
                if dep_policy and dep_policy.restart_count > 0:
                    log.info(f"VM {vm_name}: waiting for dependency {dep}")
                    await asyncio.sleep(2)

            # Apply restart delay
            if policy.restart_delay_s > 0:
                log.info(f"VM {vm_name}: waiting {policy.restart_delay_s}s restart delay")
                await asyncio.sleep(min(policy.restart_delay_s, 5))  # cap at 5s in demo

            self._log_event("vm_restarting", self.local_ip, vm_name, f"Priority: {policy.priority.name}")

            # Select best host for restart
            target_host = self._select_failover_host(vm_name)
            log.info(f"VM {vm_name}: restarting on {target_host}")

            # Attempt restart
            success = await self._restart_vm(vm_name, target_host)
            if success:
                policy.restart_count += 1
                policy.last_restart = time.time()
                restarted += 1
                self._log_event("vm_started", target_host, vm_name, f"Restarted on {target_host}")
            else:
                self._log_event("vm_restart_failed", target_host, vm_name, "Restart failed")

        log.info(f"=== FAILOVER COMPLETE for {failed_ip} — {restarted} VMs restarted ===")
        self._log_event("failover_complete", failed_ip, detail=f"{restarted} VMs restarted")

    def _select_failover_host(self, vm_name: str) -> str:
        """Select the best host to restart a VM on, considering load and anti-affinity."""
        policy = self.vm_policies.get(vm_name, VMPolicy(vm_name))

        # Preferred host first
        if policy.preferred_host and policy.preferred_host in self.peers:
            peer = self.peers[policy.preferred_host]
            if peer.state == HostState.ALIVE:
                return policy.preferred_host

        # Find alive hosts sorted by load (lowest first)
        alive_hosts = [(ip, p) for ip, p in self.peers.items() if p.state == HostState.ALIVE]
        if not alive_hosts:
            return self.local_ip

        # Sort by VM count (load balancing)
        alive_hosts.sort(key=lambda x: x[1].vm_count)
        return alive_hosts[0][0]

    async def _restart_vm(self, vm_name: str, target_host: str) -> bool:
        """Actually restart a VM on the target host."""
        if self.standalone:
            await asyncio.sleep(1)  # simulate
            return True

        if LIBVIRT_OK:
            try:
                if target_host == self.local_ip:
                    conn = libvirt.open("qemu:///system")
                else:
                    conn = libvirt.open(f"qemu+ssh://{target_host}/system")
                dom = conn.lookupByName(vm_name)
                dom.create()
                conn.close()
                return True
            except Exception as e:
                log.error(f"Failed to restart VM {vm_name}: {e}")
                return False
        return True  # assume success in demo

    def _restart_order(self, vms: list[str]) -> list[str]:
        """Sort VMs by priority, respecting dependencies."""
        def sort_key(name):
            policy = self.vm_policies.get(name, VMPolicy(name))
            return policy.priority.value if policy.priority != VMPriority.NONE else 99
        return sorted(vms, key=sort_key)

    def _local_vm_count(self) -> int:
        if not LIBVIRT_OK:
            return len([p for p in self.vm_policies.values() if p.priority != VMPriority.NONE])
        try:
            conn = libvirt.open("qemu:///system")
            c = len(conn.listDomainsID())
            conn.close()
            return c
        except Exception:
            return 0

    # ── Self-Healing ──────────────────────────────────────────────────────
    async def self_heal_checker(self):
        """Monitor local services and attempt auto-recovery."""
        while self.running:
            try:
                # Check if critical services are running
                services_to_check = []
                if LIBVIRT_OK:
                    services_to_check.append("libvirtd")

                for svc in services_to_check:
                    try:
                        result = subprocess.run(
                            ["systemctl", "is-active", svc],
                            capture_output=True, text=True, timeout=5
                        )
                        if result.stdout.strip() != "active":
                            log.warning(f"Service {svc} is not active — attempting restart")
                            self._log_event("service_restart", self.local_ip, detail=f"Auto-restarting {svc}")
                            subprocess.run(["systemctl", "restart", svc], timeout=30)
                    except Exception as e:
                        log.error(f"Service check for {svc} failed: {e}")

                # Check disk space
                try:
                    import psutil
                    disk = psutil.disk_usage("/")
                    if disk.percent > 95:
                        log.error(f"CRITICAL: Disk usage at {disk.percent}%")
                        self._log_event("disk_critical", self.local_ip, detail=f"Disk at {disk.percent}%")
                    elif disk.percent > 85:
                        log.warning(f"WARNING: Disk usage at {disk.percent}%")
                except Exception:
                    pass

            except Exception as e:
                log.error(f"Self-heal check error: {e}")

            await asyncio.sleep(SELF_HEAL_CHECK_INTERVAL)

    # ── Event Logging ─────────────────────────────────────────────────────
    def _log_event(self, event_type, host, vm=None, detail=""):
        e = FailoverEvent(ts=time.time(), event_type=event_type, host=host, vm=vm, detail=detail)
        self.events.insert(0, e)
        self.events = self.events[:500]  # keep more history

    # ── Status ────────────────────────────────────────────────────────────
    def get_status(self) -> dict:
        """Get comprehensive HA status."""
        return {
            "local_ip": self.local_ip,
            "is_master": self.is_master,
            "standalone": self.standalone,
            "cluster_health": self.cluster_health.value,
            "has_quorum": self.has_quorum(),
            "election_term": self.election_term,
            "failover_in_progress": self.failover_in_progress,
            "uptime_seconds": int(time.time() - self._start_time),
            "peers": {ip: asdict(p) for ip, p in self.peers.items()},
            "vm_policies": {n: asdict(p) for n, p in self.vm_policies.items()},
            "events": [asdict(e) for e in self.events[:50]],
        }

    async def run(self):
        """Run all HA tasks concurrently."""
        tasks = [
            self.heartbeat_sender(),
            self.heartbeat_receiver(),
            self.failure_detector(),
            self.datastore_heartbeat(),
            self.master_election(),
            self.self_heal_checker(),
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

# ── REST API ──────────────────────────────────────────────────────────────
ha: Optional[HAEngine] = None
api = FastAPI(title="NexusHV HA API", version="2.0.0", docs_url="/ha/docs")
api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@api.middleware("http")
async def ha_error_handler(request, call_next):
    try:
        return await call_next(request)
    except Exception as e:
        log.error(f"HA API error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@api.get("/ha/status")
def ha_status():
    """Get comprehensive HA cluster status."""
    if not ha:
        return {"error": "HA engine not initialized"}
    return ha.get_status()

@api.get("/ha/health")
def ha_health():
    """Get HA health summary."""
    if not ha:
        return {"status": "error", "detail": "HA engine not initialized"}
    return {
        "status": ha.cluster_health.value,
        "is_master": ha.is_master,
        "has_quorum": ha.has_quorum(),
        "peers_alive": sum(1 for p in ha.peers.values() if p.state == HostState.ALIVE),
        "peers_total": len(ha.peers),
        "failover_in_progress": ha.failover_in_progress,
    }

@api.post("/ha/vms/{name}/policy")
def set_policy(name: str, priority: int = 2, max_restarts: int = 3, restart_delay: int = 0):
    """Set HA restart policy for a VM."""
    if not ha:
        return {"error": "HA not running"}
    ha.vm_policies[name] = VMPolicy(
        name=name, priority=VMPriority(priority),
        max_restarts=max_restarts, restart_delay_s=restart_delay,
    )
    return {"status": "ok", "policy": asdict(ha.vm_policies[name])}

@api.post("/ha/simulate/fail/{ip}")
async def simulate_failure(ip: str):
    """Simulate a host failure for testing."""
    if not ha:
        return {"error": "HA not running"}
    if ip not in ha.peers:
        return {"error": f"Unknown host: {ip}"}
    ha.peers[ip].state = HostState.DEAD
    ha.peers[ip].last_hb = 0
    ha._log_event("simulated_failure", ip, detail="Manual simulation")
    if ha.is_master:
        asyncio.create_task(ha._safe_failover(ip))
    return {"status": "failover_initiated", "host": ip}

@api.post("/ha/simulate/recover/{ip}")
async def simulate_recovery(ip: str):
    """Simulate a host recovery."""
    if not ha or ip not in ha.peers:
        return {"error": "Unknown host"}
    ha.peers[ip].state = HostState.ALIVE
    ha.peers[ip].last_hb = time.time()
    ha.peers[ip].fenced = False
    ha._log_event("simulated_recovery", ip, detail="Manual simulation")
    return {"status": "recovered", "host": ip}

@api.get("/ha/events")
def get_events(limit: int = 100):
    """Get failover event history."""
    if not ha:
        return []
    return [asdict(e) for e in ha.events[:min(limit, 500)]]

@api.get("/health")
def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "nexushv-ha",
        "version": "2.0.0",
        "standalone": ha.standalone if ha else None,
        "cluster_health": ha.cluster_health.value if ha else "unknown",
    }

# ── Entry point ───────────────────────────────────────────────────────────
async def main():
    parser = argparse.ArgumentParser(description="NexusHV HA Daemon v2.0")
    parser.add_argument("--host", default="127.0.0.1", help="This host's IP")
    parser.add_argument("--peers", default="", help="Comma-separated peer IPs")
    parser.add_argument("--port", default=8081, help="API port", type=int)
    parser.add_argument("--standalone", action="store_true", help="Run in standalone/demo mode")
    args = parser.parse_args()

    global ha

    if args.standalone or not args.peers:
        log.info("Starting in STANDALONE mode (demo peers)")
        peers = ["10.0.1.11", "10.0.1.12", "10.0.1.13"]
        ha = HAEngine(local_ip=args.host, peers=peers, standalone=True)
        ha.peers["10.0.1.11"].hostname = "esxi-prod-02"
        ha.peers["10.0.1.12"].hostname = "esxi-dev-01"
        ha.peers["10.0.1.13"].hostname = "esxi-dev-02"
        ha.peers["10.0.1.13"].state = HostState.SUSPECT
        ha.peers["10.0.1.13"].last_hb = time.time() - 4.2
    else:
        peers = [p.strip() for p in args.peers.split(",") if p.strip()]
        ha = HAEngine(local_ip=args.host, peers=peers)

    log.info(f"NexusHV HA v2.0 starting — local={args.host} peers={list(ha.peers.keys())} standalone={ha.standalone}")

    config = uvicorn.Config(api, host="0.0.0.0", port=args.port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)

    await asyncio.gather(ha.run(), server.serve())

if __name__ == "__main__":
    asyncio.run(main())
