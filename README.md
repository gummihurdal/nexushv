# NexusHV v2.0

**Production-grade bare-metal hypervisor management platform — a VMware vSphere alternative built on KVM/QEMU.**

> vMotion. HA Failover. PhD-level AI Administrator. JWT Auth. Prometheus Metrics. All on-premise. Zero licensing costs.

---

## What is NexusHV?

NexusHV is a production-grade hypervisor management platform that rivals VMware vSphere in functionality while running entirely on open-source infrastructure (KVM + QEMU + Linux). It targets enterprises displaced by Broadcom's VMware price increases.

### Feature Comparison

| Feature | NexusHV v2.0 | VMware vSphere | Proxmox VE |
|---|---|---|---|
| Live Migration (vMotion) | KVM native | vMotion | QEMU migrate |
| HA Failover + STONITH | Full IPMI fencing | vSphere HA | Corosync/HA |
| AI Administrator | Local LLM (air-gapped) | --- | --- |
| Proactive Monitoring | AI-powered real-time | Paid add-on | --- |
| JWT Authentication | HS256 + RBAC | SSO/LDAP | PAM/LDAP |
| Prometheus Metrics | Native `/metrics` | Paid | Plugin |
| Right-Sizing AI | Automatic recommendations | --- | --- |
| Webhook Alerts | Built-in | --- | --- |
| Audit Trail | SQLite persistent | vRealize | --- |
| REST API + Docs | OpenAPI/Swagger | SOAP/REST | REST |
| Air-gapped deployment | Full | No | Partial |
| Licensing cost | **Free (Apache 2.0)** | $$$$$ | Free (AGPL) |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     NexusHV v2.0 Platform                    │
│                                                              │
│  ┌────────────┐  ┌──────────────┐  ┌────────────────────┐   │
│  │  React UI  │  │  FastAPI     │  │    NEXUS AI        │   │
│  │ Dashboard  │◄─┤  REST API    │◄─┤  Local LLM (Ollama)│   │
│  │ vSphere UI │  │  WebSocket   │  │  Proactive Scan    │   │
│  │ HA Panel   │  │  Prometheus  │  │  Right-Sizing      │   │
│  │ AI Chat    │  │  JWT Auth    │  │  Safe Commands     │   │
│  │ Alerts     │  │  Rate Limit  │  └────────────────────┘   │
│  └────────────┘  └──────┬───────┘                            │
│                         │                                    │
│  ┌────────────┐  ┌──────▼───────┐  ┌────────────────────┐   │
│  │ HA Engine  │  │   libvirt    │  │  SQLite Database   │   │
│  │ Quorum     │  │   KVM/QEMU  │  │  Users, Audit Log  │   │
│  │ Split-Brain│  │   virsh      │  │  Alerts, Metrics   │   │
│  │ STONITH    │  └──────────────┘  └────────────────────┘   │
│  └────────────┘                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### Option 1: Production Install (recommended)

```bash
git clone https://github.com/gummihurdal/nexushv
cd nexushv
sudo bash scripts/install.sh
```

This installs to `/opt/nexushv` with systemd services.

### Option 2: Development Setup

```bash
git clone https://github.com/gummihurdal/nexushv
cd nexushv

# Python backend
python3 -m venv venv && source venv/bin/activate
pip install fastapi uvicorn libvirt-python psutil httpx aiofiles \
            pyjwt bcrypt prometheus-client aiosqlite python-multipart

# Start API (auto-detects KVM or runs in demo mode)
python3 api/nexushv_api.py
# API: http://localhost:8080
# Docs: http://localhost:8080/api/docs
# Metrics: http://localhost:8080/metrics

# Start HA daemon
python3 ha/nexushv_ha.py --standalone --port 8081

# Frontend (development)
cd ui && npm install && npm run dev
```

### Default Credentials

```
Username: admin
Password: admin
```

**Change immediately** after first login.

---

## API Endpoints

### Authentication
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/login` | Get JWT token |
| GET | `/api/auth/me` | Current user info |
| POST | `/api/auth/users` | Create user (admin) |
| GET | `/api/auth/users` | List users (admin) |

### Virtual Machines
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/vms` | List all VMs |
| GET | `/api/vms/{name}` | Get VM details |
| POST | `/api/vms` | Create VM |
| POST | `/api/vms/{name}/action` | Start/stop/reboot/suspend |
| DELETE | `/api/vms/{name}` | Delete VM |
| POST | `/api/vms/{name}/migrate` | Live migrate (vMotion) |
| GET | `/api/vms/{name}/snapshots` | List snapshots |
| POST | `/api/vms/{name}/snapshots` | Create snapshot |
| GET | `/api/vms/{name}/console` | VNC console details |

### Monitoring & Metrics
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/hosts/local` | Host info (CPU, RAM, disk, network) |
| GET | `/api/metrics/system` | Detailed system metrics |
| GET | `/api/metrics/history` | Historical metrics for charting |
| GET | `/api/dashboard/overview` | Aggregated dashboard data |
| GET | `/metrics` | Prometheus-compatible metrics |
| WS | `/ws/metrics` | Real-time WebSocket metrics |

### AI
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/ai/health` | AI/Ollama status |
| POST | `/api/ai/chat` | Chat with NEXUS AI |
| POST | `/api/ai/scan` | Proactive health scan |
| WS | `/ws/ai/stream` | Streaming AI chat |

### Alerts & Operations
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/alerts` | Get alerts |
| POST | `/api/alerts/{id}/acknowledge` | Acknowledge alert |
| GET | `/api/audit` | Audit log (admin) |
| GET | `/api/recommendations/rightsizing` | AI right-sizing |
| GET/POST | `/api/webhooks` | Webhook management |

Full interactive docs at **http://localhost:8080/api/docs**

---

## NEXUS AI

NEXUS AI is a locally-running LLM with PhD-level virtualization expertise. Runs via Ollama — no cloud calls, no API keys, fully air-gapped.

### Capabilities
- **Proactive Health Scanning**: Detects issues before they become failures
- **Right-Sizing**: Recommends CPU/RAM adjustments based on utilization
- **Expert Troubleshooting**: KVM internals, QEMU, storage, networking, HA
- **Safe Command Execution**: Whitelisted read-only commands (virsh, iostat, etc.)
- **Real-Time Context**: Every response includes live cluster metrics

### Training Data
The AI is trained on 70+ PhD-level Q&A pairs covering:
- KVM/QEMU internals (VM-exits, EPT, VMCS)
- Storage (QCOW2, cache modes, io_uring, NVMe)
- Networking (SR-IOV, DPDK, vhost-net/user)
- Memory (ballooning, KSM, NUMA, huge pages)
- HA (STONITH, split-brain, quorum)
- Security (sVirt, seccomp, IOMMU, side-channels)
- Performance tuning (CPU pinning, iothreads)

---

## Observability

### Prometheus
```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'nexushv'
    static_configs:
      - targets: ['localhost:8080']
    metrics_path: '/metrics'
```

### Available Metrics
- `nexushv_api_requests_total` — Request counter by method/endpoint/status
- `nexushv_api_request_duration_seconds` — Request latency histogram
- `nexushv_active_websocket_connections` — Active WebSocket connections
- `nexushv_vm_count` — VM count by state
- `nexushv_host_cpu_percent` — Host CPU usage
- `nexushv_host_ram_percent` — Host RAM usage
- `nexushv_ai_requests_total` — AI chat requests
- `nexushv_ai_request_duration_seconds` — AI request latency

### Grafana
Import `observability/grafana-dashboard.json` for a pre-built dashboard.

---

## Security

- **JWT Authentication** with bcrypt password hashing
- **RBAC**: admin, operator, readonly roles
- **Audit Trail**: Every state-changing action logged to SQLite
- **Rate Limiting**: 100 requests/min per IP
- **Input Validation**: Pydantic models with strict patterns
- **Structured Logging**: JSON logs with rotation (10MB, 5 backups)

---

## Testing

```bash
# Run all tests (38+ tests)
source venv/bin/activate
python -m pytest tests/ -v

# Quick check
python -m pytest tests/ -q
```

---

## Directory Structure

```
nexushv/
├── api/
│   └── nexushv_api.py           # FastAPI backend (JWT, RBAC, metrics)
├── ha/
│   └── nexushv_ha.py            # HA engine (quorum, split-brain, STONITH)
├── ai/
│   ├── nexushv_ai_local.py      # AI module (Ollama, safe commands)
│   └── training/
│       ├── nexushv_train.py     # QLoRA fine-tuning pipeline
│       └── nexushv_dataset.jsonl # Training data (70+ Q&A pairs)
├── ui/
│   ├── src/
│   │   ├── App.jsx              # Main app (5 tabs)
│   │   ├── Dashboard.jsx        # Real-time dashboard with WebSocket
│   │   ├── AIChat.jsx           # AI chat interface
│   │   └── Alerts.jsx           # Alert management panel
│   ├── nexushv-console.jsx      # vSphere-familiar management console
│   └── nexushv_ha_dashboard.jsx # HA monitoring dashboard
├── tests/
│   └── test_api.py              # 42+ API tests
├── scripts/
│   ├── install.sh               # Production installer
│   ├── nexushv-supervisor.sh    # Process manager
│   ├── nexushv-api.service      # Systemd service
│   └── nexushv-ha.service       # Systemd service
├── observability/
│   ├── prometheus.yml           # Prometheus config
│   └── grafana-dashboard.json   # Grafana dashboard
├── docs/
│   ├── runbooks/                # Operational runbooks
│   └── adr/                     # Architecture Decision Records
├── data/                        # SQLite database (auto-created)
└── logs/                        # Log files (auto-created)
```

---

## Roadmap

- [x] JWT Authentication + RBAC
- [x] Prometheus metrics
- [x] SQLite persistence (audit, alerts, metrics)
- [x] Real-time Dashboard with WebSocket
- [x] AI right-sizing recommendations
- [x] Webhook notifications
- [x] Quorum-based HA with split-brain detection
- [x] 42+ automated tests
- [ ] DRS (Distributed Resource Scheduling)
- [ ] Ceph/RBD storage backend
- [ ] noVNC console integration
- [ ] OAuth2/OIDC authentication
- [ ] Multi-datacenter federation
- [ ] Kubernetes integration (KubeVirt)

---

## License

Apache 2.0 — free for commercial use.

---

Built by [gummihurdal](https://github.com/gummihurdal)
