# ADR-003: Docker Deployment Option

## Status
Accepted

## Context
NexusHV needs both bare-metal and containerized deployment options.
- Bare-metal: required for production (KVM needs host access)
- Docker: useful for development, CI/CD testing, and demo environments

## Decision
Provide both deployment methods:
1. `scripts/install.sh` for bare-metal production deployment
2. `docker-compose.yml` for development/demo with 3 services (API, HA, Ollama)

## Rationale
- Docker can't run KVM inside containers without `--privileged` and nested virt
- Docker is perfect for demo mode (mock VMs, no KVM needed)
- CI/CD pipelines need Docker for automated testing
- Development is faster with Docker Compose

## Consequences
- Docker images run in demo mode by default
- For production with real VMs, bare-metal install is required
- Docker Compose makes it easy to spin up the full stack for demos
