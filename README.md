# NexusHV

**Open-source bare-metal hypervisor management platform — a VMware vSphere alternative built on KVM/QEMU.**

> vMotion. HA Failover. PhD-level AI Administrator. All on-premise. Zero licensing costs.

---

## What is NexusHV?

NexusHV is a production-grade hypervisor management platform that rivals VMware vSphere in functionality while running entirely on open-source infrastructure (KVM + QEMU + Linux). It targets enterprises displaced by Broadcom's VMware price increases.

### Core Features

| Feature | NexusHV | VMware ESXi |
|---|---|---|
| Live Migration (vMotion) | ✅ KVM virDomainMigrate | ✅ |
| HA Failover + STONITH | ✅ Full IPMI fencing | ✅ |
| AI Administrator | ✅ Local LLM (no cloud) | ❌ |
| Proactive Monitoring | ✅ Real-time anomaly detection | ⚠️ Paid add-on |
| Licensing cost | **Free** | $$$$ |
| Air-gapped deployment | ✅ | ❌ |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    NexusHV Platform                     │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   React UI   │  │  FastAPI     │  │  NEXUS AI    │  │
│  │  (vSphere-   │◄─┤  REST API    │◄─┤  Local LLM   │  │
│  │   familiar)  │  │  WebSocket   │  │  (Ollama)    │  │
│  └──────────────┘  └──────┬───────┘  └──────────────┘  │
│                           │                             │
│  ┌──────────────┐  ┌──────▼───────┐  ┌──────────────┐  │
│  │  HA Daemon   │  │   libvirt    │  │  KVM / QEMU  │  │
│  │  (STONITH +  │  │   API layer  │  │  Bare Metal  │  │
│  │   Raft HA)   │  └──────────────┘  └──────────────┘  │
│  └──────────────┘                                       │
└─────────────────────────────────────────────────────────┘
```

---

## Repository Structure

```
nexushv/
├── api/
│   └── nexushv_api.py          # FastAPI REST + WebSocket backend
│                               # VM CRUD, vMotion, storage, networks
├── ha/
│   └── nexushv_ha.py           # HA failover daemon
│                               # Heartbeats, STONITH fencing, Raft election
├── ai/
│   ├── nexushv_ai_local.py     # Local LLM integration (Ollama)
│   │                           # Proactive scan, streaming chat, cluster context
│   ├── training/
│   │   ├── nexushv_train.py    # QLoRA fine-tuning pipeline
│   │   └── nexushv_dataset.jsonl  # PhD-level virtualization training data
│   └── model/
│       └── Modelfile           # Ollama deployment config + system prompt
├── ui/
│   ├── nexushv-console.jsx     # Main vSphere-familiar management console
│   └── nexushv_ha_dashboard.jsx  # HA monitoring dashboard
├── scripts/
│   └── install.sh              # Bare-metal installer
└── docs/
    └── architecture.md
```

---

## Quick Start

### 1. Prerequisites (Ubuntu 22.04 / Debian 12)

```bash
# Install KVM + libvirt
apt install -y qemu-kvm libvirt-daemon-system libvirt-clients \
               bridge-utils python3-pip

# Verify KVM
kvm-ok

# Enable libvirt
systemctl enable --now libvirtd
```

### 2. Install NexusHV API

```bash
git clone https://github.com/gummihurdal/nexushv
cd nexushv

pip install fastapi uvicorn libvirt-python psutil websockets httpx

# Start the API
python3 api/nexushv_api.py
# API running at http://0.0.0.0:8080
```

### 3. Start HA Daemon (on each host)

```bash
# Run on every cluster host:
pip install pyghmi aiofiles

python3 ha/nexushv_ha.py \
  --host 10.0.1.10 \
  --peers 10.0.1.11,10.0.1.12 \
  --port 8081
```

### 4. Deploy NEXUS AI (Local LLM)

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# After fine-tuning (see ai/training/):
ollama create nexushv-ai -f ai/model/Modelfile

# Or use base Llama3 while training your fine-tune:
ollama pull llama3.1:8b
```

### 5. Fine-tune the AI

```bash
# Install training dependencies
pip install transformers peft trl bitsandbytes accelerate datasets

# Run QLoRA fine-tuning (~4h on RTX 4090)
python3 ai/training/nexushv_train.py

# Merge + export
python3 ai/training/nexushv_train.py merge

# Convert to GGUF (requires llama.cpp)
git clone https://github.com/ggerganov/llama.cpp && cd llama.cpp && make -j
python convert_hf_to_gguf.py ../nexushv-ai-merged --outtype f16
./llama-quantize nexushv-ai-f16.gguf nexushv-ai-Q4_K_M.gguf Q4_K_M

# Deploy
ollama create nexushv-ai -f ../nexushv/ai/model/Modelfile
```

---

## NEXUS AI — The PhD Administrator

NEXUS AI is a locally-running LLM fine-tuned on virtualization expertise. It runs on the bare-metal host via Ollama — no cloud calls, no API keys, fully air-gapped.

**Expertise covers:**
- KVM/QEMU internals (VMCS, EPT violations, VM-exit paths)
- CPU virtualization (Intel VT-x, AMD-V, VPID, TSC)
- Memory virtualization (shadow page tables, EPT/NPT, KSM, ballooning)
- Storage (QCOW2 L1/L2 structure, cache modes, io_uring, thin provisioning)
- Networking (OVS, DPDK, SR-IOV, VirtIO-net, vhost-user)
- Live migration (pre-copy, post-copy, auto-converge, RDMA)
- HA (STONITH, split-brain, admission control, Raft)
- Performance tuning (NUMA, CPU pinning, iothreads, huge pages)
- Security (sVirt, seccomp, IOMMU, side-channels)

**Proactive monitoring detects:**
- Disk pools approaching capacity (warns at 70%, alerts at 85%)
- VM memory balloon inflation indicating host pressure
- CPU steal time spikes indicating vCPU overcommit
- Missing backups (>7 days)
- Single-uplink network configurations (no redundancy)
- Guest tools outdated
- Misconfigured NUMA topology
- Storage fragmentation in aging QCOW2 images

---

## vMotion — Live Migration

```bash
# Via API:
curl -X POST http://localhost:8080/api/vms/prod-db-01/migrate \
  -H "Content-Type: application/json" \
  -d '{"dest_host": "qemu+ssh://10.0.1.11/system", "live": true}'

# Direct virsh:
virsh migrate --live --auto-converge --compressed \
  prod-db-01 qemu+ssh://10.0.1.11/system
```

Zero-downtime. VM keeps running during memory transfer. Final pause < 200ms.

---

## HA Failover

When a host fails:

1. **Detect** — UDP multicast heartbeat missed for 6s
2. **Verify** — check datastore heartbeat (NFS shared storage) to distinguish network split from host crash
3. **Fence** — STONITH via IPMI powers off the failed host (prevents split-brain)
4. **Restart** — VMs restarted on surviving hosts in priority order (High → Medium → Low)

Total failover time: typically 30-90 seconds.

---

## Hardware Requirements

### Production Cluster (minimum)
- **Hosts**: 3× servers with Intel VT-x/AMD-V + VT-d/AMD-Vi (IOMMU)
- **RAM**: 64GB+ per host
- **Storage**: Shared NFS/SAN or local NVMe per host
- **Network**: 10GbE (dedicated vMotion NIC recommended)
- **IPMI**: Required for STONITH fencing

### AI Training (one-time)
- **GPU**: NVIDIA RTX 3090 (24GB) minimum, RTX 4090 recommended
- **RAM**: 64GB system RAM
- **Time**: ~4h on RTX 4090, ~12h on RTX 3090

### AI Inference (per host)
- **GPU**: NVIDIA RTX 3080 10GB or better
- **CPU fallback**: 64GB RAM (slow but functional)

---

## Roadmap

- [ ] DRS (Distributed Resource Scheduling) — auto load balancing
- [ ] vSAN equivalent — distributed block storage via Ceph
- [ ] NSX equivalent — software-defined networking
- [ ] Web installer ISO (like Proxmox VE installer)
- [ ] NEXUS AI fine-tune dataset expansion (2,000+ Q&A pairs)
- [ ] Multi-datacenter federation
- [ ] Kubernetes integration (KubeVirt backend)

---

## License

Apache 2.0 — free for commercial use.

---

## Author

Built by [gummihurdal](https://github.com/gummihurdal)
