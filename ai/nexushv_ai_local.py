"""
NexusHV AI — Local Model Integration
Connects the hypervisor management console to the locally running NEXUS AI model.

The model runs via Ollama on the same bare-metal host as the hypervisor.
Zero external API calls. Fully air-gapped.

Usage:
    # Start Ollama (runs as systemd service after install):
    systemctl start ollama
    ollama serve  # or managed by systemd

    # The NexusHV API imports this module:
    from nexushv_ai_local import NexusAI
    ai = NexusAI()
    response = await ai.chat("Why is VM prod-db-01 showing high I/O latency?")
"""

import asyncio
import json
import httpx
import time
from typing import AsyncIterator, Optional
from dataclasses import dataclass

# Ollama runs locally — zero external calls
OLLAMA_BASE  = "http://localhost:11434"
MODEL_NAME   = "nexushv-ai"   # the model you created: ollama create nexushv-ai -f Modelfile
TIMEOUT      = 120            # seconds — 8B model on RTX 4090 responds in ~3s


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
            "═══ LIVE CLUSTER STATE ═══",
            f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.timestamp))}",
            "",
            "HOSTS:",
        ]
        for h in self.hosts:
            cpu_warn = " ⚠ HIGH" if h.get("cpu_pct", 0) > 80 else ""
            ram_warn = " ⚠ HIGH" if h.get("ram_pct", 0) > 85 else ""
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
            if vm.get("balloon_mb", 0) > 500:
                issues.append(f"BALLOON {vm['balloon_mb']}MB")
            if vm.get("days_since_backup", 0) > 7:
                issues.append(f"NO BACKUP {vm['days_since_backup']}d")
            if vm.get("guest_tools_outdated", False):
                issues.append("TOOLS OUTDATED")
            issue_str = f" | ISSUES: {', '.join(issues)}" if issues else ""
            lines.append(
                f"  [{vm['state'].upper()}] {vm['name']} on {vm['host']} | "
                f"CPU: {vm.get('cpu_pct', 0)}% | RAM: {vm.get('ram_pct', 0)}% | "
                f"Disk: {vm.get('disk_pct', 0)}%{issue_str}"
            )

        lines.append("\nSTORAGE:")
        for s in self.storage:
            warn = " ⚠ CRITICAL" if s.get("used_pct", 0) > 85 else (" ⚠ WARNING" if s.get("used_pct", 0) > 70 else "")
            lines.append(
                f"  {s['name']} ({s['type']}) | "
                f"{s.get('used_pct', 0)}% full{warn} | "
                f"{s.get('free_gb', 0):.0f}GB free"
            )

        lines.append("\nNETWORKS:")
        for n in self.networks:
            issues = []
            if n.get("uplink_count", 1) < 2:
                issues.append("SINGLE UPLINK — NO REDUNDANCY")
            issue_str = f" | ⚠ {', '.join(issues)}" if issues else ""
            lines.append(f"  {n['name']} ({n['type']}) | VMs: {n.get('vm_count', 0)}{issue_str}")

        if self.events:
            lines.append("\nRECENT EVENTS (last 5):")
            for e in self.events[:5]:
                lines.append(f"  [{e.get('level','INFO').upper()}] {e.get('message', '')}")

        lines.append("═══════════════════════════")
        return "\n".join(lines)


class NexusAI:
    """
    Interface to the locally running NEXUS AI model via Ollama.
    Provides: health check, chat, streaming chat, proactive scan.
    """

    def __init__(self, base_url: str = OLLAMA_BASE, model: str = MODEL_NAME):
        self.base_url    = base_url
        self.model       = model
        self.history:    list[dict] = []   # conversation history
        self.context:    Optional[ClusterContext] = None
        self._client     = httpx.AsyncClient(timeout=TIMEOUT)

    async def health_check(self) -> dict:
        """Check if Ollama is running and the model is available."""
        try:
            r = await self._client.get(f"{self.base_url}/api/tags")
            models = r.json().get("models", [])
            model_names = [m["name"] for m in models]
            available = any(self.model in name for name in model_names)
            return {
                "ollama_running": True,
                "model_available": available,
                "available_models": model_names,
                "model_name": self.model,
            }
        except Exception as e:
            return {"ollama_running": False, "error": str(e)}

    async def chat(self, user_message: str, cluster_context: Optional[ClusterContext] = None) -> str:
        """
        Send a message and get a complete response.
        Cluster context is injected into the system message automatically.
        """
        ctx = cluster_context or self.context
        messages = self._build_messages(user_message, ctx)

        payload = {
            "model":    self.model,
            "messages": messages,
            "stream":   False,
            "options": {
                "temperature": 0.2,
                "num_predict": 2048,
                "top_p": 0.9,
            }
        }

        r = await self._client.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        response = r.json()
        content = response["message"]["content"]

        # Update history for multi-turn conversation
        self.history.append({"role": "user",      "content": user_message})
        self.history.append({"role": "assistant", "content": content})

        # Keep history bounded (last 20 exchanges = 40 messages)
        if len(self.history) > 40:
            self.history = self.history[-40:]

        return content

    async def stream(
        self,
        user_message: str,
        cluster_context: Optional[ClusterContext] = None,
    ) -> AsyncIterator[str]:
        """
        Streaming chat — yields tokens as they're generated.
        Use this for the UI chat interface for real-time response display.
        """
        ctx = cluster_context or self.context
        messages = self._build_messages(user_message, ctx)

        payload = {
            "model":    self.model,
            "messages": messages,
            "stream":   True,
            "options": {"temperature": 0.2, "num_predict": 2048},
        }

        full_response = []
        async with self._client.stream(
            "POST", f"{self.base_url}/api/chat", json=payload, timeout=TIMEOUT
        ) as resp:
            async for line in resp.aiter_lines():
                if line:
                    data = json.loads(line)
                    token = data.get("message", {}).get("content", "")
                    if token:
                        full_response.append(token)
                        yield token
                    if data.get("done"):
                        break

        # Update history
        self.history.append({"role": "user",      "content": user_message})
        self.history.append({"role": "assistant", "content": "".join(full_response)})
        if len(self.history) > 40:
            self.history = self.history[-40:]

    async def proactive_scan(self, cluster_context: ClusterContext) -> list[dict]:
        """
        Run a proactive health scan on the cluster.
        Returns structured list of detected issues.
        The model is prompted to return JSON.
        """
        self.context = cluster_context
        prompt = f"""Perform a comprehensive health scan of the cluster.

{cluster_context.to_prompt_string()}

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

Return ONLY the JSON array. No preamble."""

        response = await self.chat(prompt)

        # Parse JSON from response (model trained to return clean JSON)
        try:
            # Strip any markdown code fences if present
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            return json.loads(clean)
        except json.JSONDecodeError:
            # Fallback: return raw text as a single issue
            return [{
                "severity": "INFO",
                "component": "NEXUS AI",
                "type": "config",
                "title": "AI scan complete — see details",
                "technical_detail": response,
                "impact": "See analysis",
                "remediation": "Review AI recommendations",
                "risk": "SAFE",
            }]

    def _build_messages(
        self,
        user_message: str,
        ctx: Optional[ClusterContext],
    ) -> list[dict]:
        """Build the message array with optional cluster context."""
        messages = []

        # Inject cluster context into the first user message of each turn
        # (Ollama handles the system prompt via the Modelfile)
        if ctx:
            context_prefix = ctx.to_prompt_string() + "\n\n"
        else:
            context_prefix = ""

        # Include conversation history for multi-turn
        messages.extend(self.history)

        messages.append({
            "role": "user",
            "content": context_prefix + user_message,
        })
        return messages

    def reset_conversation(self):
        """Start a fresh conversation (call between sessions)."""
        self.history = []

    async def close(self):
        await self._client.aclose()


# ── FastAPI integration (add to nexushv_api.py) ───────────────────────────────
"""
Add these routes to your main FastAPI app:

from nexushv_ai_local import NexusAI, ClusterContext
ai = NexusAI()

@app.get("/ai/health")
async def ai_health():
    return await ai.health_check()

@app.post("/ai/scan")
async def ai_scan():
    ctx = await build_cluster_context()  # collect real metrics
    issues = await ai.proactive_scan(ctx)
    return {"issues": issues, "scanned_at": time.time()}

@app.post("/ai/chat")
async def ai_chat(message: str):
    ctx = await build_cluster_context()
    response = await ai.chat(message, ctx)
    return {"response": response}

@app.websocket("/ai/stream")
async def ai_stream(ws: WebSocket, message: str):
    await ws.accept()
    ctx = await build_cluster_context()
    async for token in ai.stream(message, ctx):
        await ws.send_text(token)
    await ws.close()
"""


# ── Quick test ────────────────────────────────────────────────────────────────
async def main():
    ai = NexusAI()

    health = await ai.health_check()
    print("Health:", health)

    if not health.get("model_available"):
        print("Model not available. Run: ollama create nexushv-ai -f Modelfile")
        return

    # Simulate cluster context
    ctx = ClusterContext(
        timestamp=time.time(),
        hosts=[
            {"name":"esxi-prod-01","ip":"10.0.1.10","status":"connected","cpu_pct":87,"ram_pct":78,"vm_count":3},
            {"name":"esxi-prod-02","ip":"10.0.1.11","status":"connected","cpu_pct":38,"ram_pct":54,"vm_count":4},
        ],
        vms=[
            {"name":"prod-db-01","host":"esxi-prod-01","state":"running","cpu_pct":72,"ram_pct":88,"disk_pct":91,"days_since_backup":0},
            {"name":"prod-web-01","host":"esxi-prod-01","state":"running","cpu_pct":28,"ram_pct":55,"disk_pct":42,"days_since_backup":1},
            {"name":"dev-sandbox","host":"esxi-prod-02","state":"running","cpu_pct":5,"ram_pct":20,"disk_pct":30,"days_since_backup":14},
            {"name":"win-rdp-01","host":"esxi-prod-01","state":"running","cpu_pct":12,"ram_pct":62,"disk_pct":58,"guest_tools_outdated":True,"days_since_backup":3},
        ],
        storage=[
            {"name":"nvme-pool-01","type":"QCOW2/NVMe","used_pct":91,"free_gb":92},
            {"name":"san-pool-01","type":"VMFS/SAN","used_pct":45,"free_gb":22528},
        ],
        networks=[
            {"name":"VM-Network","type":"Linux Bridge","vm_count":6,"uplink_count":1},
            {"name":"vMotion-Net","type":"Linux Bridge","vm_count":0,"uplink_count":2},
        ],
        events=[
            {"level":"warning","message":"prod-db-01 disk write rate 450MB/h sustained for 2h"},
            {"level":"info","message":"esxi-prod-01 CPU above 80% for 15 minutes"},
        ]
    )

    print("\nRunning proactive scan...")
    issues = await ai.proactive_scan(ctx)
    for issue in issues:
        print(f"\n[{issue['severity']}] {issue['title']}")
        print(f"  Component: {issue['component']}")
        print(f"  {issue['technical_detail'][:200]}...")

    print("\n\nChat test:")
    response = await ai.chat("Explain why prod-db-01's disk is filling up so fast and what I should do", ctx)
    print(response)

    await ai.close()


if __name__ == "__main__":
    asyncio.run(main())
