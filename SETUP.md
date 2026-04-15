# NexusHV — Build & Deployment Record

> Built on 2026-04-15 on ubuntu-8gb-hel1-1 (Ubuntu 24.04, 4 CPU, 7.6GB RAM, no GPU)

---

## Storage Volume

A 98GB volume was mounted for Ollama models and data:
```
/dev/sdb → /mnt/HC_Volume_105418457 (ext4, 98GB)
├── ollama/          ← OLLAMA_MODELS path (set in systemd + /etc/environment)
├── nexushv/         ← available for future data
└── swapfile         ← 8GB swap file (needed for larger AI models)
```
- Ollama systemd service has `Environment="OLLAMA_MODELS=/mnt/HC_Volume_105418457/ollama"`
- 8GB swap added at `/mnt/HC_Volume_105418457/swapfile` (in fstab)
- 7B model needs ~16GB RAM to run well — currently using 1.5B model
- **When server is upgraded to 16GB+ RAM**: change `Modelfile.cpu` to `FROM qwen2.5:7b` and run `ollama create nexushv-ai -f ai/model/Modelfile.cpu`

---

## What Was Done

### 1. System Dependencies
```bash
sudo apt-get install -y libvirt-dev pkg-config python3-dev python3-venv build-essential
```
- libvirt-dev installed (needed for `libvirt-python` pip package to compile)
- No KVM/libvirtd running on this machine — API runs in **demo mode** with mock data

### 2. Python Backend (venv)
```bash
cd /home/katadmin/nexushv
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn libvirt-python psutil websockets httpx aiofiles
```
- Venv at `/home/katadmin/nexushv/venv/`
- All deps installed: fastapi, uvicorn, libvirt-python, psutil, websockets, httpx, aiofiles

### 3. API Rewrite — Demo Mode (`api/nexushv_api.py`)
**What changed**: The original API required a live libvirtd connection. Rewrote to:
- Auto-detect libvirt availability at startup
- If libvirtd is unavailable → serve realistic mock VM/host/storage/network data
- If libvirtd is available → use real libvirt calls (original behavior preserved)
- Added AI integration routes: `/api/ai/health`, `/api/ai/chat`, `/api/ai/scan`, `/ws/ai/stream`
- Added static file serving for the built frontend (`ui/dist/`)
- Added CORS middleware
- All original endpoints preserved: `/api/vms`, `/api/vms/{name}/action`, `/api/vms/{name}/migrate`, etc.

### 4. HA Daemon Rewrite (`ha/nexushv_ha.py`)
**What changed**: Original required `--host` and `--peers` args with real network peers.
- Added `--standalone` flag for single-host demo mode
- In standalone mode: creates 3 simulated peers with realistic state
- Populates demo VM policies (7 VMs with priorities)
- All real HA logic preserved for production cluster mode
- Added CORS middleware to HA API
- Added `/health` endpoint

### 5. React Frontend (`ui/`)
**What changed**: Original was bare JSX files with no build system.
- Initialized Vite React project in `ui/`
- Installed deps: `react`, `react-dom`, `recharts`, `react-router-dom`
- Created `src/App.jsx` — top-level tab bar with 3 views:
  - **Management Console** (from `nexushv-console.jsx`)
  - **HA Failover** (from `nexushv_ha_dashboard.jsx`)
  - **NEXUS AI** (new `src/AIChat.jsx`)
- Created `src/AIChat.jsx` — AI chat panel with:
  - Chat interface talking to `/api/ai/chat`
  - Health scan button hitting `/api/ai/scan`
  - Suggestion chips for quick questions
  - Cluster context sidebar (live data from API)
- Configured Vite proxy: `/api/*` and `/ws/*` → `localhost:8080`
- Built production bundle at `ui/dist/`
- Updated `index.html` with IBM Plex fonts and custom scrollbar styling

### 6. Ollama + NEXUS AI Model
```bash
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull qwen2.5:1.5b
ollama create nexushv-ai -f ai/model/Modelfile.cpu
```
- Installed Ollama (CPU-only, no GPU on this machine)
- Pulled `qwen2.5:1.5b` (986MB) — small enough for 7.6GB RAM CPU inference
- Created `ai/model/Modelfile.cpu` — custom Modelfile using qwen2.5:1.5b as base with full NEXUS AI system prompt
- Registered as `nexushv-ai:latest` in Ollama

---

## Running Services

| Service | Port | Command | Log |
|---------|------|---------|-----|
| **NexusHV API** | 8080 | `source venv/bin/activate && python3 api/nexushv_api.py` | `/tmp/nexushv-api.log` |
| **HA Daemon** | 8081 | `source venv/bin/activate && python3 ha/nexushv_ha.py --standalone --port 8081` | `/tmp/nexushv-ha.log` |
| **Frontend** | 3000 | `cd ui && npx vite --host 0.0.0.0 --port 3000` | `/tmp/nexushv-ui.log` |
| **Ollama** | 11434 | `systemctl start ollama` (systemd) | `journalctl -u ollama` |

### How to Restart Everything
```bash
cd /home/katadmin/nexushv

# Kill existing
sudo fuser -k 8080/tcp 8081/tcp 3000/tcp 2>/dev/null

# Start API
source venv/bin/activate
nohup python3 api/nexushv_api.py > /tmp/nexushv-api.log 2>&1 &

# Start HA
nohup python3 ha/nexushv_ha.py --standalone --port 8081 > /tmp/nexushv-ha.log 2>&1 &

# Start frontend dev server
cd ui && nohup npx vite --host 0.0.0.0 --port 3000 > /tmp/nexushv-ui.log 2>&1 &

# Ollama (should already be running via systemd)
sudo systemctl start ollama
```

### How to Serve Frontend via API (No Vite Dev Server)
The API can serve the built frontend directly:
```bash
cd /home/katadmin/nexushv/ui && npx vite build
# Now just start the API — it serves ui/dist/ on all non-API routes
source venv/bin/activate && python3 api/nexushv_api.py
# Open http://localhost:8080 — frontend served directly
```

---

## Key API Endpoints

```
GET  /health                     → service health
GET  /api/mode                   → demo_mode / ai_available flags
GET  /api/vms                    → list all VMs
GET  /api/vms/{name}             → single VM details
POST /api/vms                    → create VM (body: VMCreate)
POST /api/vms/{name}/action      → start/stop/reboot/suspend (body: {"action":"start"})
DEL  /api/vms/{name}             → delete VM
POST /api/vms/{name}/migrate     → vMotion live migration
GET  /api/vms/{name}/snapshots   → list snapshots
POST /api/vms/{name}/snapshots   → create snapshot
GET  /api/hosts/local            → host info + metrics
GET  /api/storage                → storage pools
GET  /api/networks               → virtual networks
WS   /ws/metrics                 → real-time metrics stream (2s interval)

GET  /api/ai/health              → Ollama + model status
POST /api/ai/chat                → chat with NEXUS AI (body: {"message":"..."})
POST /api/ai/scan                → proactive health scan
WS   /ws/ai/stream               → streaming AI chat

# HA Daemon (port 8081)
GET  /ha/status                  → full HA state (peers, policies, events)
POST /ha/vms/{name}/policy       → set VM restart policy
POST /ha/simulate/fail/{ip}      → trigger simulated failover
GET  /ha/events                  → failover event log
```

---

## Files Modified/Created

```
MODIFIED:
  api/nexushv_api.py          — rewrote with demo mode + AI routes + static serving
  ha/nexushv_ha.py            — rewrote with standalone mode

CREATED:
  venv/                       — Python virtual environment
  ai/model/Modelfile.cpu      — Ollama model config for CPU inference
  ui/package.json             — added recharts, react-router-dom deps
  ui/vite.config.js           — Vite config with API proxy
  ui/index.html               — custom HTML with fonts + styling
  ui/src/main.jsx             — React entry point
  ui/src/App.jsx              — Main app with tab navigation
  ui/src/AIChat.jsx           — AI chat panel component
  ui/src/index.css            — minimal reset
  ui/node_modules/            — npm dependencies
  ui/dist/                    — production build output
  SETUP.md                    — this file
```

## Files NOT Modified (original intact)
```
  ui/nexushv-console.jsx      — management console (used as-is)
  ui/nexushv_ha_dashboard.jsx — HA dashboard (used as-is)
  ai/nexushv_ai_local.py      — AI integration module (used as-is)
  ai/training/                — training pipeline (not run, needs GPU)
  ai/model/Modelfile          — original Modelfile (needs fine-tuned GGUF)
  scripts/install.sh          — bare-metal installer (not run)
  README.md                   — original readme
```

---

## Production Deployment Notes

- On **real bare metal with KVM**: the API auto-detects libvirtd and switches to live mode
- For **GPU hosts**: use `ai/model/Modelfile` with a fine-tuned GGUF model instead of `Modelfile.cpu`
- For **multi-host HA**: run `python3 ha/nexushv_ha.py --host <IP> --peers <IP1>,<IP2>`
- The `scripts/install.sh` installer handles full bare-metal setup (KVM, libvirt, bridge, systemd services)
