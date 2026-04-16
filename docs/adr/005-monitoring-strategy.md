# ADR-005: Monitoring Strategy

## Status
Accepted

## Context
NexusHV needs comprehensive monitoring across multiple dimensions:
- API performance (request rate, latency, errors)
- Host health (CPU, RAM, disk, network)
- VM health (per-VM CPU, RAM, I/O)
- HA cluster state (peers, quorum, failovers)
- AI system (model availability, response times)

## Decision
Three-tier monitoring approach:

### Tier 1: Built-in (always available)
- `/health` endpoint with component checks
- `/metrics` Prometheus endpoint
- SQLite metrics history (7-day retention)
- WebSocket real-time metrics (/ws/metrics)
- WebSocket event stream (/ws/events)
- Proactive AI scanning (every 5 minutes)

### Tier 2: Prometheus + Grafana (optional)
- Prometheus scrapes `/metrics` every 10s
- Pre-built Grafana dashboard in `observability/`
- Long-term retention configured in Prometheus
- Alerting via Grafana alert rules

### Tier 3: External integration (optional)
- Webhooks for event notifications
- JSON structured logging for log aggregation (ELK, Loki)
- Future: OpenTelemetry traces for distributed tracing

## Rationale
- Tier 1 works out of the box with zero dependencies
- Tier 2 is industry standard and well-documented
- Tier 3 integrates with existing enterprise tooling
- Each tier is independent — no tier requires another

## Consequences
- No dependency on external monitoring for basic health
- Prometheus setup is optional but recommended for production
- Log aggregation requires external tools (not bundled)
