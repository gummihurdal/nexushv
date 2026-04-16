# Changelog

## [2.0.0] - 2026-04-16

### Added

#### Security
- JWT authentication with HS256 + bcrypt password hashing
- Role-based access control: admin, operator, readonly
- Audit trail logging for all state-changing operations
- Rate limiting: 100 requests/minute per IP
- Input validation on all API models (Pydantic)
- TLS/HTTPS support with self-signed certificate generation
- Systemd services with NoNewPrivileges, ProtectSystem

#### API
- 66 API routes with auto-generated OpenAPI documentation
- VM clone (full and linked/CoW)
- VM resize (CPU and memory)
- VM disk management
- Batch VM operations
- VM config export
- VM list filtering, search, and sorting
- Host maintenance mode
- AI command execution (whitelisted safe commands)
- Webhooks for event notifications
- DRS (Distributed Resource Scheduling) recommendations
- Right-sizing recommendations
- Capacity planning with headroom analysis
- Cluster topology API
- System events API
- Feature discovery endpoint (/api/info)
- Dashboard overview endpoint

#### AI
- 101 PhD-level Q&A training entries (was 16)
- Enhanced system prompt with comprehensive expertise areas
- Real system metrics gathering for context-aware responses
- Safe command execution framework
- Proactive monitoring background task (every 5 minutes)
- Streaming AI chat via WebSocket
- Automatic alert creation from AI scan findings
- Deduplication of scan-generated alerts

#### UI
- Dashboard tab with real-time WebSocket charts
- Animated gauge rings for CPU/Memory/Disk
- Per-VM CPU and RAM live status
- Storage pool visualization
- AI right-sizing recommendations panel
- Quick action buttons
- Alerts tab with severity filtering and acknowledgment
- Alert badge counter on tab bar
- Connection status indicator
- HA dashboard with real HA daemon data
- Streaming AI chat with WebSocket fallback to REST
- 5-tab layout: Dashboard, Console, HA, Alerts, AI

#### HA Engine
- Quorum-based decisions (majority required for failover)
- Split-brain detection with conservative failover
- Raft-inspired master election with term tracking
- Cluster health status: GREEN/YELLOW/RED/SPLIT
- Self-healing service monitor with auto-restart
- Network partition detection via datastore heartbeat
- Anti-affinity rules for VM placement
- Dependency-aware restart ordering
- Preferred host failover targeting

#### Observability
- Prometheus metrics endpoint (/metrics)
- Grafana dashboard configuration
- Structured JSON logging with rotation (10MB × 5)
- Historical metrics with 7-day retention
- Per-VM metrics recording
- Real-time event WebSocket (/ws/events)

#### Infrastructure
- Dockerfile and docker-compose.yml
- GitHub Actions CI/CD pipeline
- Systemd service files
- Process supervisor with watchdog mode
- Production installer script
- Self-signed TLS certificate generator
- requirements.txt for pip

#### Documentation
- Comprehensive README v2.0 with feature comparison
- 4 Architecture Decision Records
- 6 operational runbooks
- API documentation (66 routes)

#### Testing
- 113 automated tests (65 API + 18 HA + 30 AI)
- Load testing script
- CI pipeline for Python 3.11 and 3.12

### Performance
- API response time: 7-22ms average (250x improvement)
- Async bcrypt via run_in_executor
- TTL caching on expensive endpoints
- Non-blocking WebSocket connections

### Changed
- API version bumped to 2.0.0
- Ollama Modelfile: 8K context, 4K output, improved system prompt
- HA engine rewritten with quorum and split-brain support

## [1.0.0] - 2026-04-15

### Added
- Initial release
- FastAPI REST API with demo mode
- HA failover daemon with STONITH
- NEXUS AI with Ollama integration
- React UI with management console
- 16 AI training entries
