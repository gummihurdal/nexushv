# NexusHV Operational Runbooks

## Service Management

### Start all services
```bash
./scripts/nexushv-supervisor.sh start
```

### Stop all services
```bash
./scripts/nexushv-supervisor.sh stop
```

### Watchdog mode (auto-restart on crash)
```bash
./scripts/nexushv-supervisor.sh watch
```

---

## Runbook: API Not Responding

**Symptoms**: HTTP requests to port 8080 return connection refused or timeout.

**Diagnosis**:
```bash
# Check if process is running
ps aux | grep nexushv_api

# Check logs
tail -100 logs/nexushv-api.log

# Check port binding
ss -tulnp | grep 8080

# Check system resources
free -h
df -h
```

**Resolution**:
1. If process crashed: `./scripts/nexushv-supervisor.sh restart`
2. If port in use: `lsof -i :8080` and kill conflicting process
3. If OOM killed: check `dmesg | grep oom` and increase RAM or reduce VM count
4. If disk full: clean old logs `find logs/ -name '*.log.*' -mtime +7 -delete`

---

## Runbook: HA Split-Brain

**Symptoms**: Multiple hosts claim to be master. Cluster health shows SPLIT.

**Diagnosis**:
```bash
# Check from each host
curl http://<host-ip>:8081/ha/status | jq '.is_master, .has_quorum'

# Check network connectivity between hosts
ping -c 3 <peer-ip>

# Check multicast
tcpdump -i any udp port 5405
```

**Resolution**:
1. Identify which side has quorum (majority of nodes)
2. The side with quorum is authoritative
3. On the minority side: stop HA daemon, fix network, restart
4. If 50/50 split: stop HA on one side, restart after network is fixed
5. VMs should NOT be running on both sides — check and shut down duplicates

---

## Runbook: VM Won't Start

**Symptoms**: `virsh start <vm>` fails or API returns error.

**Diagnosis**:
```bash
virsh dominfo <vm>
virsh dumpxml <vm> | head -50
virsh domblklist <vm>
journalctl -u libvirtd --since "5 minutes ago"
```

**Common causes**:
- Disk image missing: check `virsh domblklist <vm>` paths exist
- Insufficient resources: check host CPU/RAM availability
- Locked disk: another process has the image open
- Permission error: check image file ownership

---

## Runbook: High CPU on Host

**Symptoms**: Host CPU > 90%, VMs experiencing steal time.

**Diagnosis**:
```bash
# Top processes
top -bn1 | head -20

# Per-VM CPU usage
virsh domstats --cpu-total

# Check if KSM is consuming CPU
cat /sys/kernel/mm/ksm/run
cat /sys/kernel/mm/ksm/pages_sharing

# Check for runaway QEMU processes
ps aux | grep qemu | sort -k3 -rn
```

**Resolution**:
1. Identify which VM(s) are consuming excessive CPU
2. If vCPU overcommit > 3:1: migrate VMs to other hosts
3. If KSM: reduce scan rate `echo 50 > /sys/kernel/mm/ksm/pages_to_scan`
4. If single VM: check inside guest for runaway processes

---

## Runbook: Ollama/AI Not Available

**Symptoms**: AI chat returns "not available" error.

**Diagnosis**:
```bash
systemctl status ollama
curl http://localhost:11434/api/tags
journalctl -u ollama --since "10 minutes ago"
nvidia-smi  # if GPU
```

**Resolution**:
1. Restart Ollama: `systemctl restart ollama`
2. Check model: `ollama list`
3. Reload model: `ollama run nexushv-ai`
4. If OOM: switch to smaller model `ollama run qwen2.5:1.5b`

---

## Runbook: Database Issues

**Symptoms**: API errors mentioning SQLite or database locked.

**Diagnosis**:
```bash
ls -la data/nexushv.db
sqlite3 data/nexushv.db "SELECT COUNT(*) FROM users;"
sqlite3 data/nexushv.db ".tables"
```

**Resolution**:
1. If corrupted: `sqlite3 data/nexushv.db ".recover" | sqlite3 data/nexushv-new.db`
2. If locked: check for stuck processes `lsof data/nexushv.db`
3. If missing: restart API (auto-creates with defaults)
