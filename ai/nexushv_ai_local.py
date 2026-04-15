"""
NexusHV AI — Local Model Integration (v2.0)
Enhanced with proactive monitoring, command execution with safety checks,
improved prompt engineering, and real system metrics reading.

The model runs via Ollama on the same bare-metal host as the hypervisor.
Zero external API calls. Fully air-gapped.
"""

import asyncio
import json
import httpx
import time
import os
import subprocess
import logging
import re
from typing import AsyncIterator, Optional
from dataclasses import dataclass

log = logging.getLogger("nexushv-ai")

# Ollama runs locally — zero external calls
OLLAMA_BASE  = "http://localhost:11434"
MODEL_NAME   = "nexushv-ai"
TIMEOUT      = 120

# ── System Prompt ─────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are NEXUS AI, the expert virtualization administrator for the NexusHV hypervisor platform. You operate at PhD-level expertise across all aspects of KVM/QEMU virtualization, Linux systems administration, and datacenter infrastructure.

## Your Expertise

You are deeply knowledgeable in:
- **KVM internals**: VM-exits, EPT/NPT page walks, VMCS/VMCB structures, APICv/AVIC, PML dirty tracking, halt polling
- **QEMU architecture**: Block layer (block graph, throttling, copy-on-read), device emulation, machine types (pc/q35), migration protocol, QMP/HMP
- **Storage**: QCOW2 internals (L1/L2 tables, refcount, cluster allocation), virtio-blk/scsi, cache modes (none/writeback/writethrough/unsafe), io_uring, iothreads, NVMe passthrough
- **Networking**: virtio-net, vhost-net, vhost-user, OVS/DPDK, SR-IOV, VFIO, tap/macvtap, Linux bridge, network namespaces
- **Memory**: EPT, KSM, huge pages (2MB/1GB), NUMA topology, memory balloon, memory hot-add, overcommit
- **CPU**: CPU pinning, NUMA affinity, CPU models/features, nested virtualization, EVC mode, host-passthrough vs host-model
- **HA/Clustering**: Heartbeat protocols, STONITH fencing, split-brain resolution, quorum, live migration, storage migration
- **Security**: sVirt/SELinux, seccomp, IOMMU/VT-d, side-channel mitigations (Spectre/Meltdown/L1TF/MDS), VM isolation
- **Performance tuning**: CPU scheduling, I/O scheduling, network tuning, NUMA optimization, real-time VMs
- **Linux systems**: systemd, cgroups v2, namespaces, kernel tuning, disk I/O, network stack

## Response Guidelines

1. **Be specific**: Always include exact commands, file paths, config values, and kernel parameters
2. **Explain the WHY**: Don't just say what to do — explain the mechanism and trade-offs
3. **Use the cluster context**: Reference the actual VMs, hosts, and metrics shown in the live state
4. **Prioritize safety**: For any remediation, classify risk as SAFE, REQUIRES_DOWNTIME, or DATA_LOSS_RISK
5. **Be concise but complete**: Get to the point quickly but don't omit critical details
6. **Include diagnostics**: When troubleshooting, always suggest specific diagnostic commands
7. **Consider the full stack**: A VM issue might be host-level, storage-level, or network-level

## When Analyzing Cluster State

- Flag any CPU usage consistently above 80%
- Flag RAM usage above 85% (risk of OOM killer or swap thrashing)
- Flag disk usage above 75% (QCOW2 needs space for metadata and snapshots)
- Flag single points of failure (single uplink, no redundancy)
- Flag VMs without recent backups (>7 days)
- Flag outdated guest tools or missing virtio drivers
- Check for NUMA misalignment in large VMs
- Check for overcommitted resources"""


# ── Safe Commands ─────────────────────────────────────────────────────────
# Commands that NEXUS AI is allowed to execute (read-only by default)
SAFE_COMMANDS = {
    "virsh list",
    "virsh dominfo",
    "virsh domstats",
    "virsh dommemstat",
    "virsh vcpuinfo",
    "virsh domblkstat",
    "virsh domifstat",
    "virsh nodeinfo",
    "virsh nodememstats",
    "virsh pool-list",
    "virsh net-list",
    "virsh capabilities",
    "cat /proc/cpuinfo",
    "cat /proc/meminfo",
    "free -h",
    "df -h",
    "iostat",
    "vmstat",
    "top -bn1",
    "uptime",
    "uname -a",
    "lscpu",
    "lsblk",
    "ip addr",
    "ip link",
    "ss -tulnp",
    "systemctl status libvirtd",
    "systemctl status ollama",
    "dmesg | tail -50",
    "journalctl -u libvirtd --no-pager -n 50",
}

def is_safe_command(cmd: str) -> bool:
    """Check if a command is in the safe list."""
    cmd_base = cmd.strip().split("|")[0].strip()
    for safe in SAFE_COMMANDS:
        if cmd_base.startswith(safe):
            return True
    return False

async def execute_safe_command(cmd: str) -> str:
    """Execute a command if it's safe, return output."""
    if not is_safe_command(cmd):
        return f"[BLOCKED] Command not in safe list: {cmd}"
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=15
        )
        output = result.stdout[:2000]  # limit output size
        if result.stderr:
            output += f"\n[stderr]: {result.stderr[:500]}"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return "[TIMEOUT] Command took too long"
    except Exception as e:
        return f"[ERROR] {e}"


@dataclass
class ClusterContext:
    """Real-time cluster state injected into every AI prompt."""
    hosts:     list[dict]
    vms:       list[dict]
    storage:   list[dict]
    networks:  list[dict]
    events:    list[dict]
    timestamp: float = 0.0

    def to_prompt_string(self) -> str:
        """Format cluster state as structured context for the model."""
        lines = [
            "=== LIVE CLUSTER STATE ===",
            f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.timestamp))}",
            "",
            "HOSTS:",
        ]
        for h in self.hosts:
            cpu_warn = " [HIGH]" if h.get("cpu_pct", 0) > 80 else ""
            ram_warn = " [HIGH]" if h.get("ram_pct", 0) > 85 else ""
            lines.append(
                f"  [{h['status'].upper()}] {h['name']} ({h['ip']}) | "
                f"CPU: {h.get('cpu_pct', 0)}%{cpu_warn} | "
                f"RAM: {h.get('ram_pct', 0)}%{ram_warn} | "
                f"VMs: {h.get('vm_count', 0)}"
            )

        lines.append("\nVIRTUAL MACHINES:")
        for vm in self.vms:
            issues = []
            if vm.get("disk_pct", 0) > 85:
                issues.append(f"DISK {vm['disk_pct']}% FULL")
            if vm.get("cpu_pct", 0) > 90:
                issues.append(f"CPU {vm['cpu_pct']}% SPIKE")
            if vm.get("ram_pct", 0) > 90:
                issues.append(f"RAM {vm['ram_pct']}% HIGH")
            if vm.get("balloon_mb", 0) > 500:
                issues.append(f"BALLOON {vm['balloon_mb']}MB")
            if vm.get("days_since_backup", 0) > 7:
                issues.append(f"NO BACKUP {vm['days_since_backup']}d")
            issue_str = f" | ISSUES: {', '.join(issues)}" if issues else ""
            lines.append(
                f"  [{vm['state'].upper()}] {vm['name']} on {vm['host']} | "
                f"CPU: {vm.get('cpu_pct', 0)}% | RAM: {vm.get('ram_pct', 0)}% | "
                f"Disk: {vm.get('disk_pct', 0)}%{issue_str}"
            )

        lines.append("\nSTORAGE:")
        for s in self.storage:
            warn = " [CRITICAL]" if s.get("used_pct", 0) > 85 else (" [WARNING]" if s.get("used_pct", 0) > 70 else "")
            lines.append(
                f"  {s['name']} ({s['type']}) | "
                f"{s.get('used_pct', 0)}% full{warn} | "
                f"{s.get('free_gb', 0):.0f}GB free"
            )

        lines.append("\nNETWORKS:")
        for n in self.networks:
            issues = []
            if n.get("uplink_count", 1) < 2:
                issues.append("SINGLE UPLINK - NO REDUNDANCY")
            issue_str = f" | WARNING: {', '.join(issues)}" if issues else ""
            lines.append(f"  {n['name']} ({n['type']}) | VMs: {n.get('vm_count', 0)}{issue_str}")

        if self.events:
            lines.append("\nRECENT EVENTS (last 5):")
            for e in self.events[:5]:
                lines.append(f"  [{e.get('level','INFO').upper()}] {e.get('message', '')}")

        lines.append("===========================")
        return "\n".join(lines)


class NexusAI:
    """
    Interface to the locally running NEXUS AI model via Ollama.
    Provides: health check, chat, streaming chat, proactive scan,
    command execution with safety checks, and system metrics reading.
    """

    def __init__(self, base_url: str = OLLAMA_BASE, model: str = MODEL_NAME):
        self.base_url    = base_url
        self.model       = model
        self.history:    list[dict] = []
        self.context:    Optional[ClusterContext] = None
        self._client     = httpx.AsyncClient(timeout=TIMEOUT)
        self._scan_history: list[dict] = []
        self._last_scan_time: float = 0
        log.info(f"NexusAI initialized: model={model} base_url={base_url}")

    async def health_check(self) -> dict:
        """Check if Ollama is running and the model is available."""
        try:
            r = await self._client.get(f"{self.base_url}/api/tags", timeout=10)
            models = r.json().get("models", [])
            model_names = [m["name"] for m in models]
            model_details = {}
            for m in models:
                if self.model in m["name"]:
                    model_details = {
                        "size_bytes": m.get("size", 0),
                        "parameter_size": m.get("details", {}).get("parameter_size", "unknown"),
                        "quantization": m.get("details", {}).get("quantization_level", "unknown"),
                        "family": m.get("details", {}).get("family", "unknown"),
                    }
            available = any(self.model in name for name in model_names)
            return {
                "ollama_running": True,
                "model_available": available,
                "available_models": model_names,
                "model_name": self.model,
                "model_details": model_details,
                "history_length": len(self.history),
                "last_scan": self._last_scan_time,
            }
        except httpx.ConnectError:
            return {"ollama_running": False, "error": "Cannot connect to Ollama"}
        except Exception as e:
            return {"ollama_running": False, "error": str(e)}

    async def chat(self, user_message: str, cluster_context: Optional[ClusterContext] = None) -> str:
        """Send a message and get a complete response with cluster context."""
        ctx = cluster_context or self.context
        messages = self._build_messages(user_message, ctx)

        payload = {
            "model":    self.model,
            "messages": messages,
            "stream":   False,
            "options": {
                "temperature": 0.3,
                "num_predict": 4096,
                "top_p": 0.9,
                "repeat_penalty": 1.1,
            }
        }

        try:
            r = await self._client.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            response = r.json()
            content = response["message"]["content"]

            self.history.append({"role": "user", "content": user_message})
            self.history.append({"role": "assistant", "content": content})

            if len(self.history) > 40:
                self.history = self.history[-40:]

            return content
        except httpx.ConnectError:
            return "Cannot connect to Ollama. Ensure it's running: `systemctl start ollama`"
        except httpx.TimeoutException:
            return "Request timed out. The model may be loading or the system is under heavy load."
        except Exception as e:
            log.error(f"AI chat error: {e}")
            return f"AI error: {e}"

    async def stream(
        self,
        user_message: str,
        cluster_context: Optional[ClusterContext] = None,
    ) -> AsyncIterator[str]:
        """Streaming chat — yields tokens as they're generated."""
        ctx = cluster_context or self.context
        messages = self._build_messages(user_message, ctx)

        payload = {
            "model":    self.model,
            "messages": messages,
            "stream":   True,
            "options": {"temperature": 0.3, "num_predict": 4096, "repeat_penalty": 1.1},
        }

        full_response = []
        try:
            async with self._client.stream(
                "POST", f"{self.base_url}/api/chat", json=payload, timeout=TIMEOUT
            ) as resp:
                async for line in resp.aiter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            token = data.get("message", {}).get("content", "")
                            if token:
                                full_response.append(token)
                                yield token
                            if data.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue

            self.history.append({"role": "user", "content": user_message})
            self.history.append({"role": "assistant", "content": "".join(full_response)})
            if len(self.history) > 40:
                self.history = self.history[-40:]
        except Exception as e:
            yield f"\n\n[Error: {e}]"

    async def proactive_scan(self, cluster_context: ClusterContext) -> list[dict]:
        """Run a proactive health scan on the cluster. Returns structured issues."""
        self.context = cluster_context
        self._last_scan_time = time.time()

        # Gather additional system metrics for the scan
        system_info = await self._gather_system_metrics()

        prompt = f"""Perform a comprehensive health scan of the cluster.

{cluster_context.to_prompt_string()}

ADDITIONAL SYSTEM METRICS:
{system_info}

Analyze every metric and return a JSON array of detected issues. Each issue:
{{
  "severity": "CRITICAL" | "WARNING" | "INFO",
  "component": "VM name, host name, storage pool, or network name",
  "type": "disk_space" | "cpu_pressure" | "memory" | "network" | "backup" | "config" | "performance" | "security",
  "title": "Short title (max 60 chars)",
  "technical_detail": "PhD-level explanation of the root cause",
  "impact": "What breaks and when if unaddressed",
  "remediation": "Specific commands or steps to fix",
  "risk": "SAFE" | "REQUIRES_DOWNTIME" | "DATA_LOSS_RISK",
  "proposed_command": "Exact command if I should execute this (optional)"
}}

Rules:
- Only report REAL issues based on the actual metrics shown
- Include at least one INFO item even if the cluster is healthy
- For each WARNING/CRITICAL, provide a specific actionable remediation
- Check for: resource exhaustion, single points of failure, backup gaps, security misconfigs, performance bottlenecks
- Return ONLY the JSON array. No preamble, no code fences."""

        response = await self.chat(prompt)

        try:
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            # Try to find a JSON array in the response
            start = clean.find("[")
            end = clean.rfind("]") + 1
            if start >= 0 and end > start:
                clean = clean[start:end]
            issues = json.loads(clean)
            if isinstance(issues, list):
                self._scan_history = issues
                return issues
        except json.JSONDecodeError:
            pass

        # Fallback: return raw text as a single issue
        return [{
            "severity": "INFO",
            "component": "NEXUS AI",
            "type": "config",
            "title": "AI scan complete — see details",
            "technical_detail": response[:1000],
            "impact": "See analysis",
            "remediation": "Review AI recommendations",
            "risk": "SAFE",
        }]

    async def _gather_system_metrics(self) -> str:
        """Gather real system metrics for enhanced AI analysis."""
        metrics = []
        try:
            import psutil
            cpu = psutil.cpu_percent(percpu=True)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            load = os.getloadavg()
            swap = psutil.swap_memory()

            metrics.append(f"CPU per core: {cpu}")
            metrics.append(f"Load average: {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}")
            metrics.append(f"RAM: {mem.used // (1024**2)}MB / {mem.total // (1024**2)}MB ({mem.percent}%)")
            metrics.append(f"Swap: {swap.used // (1024**2)}MB / {swap.total // (1024**2)}MB ({swap.percent}%)")
            metrics.append(f"Disk /: {disk.used // (1024**3)}GB / {disk.total // (1024**3)}GB ({disk.percent}%)")

            # Disk I/O
            dio = psutil.disk_io_counters()
            if dio:
                metrics.append(f"Disk I/O: read={dio.read_bytes // (1024**2)}MB write={dio.write_bytes // (1024**2)}MB")

            # Network
            nio = psutil.net_io_counters()
            metrics.append(f"Network: rx={nio.bytes_recv // (1024**2)}MB tx={nio.bytes_sent // (1024**2)}MB errors={nio.errin + nio.errout} drops={nio.dropin + nio.dropout}")

            # Top processes
            procs = []
            for p in psutil.process_iter(["name", "cpu_percent", "memory_percent"]):
                try:
                    info = p.info
                    if info["cpu_percent"] and info["cpu_percent"] > 1:
                        procs.append(info)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            procs.sort(key=lambda x: x.get("cpu_percent", 0), reverse=True)
            if procs:
                metrics.append("Top CPU processes: " + ", ".join(
                    f"{p['name']}({p['cpu_percent']:.1f}%)" for p in procs[:5]
                ))

        except Exception as e:
            metrics.append(f"(could not gather system metrics: {e})")

        return "\n".join(metrics)

    async def execute_command(self, command: str) -> dict:
        """Execute a safe command and return results."""
        if is_safe_command(command):
            output = await execute_safe_command(command)
            return {"status": "executed", "command": command, "output": output}
        else:
            return {"status": "blocked", "command": command, "reason": "Command not in safe list"}

    def _build_messages(
        self,
        user_message: str,
        ctx: Optional[ClusterContext],
    ) -> list[dict]:
        """Build the message array with system prompt and cluster context."""
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        if ctx:
            context_prefix = ctx.to_prompt_string() + "\n\n"
        else:
            context_prefix = ""

        # Include conversation history for multi-turn
        messages.extend(self.history[-20:])  # last 10 exchanges

        messages.append({
            "role": "user",
            "content": context_prefix + user_message,
        })
        return messages

    def reset_conversation(self):
        """Start a fresh conversation."""
        self.history = []

    async def close(self):
        await self._client.aclose()


# ── Quick test ────────────────────────────────────────────────────────────
async def main():
    ai = NexusAI()

    health = await ai.health_check()
    print("Health:", json.dumps(health, indent=2))

    if not health.get("ollama_running"):
        print("Ollama not running. Start with: systemctl start ollama")
        return

    # Test system metrics gathering
    metrics = await ai._gather_system_metrics()
    print("\nSystem Metrics:")
    print(metrics)

    await ai.close()


if __name__ == "__main__":
    asyncio.run(main())
