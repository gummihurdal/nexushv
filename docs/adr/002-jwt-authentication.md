# ADR-002: JWT for API Authentication

## Status
Accepted

## Context
NexusHV API needs authentication. Options considered:
- JWT tokens: Stateless, self-contained, widely supported
- Session cookies: Stateful, requires server-side session store
- API keys: Simple but no expiration or role embedding
- OAuth2/OIDC: Full-featured but complex for self-hosted appliance

## Decision
Use JWT (HS256) with bcrypt-hashed passwords stored in SQLite.

## Rationale
- **Stateless**: No server-side session store needed
- **Self-contained**: Token carries username and role — no DB lookup per request
- **Standard**: Widely supported by HTTP clients and frontends
- **Simple**: HS256 with a server-side secret is sufficient for single-host
- **Bcrypt**: Industry-standard password hashing with salt

## Auth Flow
1. POST /api/auth/login with username/password
2. Server validates against bcrypt hash in SQLite
3. Returns JWT token (24-hour expiry)
4. Client includes `Authorization: Bearer <token>` in subsequent requests
5. Server validates JWT signature and expiry on each request

## Roles
- `admin`: Full access including user management and audit log
- `operator`: Can manage VMs, storage, networks; cannot manage users
- `readonly`: Can view all data but cannot make changes

## Consequences
- Token revocation requires maintaining a blacklist (not implemented yet)
- HS256 means shared secret — not suitable for distributed auth
- For enterprise deployment, would add OAuth2/OIDC support
