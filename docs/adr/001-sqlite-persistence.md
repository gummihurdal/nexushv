# ADR-001: SQLite for Persistence Layer

## Status
Accepted

## Context
NexusHV needs persistent storage for users, audit logs, alerts, settings, and metrics history. Options considered:
- PostgreSQL: Full-featured RDBMS, requires separate process
- SQLite: Embedded, zero-config, single-file database
- Redis: In-memory, good for caching but poor for durability
- Plain files: Simple but no query capability

## Decision
Use SQLite for all persistence needs.

## Rationale
- **Zero config**: No separate database server to manage
- **Single file**: Easy backup (`cp data/nexushv.db backup/`)
- **Sufficient performance**: NexusHV is single-host; SQLite handles thousands of writes/second
- **Embedded**: No network overhead, no connection management
- **ACID compliant**: Proper transaction support
- **Small footprint**: < 1MB binary, trivial resource usage

## Consequences
- Cannot scale horizontally (single-writer limitation)
- No built-in replication (would need external solution)
- WAL mode needed for concurrent read/write from API + background tasks
- For multi-host NexusHV deployment, would need to migrate to PostgreSQL

## Migration Path
If PostgreSQL is needed later, use SQLAlchemy ORM to abstract the database layer. Current raw SQL is simple enough that migration would be straightforward.
