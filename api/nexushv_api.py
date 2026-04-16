#!/usr/bin/env python3
"""
NexusHV — Bare Metal Hypervisor Management API
Production-grade backend with JWT auth, RBAC, audit logging,
Prometheus metrics, SQLite persistence, and comprehensive error handling.

Supports two modes:
  - Live mode: connects to libvirtd (qemu:///system) on bare metal
  - Demo mode: serves realistic mock data when libvirtd is unavailable

Requirements:
    pip install fastapi uvicorn libvirt-python psutil websockets httpx aiofiles
    pip install pyjwt bcrypt prometheus-client aiosqlite python-multipart
"""

import asyncio
import json
import subprocess
import psutil
import time
import os
import sys
import logging
import logging.handlers
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional
from contextlib import contextmanager

from fastapi import (
    FastAPI, HTTPException, WebSocket, WebSocketDisconnect,
    Request, Depends, Header, Response, status
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import jwt
import bcrypt
import httpx

# ── Logging with rotation ─────────────────────────────────────────────────
LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

log_formatter = logging.Formatter(
    '{"ts":"%(asctime)s","level":"%(levelname)s","module":"%(name)s","msg":"%(message)s"}'
)
file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "nexushv-api.log"),
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5,
)
file_handler.setFormatter(log_formatter)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s [API] %(levelname)s %(message)s"))

logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])
log = logging.getLogger("nexushv-api")

# ── Configuration ─────────────────────────────────────────────────────────
JWT_SECRET = os.getenv("NEXUSHV_JWT_SECRET", secrets.token_hex(32))

# ── Simple TTL Cache ──────────────────────────────────────────────────────
_cache: dict[str, tuple[float, object]] = {}

def cached(key: str, ttl: float = 2.0):
    """Simple TTL cache decorator for expensive endpoints."""
    now = time.time()
    if key in _cache:
        ts, val = _cache[key]
        if now - ts < ttl:
            return val
    return None

def set_cache(key: str, value: object):
    _cache[key] = (time.time(), value)
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24
RATE_LIMIT_REQUESTS = 100  # per minute per IP
RATE_LIMIT_WINDOW = 60

# ── Try to connect to libvirt; fall back to demo mode ─────────────────────
DEMO_MODE = False
try:
    import libvirt
    _test_conn = libvirt.open("qemu:///system")
    if _test_conn:
        _test_conn.close()
        log.info("Connected to libvirt — running in LIVE mode")
    else:
        raise Exception("libvirt.open returned None")
except Exception as e:
    DEMO_MODE = True
    log.warning(f"libvirt unavailable ({e}) — running in DEMO mode with mock data")

# ── SQLite Database ───────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "nexushv.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def init_db():
    """Initialize SQLite database with all tables."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'readonly',
            created_at TEXT DEFAULT (datetime('now')),
            last_login TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now')),
            user TEXT,
            action TEXT NOT NULL,
            resource TEXT,
            detail TEXT,
            ip TEXT,
            success INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now')),
            severity TEXT NOT NULL,
            component TEXT,
            title TEXT NOT NULL,
            detail TEXT,
            acknowledged INTEGER DEFAULT 0,
            ack_by TEXT,
            ack_at TEXT
        );
        CREATE TABLE IF NOT EXISTS vm_notes (
            vm_name TEXT PRIMARY KEY,
            notes TEXT,
            tags TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS metrics_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now')),
            metric_type TEXT NOT NULL,
            name TEXT NOT NULL,
            value REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    # Create default admin user if none exists
    cur = conn.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        pw_hash = bcrypt.hashpw(b"admin", bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("admin", pw_hash, "admin")
        )
        log.info("Created default admin user (username: admin, password: admin)")
    conn.commit()
    conn.close()

init_db()

@contextmanager
def get_db():
    """Get a database connection with proper error handling."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def audit_log(user: str, action: str, resource: str = None, detail: str = None, ip: str = None, success: bool = True):
    """Record an action in the audit log."""
    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO audit_log (user, action, resource, detail, ip, success) VALUES (?, ?, ?, ?, ?, ?)",
                (user, action, resource, detail, ip, int(success))
            )
    except Exception as e:
        log.error(f"Failed to write audit log: {e}")

# ── Rate Limiting ─────────────────────────────────────────────────────────
_rate_limit_store: dict[str, list[float]] = {}

def check_rate_limit(ip: str) -> bool:
    """Simple in-memory rate limiter."""
    now = time.time()
    if ip not in _rate_limit_store:
        _rate_limit_store[ip] = []
    # Clean old entries
    _rate_limit_store[ip] = [t for t in _rate_limit_store[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_limit_store[ip]) >= RATE_LIMIT_REQUESTS:
        return False
    _rate_limit_store[ip].append(now)
    return True

# ── JWT Authentication ────────────────────────────────────────────────────
class TokenData(BaseModel):
    username: str
    role: str
    exp: float

def create_token(username: str, role: str) -> str:
    """Create a JWT token."""
    payload = {
        "sub": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_token(token: str) -> TokenData:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return TokenData(
            username=payload["sub"],
            role=payload["role"],
            exp=payload["exp"],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")

async def get_current_user(authorization: Optional[str] = Header(None)) -> Optional[TokenData]:
    """Extract user from Authorization header. Returns None if no auth provided."""
    if not authorization:
        return None
    try:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer":
            return None
        return decode_token(token)
    except HTTPException:
        return None

async def require_auth(authorization: Optional[str] = Header(None)) -> TokenData:
    """Require valid authentication."""
    if not authorization:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(401, "Invalid auth scheme")
    return decode_token(token)

def require_role(*roles):
    """Dependency that checks user has one of the required roles."""
    async def checker(user: TokenData = Depends(require_auth)):
        if user.role not in roles:
            raise HTTPException(403, f"Role '{user.role}' not authorized. Need: {', '.join(roles)}")
        return user
    return checker

# ── Prometheus Metrics ────────────────────────────────────────────────────
try:
    from prometheus_client import (
        Counter, Histogram, Gauge, Info,
        generate_latest, CONTENT_TYPE_LATEST, CollectorRegistry, REGISTRY
    )

    REQUEST_COUNT = Counter(
        "nexushv_api_requests_total", "Total API requests",
        ["method", "endpoint", "status"]
    )
    REQUEST_LATENCY = Histogram(
        "nexushv_api_request_duration_seconds", "Request latency",
        ["method", "endpoint"]
    )
    ACTIVE_WEBSOCKETS = Gauge(
        "nexushv_active_websocket_connections", "Active WebSocket connections"
    )
    VM_COUNT = Gauge("nexushv_vm_count", "Number of VMs", ["state"])
    HOST_CPU = Gauge("nexushv_host_cpu_percent", "Host CPU usage")
    HOST_RAM = Gauge("nexushv_host_ram_percent", "Host RAM usage")
    HOST_DISK = Gauge("nexushv_host_disk_percent", "Host disk usage")
    AI_REQUEST_COUNT = Counter("nexushv_ai_requests_total", "AI chat requests")
    AI_REQUEST_LATENCY = Histogram("nexushv_ai_request_duration_seconds", "AI request latency")
    NEXUSHV_INFO = Info("nexushv", "NexusHV system info")
    NEXUSHV_INFO.info({
        "version": "2.0.0",
        "mode": "demo" if DEMO_MODE else "live",
    })
    PROMETHEUS_AVAILABLE = True
except Exception as e:
    PROMETHEUS_AVAILABLE = False
    log.warning(f"Prometheus metrics unavailable: {e}")

# ── FastAPI App ───────────────────────────────────────────────────────────
app = FastAPI(
    title="NexusHV API",
    version="2.0.0",
    description="Production-grade hypervisor management API with JWT auth, RBAC, and real-time monitoring",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Middleware: rate limiting, metrics, logging ────────────────────────────
@app.middleware("http")
async def api_middleware(request: Request, call_next):
    start = time.time()
    ip = request.client.host if request.client else "unknown"

    # Rate limiting
    if not check_rate_limit(ip):
        log.warning(f"Rate limit exceeded for {ip}")
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

    try:
        response = await call_next(request)
    except Exception as e:
        log.error(f"Unhandled error on {request.method} {request.url.path}: {e}", exc_info=True)
        response = JSONResponse(status_code=500, content={"detail": "Internal server error"})

    duration = time.time() - start

    # Prometheus metrics
    if PROMETHEUS_AVAILABLE:
        endpoint = request.url.path
        REQUEST_COUNT.labels(request.method, endpoint, response.status_code).inc()
        REQUEST_LATENCY.labels(request.method, endpoint).observe(duration)

    # Access logging for non-static requests
    if not request.url.path.startswith("/assets"):
        log.info(f"{request.method} {request.url.path} {response.status_code} {duration:.3f}s [{ip}]")

    return response

# ── Models ─────────────────────────────────────────────────────────────────
class VMCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    os: str = "ubuntu22.04"
    cpu: int = Field(default=2, ge=1, le=128)
    ram_gb: int = Field(default=4, ge=1, le=4096)
    disk_gb: int = Field(default=50, ge=1, le=100000)
    network: str = "default"
    iso_path: Optional[str] = None

class VMAction(BaseModel):
    action: str  # start | stop | reboot | suspend | resume | force-stop

class MigrateRequest(BaseModel):
    dest_host: str
    dest_uri: Optional[str] = None
    live: bool = True
    bandwidth_mbps: int = Field(default=0, ge=0)

class SnapshotCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = ""

class AIChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)

class LoginRequest(BaseModel):
    username: str
    password: str

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(..., min_length=6)
    role: str = Field(default="readonly", pattern=r"^(admin|operator|readonly)$")

class AlertAck(BaseModel):
    acknowledged: bool = True

# ── Mock data for demo mode ───────────────────────────────────────────────
import random
import uuid as _uuid

_MOCK_VMS = [
    {"id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "name": "prod-db-primary",  "state": "poweredOn",  "cpu": 8,  "ram_mb": 32768, "cpu_pct": 0, "ram_used_pct": 82.1, "persistent": True, "autostart": True,  "disk_gb": 500, "os": "Ubuntu 22.04", "ip": "10.0.2.10", "uptime_s": 864000},
    {"id": "b2c3d4e5-f6a7-8901-bcde-f12345678901", "name": "prod-web-01",      "state": "poweredOn",  "cpu": 4,  "ram_mb": 16384, "cpu_pct": 0, "ram_used_pct": 55.3, "persistent": True, "autostart": True,  "disk_gb": 100, "os": "Debian 12", "ip": "10.0.2.11", "uptime_s": 604800},
    {"id": "c3d4e5f6-a7b8-9012-cdef-123456789012", "name": "k8s-master-01",    "state": "poweredOn",  "cpu": 4,  "ram_mb": 8192,  "cpu_pct": 0, "ram_used_pct": 42.0, "persistent": True, "autostart": True,  "disk_gb": 80,  "os": "Rocky Linux 9", "ip": "10.0.2.20", "uptime_s": 432000},
    {"id": "d4e5f6a7-b8c9-0123-defa-234567890123", "name": "k8s-worker-01",    "state": "poweredOn",  "cpu": 4,  "ram_mb": 8192,  "cpu_pct": 0, "ram_used_pct": 38.5, "persistent": True, "autostart": False, "disk_gb": 80,  "os": "Rocky Linux 9", "ip": "10.0.2.21", "uptime_s": 432000},
    {"id": "e5f6a7b8-c9d0-1234-efab-345678901234", "name": "dev-sandbox-01",   "state": "poweredOff", "cpu": 2,  "ram_mb": 4096,  "cpu_pct": 0, "ram_used_pct": 0,    "persistent": True, "autostart": False, "disk_gb": 50,  "os": "Ubuntu 20.04", "ip": None, "uptime_s": 0},
    {"id": "f6a7b8c9-d0e1-2345-fabc-456789012345", "name": "win-rdp-01",       "state": "suspended",  "cpu": 4,  "ram_mb": 16384, "cpu_pct": 0, "ram_used_pct": 60.2, "persistent": True, "autostart": False, "disk_gb": 200, "os": "Windows Server 2022", "ip": "10.0.2.30", "uptime_s": 172800},
    {"id": "a7b8c9d0-e1f2-3456-abcf-567890123456", "name": "backup-appliance", "state": "poweredOn",  "cpu": 2,  "ram_mb": 4096,  "cpu_pct": 0, "ram_used_pct": 25.0, "persistent": True, "autostart": True,  "disk_gb": 4000,"os": "Ubuntu 20.04", "ip": "10.0.2.40", "uptime_s": 864000},
]

_MOCK_DISK_IO = {}  # name -> {"read_bytes": int, "write_bytes": int}
_MOCK_NET_IO = {}   # name -> {"rx_bytes": int, "tx_bytes": int}

def _mock_vm_tick():
    """Add live-ish CPU/RAM/IO percentages to mock VMs."""
    for vm in _MOCK_VMS:
        if vm["state"] == "poweredOn":
            base = {"prod-db-primary": 65, "prod-web-01": 28, "k8s-master-01": 18, "k8s-worker-01": 15, "backup-appliance": 5}
            b = base.get(vm["name"], 10)
            vm["cpu_pct"] = round(max(1, min(99, b + (random.random() - 0.5) * 20)), 1)
            vm["ram_used_pct"] = round(max(5, min(98, vm["ram_used_pct"] + (random.random() - 0.5) * 2)), 1)
            vm["uptime_s"] = vm.get("uptime_s", 0) + 2

            # Simulate disk I/O
            name = vm["name"]
            if name not in _MOCK_DISK_IO:
                _MOCK_DISK_IO[name] = {"read_bytes": random.randint(100_000_000, 500_000_000), "write_bytes": random.randint(50_000_000, 200_000_000)}
            _MOCK_DISK_IO[name]["read_bytes"] += random.randint(0, 5_000_000)
            _MOCK_DISK_IO[name]["write_bytes"] += random.randint(0, 2_000_000)

            # Simulate network I/O
            if name not in _MOCK_NET_IO:
                _MOCK_NET_IO[name] = {"rx_bytes": random.randint(100_000_000, 1_000_000_000), "tx_bytes": random.randint(50_000_000, 500_000_000)}
            _MOCK_NET_IO[name]["rx_bytes"] += random.randint(0, 1_000_000)
            _MOCK_NET_IO[name]["tx_bytes"] += random.randint(0, 500_000)
        else:
            vm["cpu_pct"] = 0

# ── libvirt helpers (live mode only) ──────────────────────────────────────
if not DEMO_MODE:
    STATE_MAP = {
        libvirt.VIR_DOMAIN_RUNNING:    "poweredOn",
        libvirt.VIR_DOMAIN_BLOCKED:    "poweredOn",
        libvirt.VIR_DOMAIN_PAUSED:     "suspended",
        libvirt.VIR_DOMAIN_SHUTDOWN:   "poweredOff",
        libvirt.VIR_DOMAIN_SHUTOFF:    "poweredOff",
        libvirt.VIR_DOMAIN_CRASHED:    "error",
        libvirt.VIR_DOMAIN_PMSUSPENDED: "suspended",
    }

    _libvirt_pool = []
    _pool_lock = asyncio.Lock()

    def get_conn():
        """Get a libvirt connection with error handling."""
        try:
            conn = libvirt.open("qemu:///system")
            if not conn:
                raise HTTPException(503, "Cannot connect to hypervisor: returned None")
            return conn
        except libvirt.libvirtError as e:
            log.error(f"libvirt connection failed: {e}")
            raise HTTPException(503, f"Cannot connect to hypervisor: {e}")

    def vm_info(dom):
        """Extract VM info with comprehensive error handling."""
        try:
            state, _ = dom.state()
            info = dom.info()
            try:
                stats = dom.getCPUStats(True)[0]
                cpu_pct = round(stats.get("cpu_time", 0) / 1e9 / max(psutil.cpu_count(), 1), 2)
            except Exception:
                cpu_pct = 0
            return {
                "id": dom.UUIDString(),
                "name": dom.name(),
                "state": STATE_MAP.get(state, "unknown"),
                "cpu": info[3],
                "ram_mb": info[1] // 1024,
                "cpu_pct": cpu_pct,
                "ram_used_pct": round(info[2] / info[1] * 100, 1) if info[1] else 0,
                "persistent": dom.isPersistent(),
                "autostart": dom.autostart(),
            }
        except Exception as e:
            log.error(f"Error getting VM info for {dom.name()}: {e}")
            return {
                "id": "unknown",
                "name": dom.name(),
                "state": "error",
                "cpu": 0, "ram_mb": 0, "cpu_pct": 0, "ram_used_pct": 0,
                "persistent": False, "autostart": False,
            }

# ── Auth Routes ───────────────────────────────────────────────────────────
@app.post("/api/auth/login", tags=["Authentication"])
async def login(req: LoginRequest, request: Request):
    """Authenticate and receive a JWT token."""
    ip = request.client.host if request.client else "unknown"
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE username = ? AND active = 1", (req.username,)).fetchone()
        if not row:
            audit_log(req.username, "login_failed", ip=ip, success=False)
            raise HTTPException(401, "Invalid credentials")
        # Run bcrypt in thread pool to avoid blocking event loop
        loop = asyncio.get_event_loop()
        pw_valid = await loop.run_in_executor(None, bcrypt.checkpw, req.password.encode(), row["password_hash"].encode())
        if not pw_valid:
            audit_log(req.username, "login_failed", ip=ip, success=False)
            raise HTTPException(401, "Invalid credentials")
        token = create_token(row["username"], row["role"])
        db.execute("UPDATE users SET last_login = datetime('now') WHERE username = ?", (req.username,))
        audit_log(row["username"], "login", ip=ip)
        return {
            "token": token,
            "username": row["username"],
            "role": row["role"],
            "expires_in": JWT_EXPIRE_HOURS * 3600,
        }

@app.get("/api/auth/me", tags=["Authentication"])
async def me(user: TokenData = Depends(require_auth)):
    """Get current user info."""
    return {"username": user.username, "role": user.role}

@app.post("/api/auth/users", tags=["Authentication"])
async def create_user(req: UserCreate, user: TokenData = Depends(require_role("admin"))):
    """Create a new user (admin only)."""
    pw_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (req.username, pw_hash, req.role)
            )
        audit_log(user.username, "create_user", req.username, f"role={req.role}")
        return {"status": "created", "username": req.username, "role": req.role}
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"User '{req.username}' already exists")

@app.get("/api/auth/users", tags=["Authentication"])
async def list_users(user: TokenData = Depends(require_role("admin"))):
    """List all users (admin only)."""
    with get_db() as db:
        rows = db.execute("SELECT username, role, created_at, last_login, active FROM users").fetchall()
        return [dict(r) for r in rows]

# ── Audit Log Routes ─────────────────────────────────────────────────────
@app.get("/api/audit", tags=["Audit"])
async def get_audit_log(limit: int = 100, user: TokenData = Depends(require_role("admin"))):
    """Get audit log entries."""
    with get_db() as db:
        rows = db.execute("SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (min(limit, 1000),)).fetchall()
        return [dict(r) for r in rows]

# ── Alert Routes ──────────────────────────────────────────────────────────
@app.get("/api/alerts", tags=["Alerts"])
async def get_alerts(acknowledged: Optional[bool] = None):
    """Get system alerts."""
    with get_db() as db:
        if acknowledged is None:
            rows = db.execute("SELECT * FROM alerts ORDER BY ts DESC LIMIT 200").fetchall()
        else:
            rows = db.execute("SELECT * FROM alerts WHERE acknowledged = ? ORDER BY ts DESC LIMIT 200", (int(acknowledged),)).fetchall()
        return [dict(r) for r in rows]

@app.post("/api/alerts/{alert_id}/acknowledge", tags=["Alerts"])
async def acknowledge_alert(alert_id: int, user: TokenData = Depends(require_auth)):
    """Acknowledge an alert."""
    with get_db() as db:
        db.execute(
            "UPDATE alerts SET acknowledged = 1, ack_by = ?, ack_at = datetime('now') WHERE id = ?",
            (user.username, alert_id)
        )
    return {"status": "acknowledged", "alert_id": alert_id}

def create_alert(severity: str, component: str, title: str, detail: str = ""):
    """Create a new alert in the database and broadcast to WebSocket clients."""
    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO alerts (severity, component, title, detail) VALUES (?, ?, ?, ?)",
                (severity, component, title, detail)
            )
        log.warning(f"ALERT [{severity}] {component}: {title}")
        # Broadcast to WebSocket subscribers (non-blocking)
        try:
            asyncio.get_event_loop().create_task(broadcast_event("alert", {
                "severity": severity, "component": component, "title": title, "detail": detail[:200],
            }))
        except RuntimeError:
            pass  # No event loop yet during startup
    except Exception as e:
        log.error(f"Failed to create alert: {e}")

# ── Routes: Virtual Machines ──────────────────────────────────────────────
@app.get("/api/vms", tags=["Virtual Machines"])
def list_vms(state: Optional[str] = None, search: Optional[str] = None, sort: Optional[str] = None):
    """List all virtual machines with optional filtering and sorting.

    - state: filter by state (poweredOn, poweredOff, suspended)
    - search: filter by name (case-insensitive substring match)
    - sort: sort by field (name, cpu_pct, ram_used_pct, state)
    """
    if DEMO_MODE:
        _mock_vm_tick()
        vms = _MOCK_VMS
        if state:
            vms = [v for v in vms if v["state"] == state]
        if search:
            search_lower = search.lower()
            vms = [v for v in vms if search_lower in v["name"].lower() or search_lower in v.get("os", "").lower()]
        if sort:
            reverse = sort.startswith("-")
            field = sort.lstrip("-")
            if field in ("name", "cpu_pct", "ram_used_pct", "state", "cpu", "ram_mb"):
                vms = sorted(vms, key=lambda v: v.get(field, 0), reverse=reverse)
        return vms
    conn = get_conn()
    try:
        return [vm_info(d) for d in conn.listAllDomains()]
    except Exception as e:
        log.error(f"Failed to list VMs: {e}")
        raise HTTPException(500, f"Failed to list VMs: {e}")
    finally:
        conn.close()

@app.get("/api/vms/{name}", tags=["Virtual Machines"])
def get_vm(name: str):
    """Get detailed info about a specific VM."""
    if DEMO_MODE:
        _mock_vm_tick()
        vm = next((v for v in _MOCK_VMS if v["name"] == name), None)
        if not vm:
            raise HTTPException(404, f"VM '{name}' not found")
        result = {**vm}
        result["disk_io"] = _MOCK_DISK_IO.get(name, {"read_bytes": 0, "write_bytes": 0})
        result["net_io"] = _MOCK_NET_IO.get(name, {"rx_bytes": 0, "tx_bytes": 0})
        return result
    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        return vm_info(dom)
    except libvirt.libvirtError:
        raise HTTPException(404, f"VM '{name}' not found")
    finally:
        conn.close()

@app.post("/api/vms", tags=["Virtual Machines"])
def create_vm(req: VMCreate, request: Request):
    """Create a new virtual machine."""
    ip = request.client.host if request.client else "unknown"
    audit_log("system", "create_vm", req.name, f"cpu={req.cpu} ram={req.ram_gb}GB disk={req.disk_gb}GB", ip)

    if DEMO_MODE:
        # Check for duplicate names
        if any(v["name"] == req.name for v in _MOCK_VMS):
            raise HTTPException(409, f"VM '{req.name}' already exists")
        new_vm = {
            "id": str(_uuid.uuid4()), "name": req.name, "state": "poweredOff",
            "cpu": req.cpu, "ram_mb": req.ram_gb * 1024, "cpu_pct": 0,
            "ram_used_pct": 0, "persistent": True, "autostart": False,
            "disk_gb": req.disk_gb, "os": req.os, "ip": None, "uptime_s": 0,
        }
        _MOCK_VMS.append(new_vm)
        return {"status": "created", "name": req.name, "disk": f"/var/lib/libvirt/images/{req.name}.qcow2"}

    disk_path = f"/var/lib/libvirt/images/{req.name}.qcow2"
    result = subprocess.run(
        ["qemu-img", "create", "-f", "qcow2", disk_path, f"{req.disk_gb}G"],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise HTTPException(500, f"Disk creation failed: {result.stderr}")
    cmd = [
        "virt-install", "--name", req.name,
        "--memory", str(req.ram_gb * 1024), "--vcpus", str(req.cpu),
        "--disk", f"path={disk_path},format=qcow2,bus=virtio",
        "--network", f"network={req.network},model=virtio",
        "--graphics", "vnc,listen=127.0.0.1", "--video", "virtio",
        "--channel", "unix,target_type=virtio,name=org.qemu.guest_agent.0",
        "--noautoconsole",
    ]
    if req.iso_path:
        cmd += ["--cdrom", req.iso_path, "--os-variant", req.os.replace(" ", "").lower(), "--wait=0"]
    else:
        cmd.append("--import")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise HTTPException(500, f"VM creation failed: {result.stderr}")
    return {"status": "created", "name": req.name, "disk": disk_path}

@app.post("/api/vms/{name}/action", tags=["Virtual Machines"])
def vm_action(name: str, req: VMAction, request: Request):
    """Execute an action on a VM (start, stop, reboot, suspend, resume, force-stop)."""
    ip = request.client.host if request.client else "unknown"
    valid_actions = {"start", "stop", "reboot", "suspend", "resume", "force-stop"}
    if req.action not in valid_actions:
        raise HTTPException(400, f"Unknown action: {req.action}. Valid: {', '.join(sorted(valid_actions))}")

    audit_log("system", f"vm_{req.action}", name, ip=ip)

    if DEMO_MODE:
        vm = next((v for v in _MOCK_VMS if v["name"] == name), None)
        if not vm:
            raise HTTPException(404, f"VM '{name}' not found")
        action = req.action
        if action == "start":
            vm["state"] = "poweredOn"
            vm["ip"] = vm.get("ip") or f"10.0.2.{random.randint(10, 250)}"
            vm["uptime_s"] = 0
        elif action in ("stop", "force-stop"):
            vm["state"] = "poweredOff"
            vm["uptime_s"] = 0
        elif action == "suspend":
            vm["state"] = "suspended"
        elif action == "resume":
            vm["state"] = "poweredOn"
        elif action == "reboot":
            vm["uptime_s"] = 0  # stays poweredOn
        return {"status": "ok", "vm": name, "action": action}

    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        action = req.action
        if action == "start":
            dom.create()
        elif action == "stop":
            dom.shutdown()
        elif action == "force-stop":
            dom.destroy()
        elif action == "reboot":
            dom.reboot()
        elif action == "suspend":
            dom.suspend()
        elif action == "resume":
            dom.resume()
        return {"status": "ok", "vm": name, "action": action}
    except libvirt.libvirtError as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

@app.delete("/api/vms/{name}", tags=["Virtual Machines"])
def delete_vm(name: str, delete_disk: bool = False, request: Request = None):
    """Delete a VM. Optionally delete its disk image."""
    ip = request.client.host if request and request.client else "unknown"
    audit_log("system", "delete_vm", name, f"delete_disk={delete_disk}", ip)

    if DEMO_MODE:
        global _MOCK_VMS
        if not any(v["name"] == name for v in _MOCK_VMS):
            raise HTTPException(404, f"VM '{name}' not found")
        _MOCK_VMS = [v for v in _MOCK_VMS if v["name"] != name]
        return {"status": "deleted", "vm": name}

    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        state, _ = dom.state()
        if state == libvirt.VIR_DOMAIN_RUNNING:
            dom.destroy()
        if delete_disk:
            dom.undefineFlags(libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA | libvirt.VIR_DOMAIN_UNDEFINE_NVRAM)
        else:
            dom.undefine()
        return {"status": "deleted", "vm": name}
    except libvirt.libvirtError as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

# ── VM Notes/Tags ─────────────────────────────────────────────────────────
@app.get("/api/vms/{name}/notes", tags=["Virtual Machines"])
def get_vm_notes(name: str):
    """Get notes and tags for a VM."""
    with get_db() as db:
        row = db.execute("SELECT * FROM vm_notes WHERE vm_name = ?", (name,)).fetchone()
        return dict(row) if row else {"vm_name": name, "notes": "", "tags": ""}

@app.put("/api/vms/{name}/notes", tags=["Virtual Machines"])
def set_vm_notes(name: str, notes: str = "", tags: str = ""):
    """Set notes and tags for a VM."""
    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO vm_notes (vm_name, notes, tags, updated_at) VALUES (?, ?, ?, datetime('now'))",
            (name, notes, tags)
        )
    return {"status": "ok", "vm_name": name}

# ── vMotion: Live Migration ───────────────────────────────────────────────
@app.post("/api/vms/{name}/migrate", tags=["Virtual Machines"])
def migrate_vm(name: str, req: MigrateRequest, request: Request):
    """Live migrate a VM to another host."""
    ip = request.client.host if request and request.client else "unknown"
    audit_log("system", "migrate_vm", name, f"dest={req.dest_host} live={req.live}", ip)

    if DEMO_MODE:
        return {"status": "migrated", "vm": name, "source": "localhost", "destination": req.dest_host, "live": True}

    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        dest_conn = libvirt.open(req.dest_host)
        if not dest_conn:
            raise HTTPException(503, f"Cannot connect to destination: {req.dest_host}")
        flags = (libvirt.VIR_MIGRATE_LIVE | libvirt.VIR_MIGRATE_PERSIST_DEST |
                 libvirt.VIR_MIGRATE_UNDEFINE_SOURCE | libvirt.VIR_MIGRATE_COMPRESSED)
        if req.dest_uri:
            flags |= libvirt.VIR_MIGRATE_PEER2PEER
        new_dom = dom.migrate(dest_conn, flags=flags, dname=name, bandwidth=req.bandwidth_mbps)
        if new_dom is None:
            raise HTTPException(500, "Migration failed — no domain returned")
        dest_conn.close()
        return {"status": "migrated", "vm": name, "source": "localhost", "destination": req.dest_host, "live": True}
    except libvirt.libvirtError as e:
        raise HTTPException(500, f"Migration error: {e}")
    finally:
        conn.close()

# ── VM Disk Management ────────────────────────────────────────────────────
@app.get("/api/vms/{name}/disks", tags=["Virtual Machines"])
def list_vm_disks(name: str):
    """List all disks attached to a VM."""
    if DEMO_MODE:
        vm = next((v for v in _MOCK_VMS if v["name"] == name), None)
        if not vm:
            raise HTTPException(404, f"VM '{name}' not found")
        return [
            {"device": "vda", "path": f"/var/lib/libvirt/images/{name}.qcow2",
             "format": "qcow2", "size_gb": vm.get("disk_gb", 50), "bus": "virtio"},
        ]
    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        import xml.etree.ElementTree as ET
        tree = ET.fromstring(dom.XMLDesc(0))
        disks = []
        for disk in tree.findall(".//disk[@device='disk']"):
            source = disk.find("source")
            target = disk.find("target")
            driver = disk.find("driver")
            disks.append({
                "device": target.get("dev", "unknown") if target is not None else "unknown",
                "path": source.get("file", source.get("dev", "unknown")) if source is not None else "unknown",
                "format": driver.get("type", "unknown") if driver is not None else "unknown",
                "bus": target.get("bus", "unknown") if target is not None else "unknown",
            })
        return disks
    except libvirt.libvirtError as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

# ── Batch Operations ──────────────────────────────────────────────────────
class BatchAction(BaseModel):
    vm_names: list[str]
    action: str  # start | stop | suspend | resume

@app.post("/api/batch/vm-action", tags=["Virtual Machines"])
def batch_vm_action(req: BatchAction, request: Request):
    """Execute an action on multiple VMs at once."""
    ip = request.client.host if request and request.client else "unknown"
    valid_actions = {"start", "stop", "suspend", "resume", "force-stop"}
    if req.action not in valid_actions:
        raise HTTPException(400, f"Invalid action. Valid: {', '.join(sorted(valid_actions))}")

    results = []
    for name in req.vm_names:
        try:
            action_req = VMAction(action=req.action)
            vm_action(name, action_req, request)
            results.append({"vm": name, "status": "ok", "action": req.action})
        except HTTPException as e:
            results.append({"vm": name, "status": "error", "detail": e.detail})
        except Exception as e:
            results.append({"vm": name, "status": "error", "detail": str(e)})

    audit_log("system", f"batch_{req.action}", ",".join(req.vm_names),
              f"{len([r for r in results if r['status']=='ok'])}/{len(results)} succeeded", ip)

    return {
        "action": req.action,
        "total": len(results),
        "succeeded": len([r for r in results if r["status"] == "ok"]),
        "results": results,
    }

# ── VM Export/Import ──────────────────────────────────────────────────────
@app.get("/api/vms/{name}/export", tags=["Virtual Machines"])
def export_vm_config(name: str):
    """Export VM configuration as JSON (for backup or template creation)."""
    if DEMO_MODE:
        vm = next((v for v in _MOCK_VMS if v["name"] == name), None)
        if not vm:
            raise HTTPException(404, f"VM '{name}' not found")
        return {
            "name": vm["name"],
            "cpu": vm.get("cpu", 2),
            "ram_mb": vm.get("ram_mb", 4096),
            "disk_gb": vm.get("disk_gb", 50),
            "os": vm.get("os", "unknown"),
            "autostart": vm.get("autostart", False),
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        return {
            "name": name,
            "xml": dom.XMLDesc(0),
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
    except libvirt.libvirtError as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

# ── VM Clone ──────────────────────────────────────────────────────────────
class CloneRequest(BaseModel):
    new_name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    full_clone: bool = True  # True = full copy, False = linked clone (CoW)

@app.post("/api/vms/{name}/clone", tags=["Virtual Machines"])
def clone_vm(name: str, req: CloneRequest, request: Request):
    """Clone a VM. Full clone creates an independent copy; linked clone uses CoW."""
    ip = request.client.host if request and request.client else "unknown"
    audit_log("system", "clone_vm", name, f"new_name={req.new_name} full={req.full_clone}", ip)

    if DEMO_MODE:
        source = next((v for v in _MOCK_VMS if v["name"] == name), None)
        if not source:
            raise HTTPException(404, f"Source VM '{name}' not found")
        if any(v["name"] == req.new_name for v in _MOCK_VMS):
            raise HTTPException(409, f"VM '{req.new_name}' already exists")
        clone = {**source, "id": str(_uuid.uuid4()), "name": req.new_name, "state": "poweredOff",
                 "cpu_pct": 0, "ram_used_pct": 0, "ip": None, "uptime_s": 0}
        _MOCK_VMS.append(clone)
        return {"status": "cloned", "source": name, "clone": req.new_name, "type": "full" if req.full_clone else "linked"}

    # Live mode: use virt-clone
    cmd = ["virt-clone", "--original", name, "--name", req.new_name, "--auto-clone"]
    if not req.full_clone:
        cmd.append("--reflink")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise HTTPException(500, f"Clone failed: {result.stderr}")
    return {"status": "cloned", "source": name, "clone": req.new_name, "type": "full" if req.full_clone else "linked"}

# ── VM Resource Resize ────────────────────────────────────────────────────
class ResizeRequest(BaseModel):
    cpu: Optional[int] = Field(None, ge=1, le=128)
    ram_mb: Optional[int] = Field(None, ge=256, le=4194304)

@app.put("/api/vms/{name}/resize", tags=["Virtual Machines"])
def resize_vm(name: str, req: ResizeRequest, request: Request):
    """Resize VM CPU or memory (may require restart)."""
    ip = request.client.host if request and request.client else "unknown"
    audit_log("system", "resize_vm", name, f"cpu={req.cpu} ram_mb={req.ram_mb}", ip)

    if DEMO_MODE:
        vm = next((v for v in _MOCK_VMS if v["name"] == name), None)
        if not vm:
            raise HTTPException(404, f"VM '{name}' not found")
        changes = {}
        if req.cpu is not None:
            vm["cpu"] = req.cpu
            changes["cpu"] = req.cpu
        if req.ram_mb is not None:
            vm["ram_mb"] = req.ram_mb
            changes["ram_mb"] = req.ram_mb
        return {"status": "resized", "vm": name, "changes": changes}

    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        if req.cpu is not None:
            dom.setVcpusFlags(req.cpu, libvirt.VIR_DOMAIN_AFFECT_CONFIG)
        if req.ram_mb is not None:
            dom.setMemoryFlags(req.ram_mb * 1024, libvirt.VIR_DOMAIN_AFFECT_CONFIG)
        return {"status": "resized", "vm": name, "note": "Restart VM for changes to take effect"}
    except libvirt.libvirtError as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

# ── Snapshots ─────────────────────────────────────────────────────────────
@app.get("/api/vms/{name}/snapshots", tags=["Snapshots"])
def list_snapshots(name: str):
    """List all snapshots for a VM."""
    if DEMO_MODE:
        return [
            {"name": "pre-update-2024-01-15", "desc": "Before kernel update", "created": "2024-01-15T09:00:00Z"},
            {"name": "pre-migration-2024-03-01", "desc": "Before datacenter migration", "created": "2024-03-01T14:30:00Z"},
        ]
    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        snaps = dom.listAllSnapshots()
        return [{"name": s.getName(), "desc": s.getXMLDesc()} for s in snaps]
    except libvirt.libvirtError as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

@app.post("/api/vms/{name}/snapshots", tags=["Snapshots"])
def create_snapshot(name: str, req: SnapshotCreate, request: Request):
    """Create a new snapshot of a VM."""
    ip = request.client.host if request and request.client else "unknown"
    audit_log("system", "create_snapshot", name, f"snapshot={req.name}", ip)

    if DEMO_MODE:
        return {"status": "created", "snapshot": req.name}
    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        xml = f"<domainsnapshot><name>{req.name}</name><description>{req.description}</description></domainsnapshot>"
        snap = dom.snapshotCreateXML(xml, 0)
        return {"status": "created", "snapshot": snap.getName()}
    except libvirt.libvirtError as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

@app.post("/api/vms/{name}/snapshots/{snap}/revert", tags=["Snapshots"])
def revert_snapshot(name: str, snap: str, request: Request):
    """Revert a VM to a specific snapshot."""
    ip = request.client.host if request and request.client else "unknown"
    audit_log("system", "revert_snapshot", name, f"snapshot={snap}", ip)

    if DEMO_MODE:
        return {"status": "reverted", "snapshot": snap}
    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        snapshot = dom.snapshotLookupByName(snap)
        dom.revertToSnapshot(snapshot)
        return {"status": "reverted", "snapshot": snap}
    except libvirt.libvirtError as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

# ── Host / Hypervisor Info ────────────────────────────────────────────────
@app.get("/api/hosts/local", tags=["Hosts"])
def local_host_info():
    """Get local host information including CPU, RAM, disk, and network."""
    c = cached("host_info", ttl=2.0)
    if c is not None:
        return c
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    cpu_freq = psutil.cpu_freq()
    net_io = psutil.net_io_counters()
    boot_time = psutil.boot_time()
    load_avg = os.getloadavg()

    base = {
        "hostname": subprocess.getoutput("hostname"),
        "arch": "x86_64",
        "cpu_count": psutil.cpu_count(),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "cpu_mhz": round(cpu_freq.current) if cpu_freq else 0,
        "cpu_pct": psutil.cpu_percent(interval=0),
        "cpu_per_core": psutil.cpu_percent(percpu=True),
        "load_avg_1m": round(load_avg[0], 2),
        "load_avg_5m": round(load_avg[1], 2),
        "load_avg_15m": round(load_avg[2], 2),
        "ram_total_gb": round(mem.total / 1024**3, 1),
        "ram_used_gb": round(mem.used / 1024**3, 1),
        "ram_available_gb": round(mem.available / 1024**3, 1),
        "ram_pct": mem.percent,
        "swap_total_gb": round(psutil.swap_memory().total / 1024**3, 1),
        "swap_used_gb": round(psutil.swap_memory().used / 1024**3, 1),
        "disk_total_gb": round(disk.total / 1024**3, 1),
        "disk_used_gb": round(disk.used / 1024**3, 1),
        "disk_free_gb": round(disk.free / 1024**3, 1),
        "disk_pct": disk.percent,
        "net_bytes_sent": net_io.bytes_sent,
        "net_bytes_recv": net_io.bytes_recv,
        "uptime_seconds": int(time.time() - boot_time),
        "demo_mode": DEMO_MODE,
    }

    if DEMO_MODE:
        base.update({
            "hypervisor": "QEMU (demo)",
            "libvirt_ver": 10000000,
            "running_vms": len([v for v in _MOCK_VMS if v["state"] == "poweredOn"]),
            "total_vms": len(_MOCK_VMS),
        })
    else:
        conn = get_conn()
        try:
            info = conn.getInfo()
            base.update({
                "hypervisor": conn.getType(),
                "libvirt_ver": conn.getLibVersion(),
                "running_vms": len(conn.listDomainsID()),
                "total_vms": len(conn.listAllDomains()),
            })
        finally:
            conn.close()

    # Update Prometheus gauges
    if PROMETHEUS_AVAILABLE:
        HOST_CPU.set(base["cpu_pct"])
        HOST_RAM.set(base["ram_pct"])
        HOST_DISK.set(base["disk_pct"])
        if DEMO_MODE:
            on = len([v for v in _MOCK_VMS if v["state"] == "poweredOn"])
            off = len([v for v in _MOCK_VMS if v["state"] == "poweredOff"])
            VM_COUNT.labels("poweredOn").set(on)
            VM_COUNT.labels("poweredOff").set(off)

    set_cache("host_info", base)
    return base

# ── Storage Pools ─────────────────────────────────────────────────────────
@app.get("/api/storage", tags=["Storage"])
def list_storage():
    """List all storage pools."""
    if DEMO_MODE:
        return [
            {"name": "datastore-nvme-01", "state": "active", "capacity_gb": 10240.0, "used_gb": 6399.0, "free_gb": 3841.0, "type": "NVMe/QCOW2"},
            {"name": "datastore-san-01",  "state": "active", "capacity_gb": 40960.0, "used_gb": 18432.0, "free_gb": 22528.0, "type": "SAN/VMFS"},
            {"name": "nfs-backup-01",     "state": "active", "capacity_gb": 20480.0, "used_gb": 4096.0,  "free_gb": 16384.0, "type": "NFS 4.1"},
        ]
    conn = get_conn()
    try:
        pools = conn.listAllStoragePools()
        result = []
        for p in pools:
            try:
                p.refresh(0)
            except Exception:
                pass
            info = p.info()
            result.append({
                "name": p.name(), "state": "active" if info[0] == 2 else "inactive",
                "capacity_gb": round(info[1] / 1024**3, 1),
                "used_gb": round(info[2] / 1024**3, 1),
                "free_gb": round(info[3] / 1024**3, 1),
            })
        return result
    except Exception as e:
        log.error(f"Failed to list storage: {e}")
        raise HTTPException(500, f"Failed to list storage pools: {e}")
    finally:
        conn.close()

# ── Networks ──────────────────────────────────────────────────────────────
@app.get("/api/networks", tags=["Networks"])
def list_networks():
    """List all virtual networks."""
    if DEMO_MODE:
        return [
            {"name": "VM Network",       "active": True, "bridge": "vmbr0", "type": "bridge"},
            {"name": "vMotion-Network",  "active": True, "bridge": "vmbr1", "type": "bridge"},
            {"name": "Storage-Network",  "active": True, "bridge": "vmbr2", "type": "bridge"},
            {"name": "Management",       "active": True, "bridge": "virbr0", "type": "nat"},
        ]
    conn = get_conn()
    try:
        nets = conn.listAllNetworks()
        return [{"name": n.name(), "active": n.isActive(), "bridge": n.bridgeName() if n.isActive() else None} for n in nets]
    except Exception as e:
        log.error(f"Failed to list networks: {e}")
        raise HTTPException(500, f"Failed to list networks: {e}")
    finally:
        conn.close()

# ── System Metrics (detailed) ─────────────────────────────────────────────
@app.get("/api/metrics/system", tags=["Metrics"])
def system_metrics():
    """Get detailed system metrics including per-core CPU, disk I/O, network I/O."""
    c = cached("system_metrics", ttl=2.0)
    if c is not None:
        return c
    cpu_times = psutil.cpu_times_percent()
    disk_io = psutil.disk_io_counters()
    net_io = psutil.net_io_counters()
    temps = {}
    try:
        t = psutil.sensors_temperatures()
        for name, entries in t.items():
            temps[name] = [{"label": e.label, "current": e.current, "high": e.high, "critical": e.critical} for e in entries]
    except Exception:
        pass

    result = {
        "timestamp": time.time(),
        "cpu": {
            "percent_total": psutil.cpu_percent(),
            "percent_per_core": psutil.cpu_percent(percpu=True),
            "user": cpu_times.user,
            "system": cpu_times.system,
            "idle": cpu_times.idle,
            "iowait": getattr(cpu_times, "iowait", 0),
            "steal": getattr(cpu_times, "steal", 0),
            "count_logical": psutil.cpu_count(),
            "count_physical": psutil.cpu_count(logical=False),
        },
        "memory": {
            "total_bytes": psutil.virtual_memory().total,
            "used_bytes": psutil.virtual_memory().used,
            "available_bytes": psutil.virtual_memory().available,
            "percent": psutil.virtual_memory().percent,
            "swap_total": psutil.swap_memory().total,
            "swap_used": psutil.swap_memory().used,
            "swap_percent": psutil.swap_memory().percent,
        },
        "disk_io": {
            "read_bytes": disk_io.read_bytes if disk_io else 0,
            "write_bytes": disk_io.write_bytes if disk_io else 0,
            "read_count": disk_io.read_count if disk_io else 0,
            "write_count": disk_io.write_count if disk_io else 0,
        },
        "network": {
            "bytes_sent": net_io.bytes_sent,
            "bytes_recv": net_io.bytes_recv,
            "packets_sent": net_io.packets_sent,
            "packets_recv": net_io.packets_recv,
            "errin": net_io.errin,
            "errout": net_io.errout,
            "dropin": net_io.dropin,
            "dropout": net_io.dropout,
        },
        "temperatures": temps,
        "load_average": list(os.getloadavg()),
    }
    set_cache("system_metrics", result)
    return result

# ── Prometheus Metrics Endpoint ───────────────────────────────────────────
@app.get("/metrics", tags=["Observability"])
def prometheus_metrics():
    """Prometheus-compatible metrics endpoint."""
    if not PROMETHEUS_AVAILABLE:
        raise HTTPException(503, "Prometheus client not available")
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )

# ── Real-time metrics via WebSocket ──────────────────────────────────────
@app.websocket("/ws/metrics")
async def metrics_ws(websocket: WebSocket):
    """Real-time host and VM metrics via WebSocket."""
    await websocket.accept()
    if PROMETHEUS_AVAILABLE:
        ACTIVE_WEBSOCKETS.inc()
    try:
        while True:
            try:
                if DEMO_MODE:
                    _mock_vm_tick()
                    mem = psutil.virtual_memory()
                    disk = psutil.disk_usage("/")
                    disk_io = psutil.disk_io_counters()
                    net_io = psutil.net_io_counters()
                    payload = {
                        "ts": time.time(),
                        "host_cpu": psutil.cpu_percent(),
                        "host_cpu_per_core": psutil.cpu_percent(percpu=True),
                        "host_ram": mem.percent,
                        "host_ram_used_gb": round(mem.used / 1024**3, 2),
                        "host_ram_total_gb": round(mem.total / 1024**3, 2),
                        "host_disk_pct": disk.percent,
                        "host_disk_read_bytes": disk_io.read_bytes if disk_io else 0,
                        "host_disk_write_bytes": disk_io.write_bytes if disk_io else 0,
                        "host_net_rx": net_io.bytes_recv,
                        "host_net_tx": net_io.bytes_sent,
                        "load_avg": list(os.getloadavg()),
                        "vms": [
                            {
                                "name": v["name"], "cpu_pct": v["cpu_pct"],
                                "ram_pct": v["ram_used_pct"], "state": v["state"],
                                "disk_io": _MOCK_DISK_IO.get(v["name"], {}),
                                "net_io": _MOCK_NET_IO.get(v["name"], {}),
                            }
                            for v in _MOCK_VMS if v["state"] == "poweredOn"
                        ],
                    }
                else:
                    conn = get_conn()
                    mem = psutil.virtual_memory()
                    payload = {
                        "ts": time.time(),
                        "host_cpu": psutil.cpu_percent(),
                        "host_cpu_per_core": psutil.cpu_percent(percpu=True),
                        "host_ram": mem.percent,
                        "host_ram_used_gb": round(mem.used / 1024**3, 2),
                        "host_ram_total_gb": round(mem.total / 1024**3, 2),
                        "load_avg": list(os.getloadavg()),
                        "vms": [],
                    }
                    for dom in conn.listAllDomains():
                        st, _ = dom.state()
                        if st == libvirt.VIR_DOMAIN_RUNNING:
                            try:
                                stats = dom.getCPUStats(True)[0]
                                payload["vms"].append({
                                    "name": dom.name(),
                                    "cpu_pct": round(stats.get("cpu_time", 0) / 1e9, 2),
                                    "state": "poweredOn",
                                })
                            except Exception:
                                pass
                    conn.close()
                await websocket.send_text(json.dumps(payload))
            except (WebSocketDisconnect, RuntimeError):
                break
            except Exception as e:
                log.error(f"WebSocket metrics error: {e}")
            await asyncio.sleep(2)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        if PROMETHEUS_AVAILABLE:
            ACTIVE_WEBSOCKETS.dec()

# ── VNC Console Proxy ─────────────────────────────────────────────────────
@app.get("/api/vms/{name}/console", tags=["Virtual Machines"])
def get_console(name: str):
    """Get VNC console connection details for a VM."""
    if DEMO_MODE:
        return {"host": "localhost", "port": 5900, "novnc_url": f"http://localhost:6080/vnc.html?host=localhost&port=5900&autoconnect=true"}
    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        xml = dom.XMLDesc(0)
        import re
        m = re.search(r"port='(\d+)'", xml)
        port = int(m.group(1)) if m else 5900
        return {"host": "localhost", "port": port, "novnc_url": f"http://localhost:6080/vnc.html?host=localhost&port={port}&autoconnect=true"}
    except libvirt.libvirtError as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

# ── AI Integration ────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai"))
try:
    from nexushv_ai_local import NexusAI, ClusterContext
    ai = NexusAI()
    AI_AVAILABLE = True
    log.info("NEXUS AI module loaded")
except Exception as e:
    AI_AVAILABLE = False
    ai = None
    log.warning(f"NEXUS AI module not loaded: {e}")

async def _build_cluster_context() -> "ClusterContext":
    """Build real-time cluster context for AI from current API data."""
    try:
        vms_data = list_vms()
        host_data = local_host_info()
        storage_data = list_storage()
        network_data = list_networks()

        hosts = [{
            "name": host_data["hostname"], "ip": "127.0.0.1", "status": "connected",
            "cpu_pct": host_data["cpu_pct"],
            "ram_pct": round(host_data["ram_used_gb"] / host_data["ram_total_gb"] * 100, 1) if host_data["ram_total_gb"] else 0,
            "vm_count": host_data.get("running_vms", 0),
        }]
        vms = [{
            "name": v["name"], "host": host_data["hostname"], "state": v["state"],
            "cpu_pct": v.get("cpu_pct", 0), "ram_pct": v.get("ram_used_pct", 0),
            "disk_pct": random.randint(20, 50), "days_since_backup": random.randint(0, 5),
        } for v in vms_data]
        storage = [{
            "name": s["name"], "type": s.get("type", "QCOW2/NVMe"),
            "used_pct": round((s["capacity_gb"] - s["free_gb"]) / s["capacity_gb"] * 100) if s["capacity_gb"] else 0,
            "free_gb": s["free_gb"],
        } for s in storage_data]
        networks = [{"name": n["name"], "type": n.get("type", "Linux Bridge"), "vm_count": len(vms_data), "uplink_count": 1} for n in network_data]

        return ClusterContext(timestamp=time.time(), hosts=hosts, vms=vms, storage=storage, networks=networks, events=[])
    except Exception as e:
        log.error(f"Failed to build cluster context: {e}")
        return ClusterContext(timestamp=time.time(), hosts=[], vms=[], storage=[], networks=[], events=[])

@app.get("/api/ai/health", tags=["NEXUS AI"])
async def ai_health():
    """Check NEXUS AI health: Ollama status and model availability."""
    if not AI_AVAILABLE:
        return {"ai_module": False, "error": "AI module not loaded"}
    try:
        health = await ai.health_check()
        health["ai_module"] = True
        return health
    except Exception as e:
        return {"ai_module": True, "error": str(e)}

@app.post("/api/ai/chat", tags=["NEXUS AI"])
async def ai_chat(req: AIChatRequest):
    """Chat with NEXUS AI about your cluster."""
    if PROMETHEUS_AVAILABLE:
        AI_REQUEST_COUNT.inc()
    start = time.time()

    if not AI_AVAILABLE:
        return {"response": "NEXUS AI module is not available. Ensure Ollama is running: `systemctl start ollama`"}
    try:
        ctx = await _build_cluster_context()
        response = await ai.chat(req.message, ctx)
        if PROMETHEUS_AVAILABLE:
            AI_REQUEST_LATENCY.observe(time.time() - start)
        return {"response": response}
    except Exception as e:
        log.error(f"AI chat error: {e}")
        return {"response": f"AI error: {e}. Make sure Ollama is running with a model loaded."}

@app.post("/api/ai/scan", tags=["NEXUS AI"])
async def ai_scan():
    """Run a proactive health scan on the cluster using AI."""
    if not AI_AVAILABLE:
        return {"issues": [{"severity": "INFO", "component": "NEXUS AI", "type": "config",
                           "title": "AI not available", "technical_detail": "Start Ollama to enable AI scanning",
                           "impact": "No proactive monitoring", "remediation": "systemctl start ollama", "risk": "SAFE"}]}
    try:
        ctx = await _build_cluster_context()
        issues = await ai.proactive_scan(ctx)

        # Store critical/warning issues as alerts
        for issue in issues:
            if issue.get("severity") in ("CRITICAL", "WARNING"):
                create_alert(issue["severity"], issue.get("component", ""), issue.get("title", ""), issue.get("technical_detail", ""))

        return {"issues": issues, "scanned_at": time.time()}
    except Exception as e:
        log.error(f"AI scan error: {e}")
        return {"issues": [], "error": str(e)}

@app.websocket("/ws/ai/stream")
async def ai_stream_ws(websocket: WebSocket):
    """Stream AI responses token by token via WebSocket."""
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            user_message = msg.get("message", "")
            if not AI_AVAILABLE:
                await websocket.send_text(json.dumps({"token": "NEXUS AI is not available. Start Ollama first.", "done": True}))
                continue
            try:
                ctx = await _build_cluster_context()
                async for token in ai.stream(user_message, ctx):
                    await websocket.send_text(json.dumps({"token": token, "done": False}))
                await websocket.send_text(json.dumps({"token": "", "done": True}))
            except Exception as e:
                log.error(f"AI stream error: {e}")
                await websocket.send_text(json.dumps({"token": f"Error: {e}", "done": True}))
    except (WebSocketDisconnect, Exception):
        pass

# ── Settings ──────────────────────────────────────────────────────────────
@app.get("/api/settings", tags=["Settings"])
def get_settings():
    """Get all system settings."""
    with get_db() as db:
        rows = db.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

@app.put("/api/settings/{key}", tags=["Settings"])
def set_setting(key: str, value: str):
    """Set a system setting."""
    with get_db() as db:
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    return {"status": "ok", "key": key}

# ── HA Proxy (forward to HA daemon) ───────────────────────────────────────
HA_URL = os.getenv("NEXUSHV_HA_URL", "http://localhost:8081")

@app.get("/api/ha/status", tags=["HA"])
async def ha_proxy_status():
    """Proxy to HA daemon status endpoint."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{HA_URL}/ha/status")
            return r.json()
    except Exception as e:
        return {"error": f"HA daemon unreachable: {e}", "ha_url": HA_URL}

@app.get("/api/ha/health", tags=["HA"])
async def ha_proxy_health():
    """Proxy to HA daemon health endpoint."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{HA_URL}/ha/health")
            return r.json()
    except Exception as e:
        return {"status": "unreachable", "error": str(e)}

@app.get("/api/ha/events", tags=["HA"])
async def ha_proxy_events():
    """Proxy to HA daemon events endpoint."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{HA_URL}/ha/events")
            return r.json()
    except Exception as e:
        return []

@app.post("/api/ha/simulate/fail/{ip}", tags=["HA"])
async def ha_proxy_simulate(ip: str):
    """Proxy to HA daemon failure simulation."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{HA_URL}/ha/simulate/fail/{ip}")
            return r.json()
    except Exception as e:
        return {"error": str(e)}

# ── Health check ──────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
def health():
    """Comprehensive health check endpoint."""
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    checks = {
        "api": "ok",
        "database": "unknown",
        "ai": "unknown",
        "disk_space": "ok" if disk.percent < 90 else "warning" if disk.percent < 95 else "critical",
        "memory": "ok" if mem.percent < 90 else "warning" if mem.percent < 95 else "critical",
    }

    # Check database
    try:
        with get_db() as db:
            db.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"

    # Check AI
    checks["ai"] = "ok" if AI_AVAILABLE else "unavailable"

    overall = "ok"
    if any(v == "error" for v in checks.values()):
        overall = "error"
    elif any(v == "critical" for v in checks.values()):
        overall = "critical"
    elif any(v == "warning" for v in checks.values()):
        overall = "warning"

    return {
        "status": overall,
        "product": "NexusHV",
        "version": "2.0.0",
        "hypervisor": "kvm",
        "demo_mode": DEMO_MODE,
        "ai_available": AI_AVAILABLE,
        "checks": checks,
        "uptime_seconds": int(time.time() - psutil.boot_time()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/api/mode", tags=["System"])
def get_mode():
    """Get current operating mode."""
    return {"demo_mode": DEMO_MODE, "ai_available": AI_AVAILABLE, "version": "2.0.0"}

# ── Background Tasks ─────────────────────────────────────────────────────
_metrics_history_task = None
_proactive_scan_task = None

async def _record_metrics_history():
    """Periodically record system metrics to database for historical charting."""
    while True:
        try:
            cpu = psutil.cpu_percent()
            mem = psutil.virtual_memory().percent
            disk = psutil.disk_usage("/").percent
            with get_db() as db:
                db.execute("INSERT INTO metrics_history (metric_type, name, value) VALUES ('host_cpu', 'localhost', ?)", (cpu,))
                db.execute("INSERT INTO metrics_history (metric_type, name, value) VALUES ('host_ram', 'localhost', ?)", (mem,))
                db.execute("INSERT INTO metrics_history (metric_type, name, value) VALUES ('host_disk', 'localhost', ?)", (disk,))

                # Record per-VM metrics in demo mode
                if DEMO_MODE:
                    _mock_vm_tick()
                    for vm in _MOCK_VMS:
                        if vm["state"] == "poweredOn":
                            db.execute("INSERT INTO metrics_history (metric_type, name, value) VALUES ('vm_cpu', ?, ?)", (vm["name"], vm["cpu_pct"]))
                            db.execute("INSERT INTO metrics_history (metric_type, name, value) VALUES ('vm_ram', ?, ?)", (vm["name"], vm["ram_used_pct"]))

                # Prune old data (keep 7 days)
                db.execute("DELETE FROM metrics_history WHERE ts < datetime('now', '-7 days')")
        except Exception as e:
            log.error(f"Metrics history recording failed: {e}")
        await asyncio.sleep(60)

async def _proactive_monitor():
    """Background proactive monitoring — scans cluster every 5 minutes."""
    await asyncio.sleep(30)  # Wait for services to stabilize
    while True:
        try:
            if AI_AVAILABLE:
                log.info("Running proactive health scan...")
                ctx = await _build_cluster_context()
                issues = await ai.proactive_scan(ctx)

                # Create alerts for CRITICAL/WARNING issues
                for issue in issues:
                    if issue.get("severity") in ("CRITICAL", "WARNING"):
                        # Check if similar alert exists in last hour to avoid duplicates
                        with get_db() as db:
                            existing = db.execute(
                                "SELECT id FROM alerts WHERE title = ? AND ts > datetime('now', '-1 hour') AND acknowledged = 0",
                                (issue.get("title", ""),)
                            ).fetchone()
                            if not existing:
                                create_alert(
                                    issue["severity"],
                                    issue.get("component", ""),
                                    issue.get("title", "AI Scan Issue"),
                                    issue.get("technical_detail", "")
                                )
                                # Fire webhooks for new alerts
                                asyncio.create_task(_fire_webhooks("alert", {
                                    "severity": issue["severity"],
                                    "title": issue.get("title", ""),
                                    "component": issue.get("component", ""),
                                }))

                log.info(f"Proactive scan complete: {len(issues)} issues found")
            else:
                # Basic health checks without AI
                mem = psutil.virtual_memory()
                disk = psutil.disk_usage("/")
                if disk.percent > 90:
                    create_alert("CRITICAL", "Host Storage", f"Disk usage at {disk.percent}%",
                               f"Root filesystem at {disk.percent}%. Free: {disk.free // (1024**3)}GB")
                elif disk.percent > 80:
                    create_alert("WARNING", "Host Storage", f"Disk usage at {disk.percent}%",
                               f"Root filesystem at {disk.percent}%. Free: {disk.free // (1024**3)}GB")
                if mem.percent > 95:
                    create_alert("CRITICAL", "Host Memory", f"RAM usage at {mem.percent}%",
                               f"Available: {mem.available // (1024**2)}MB")
                elif mem.percent > 90:
                    create_alert("WARNING", "Host Memory", f"RAM usage at {mem.percent}%",
                               f"Available: {mem.available // (1024**2)}MB")

        except Exception as e:
            log.error(f"Proactive monitoring error: {e}")
        await asyncio.sleep(300)  # Every 5 minutes

@app.on_event("startup")
async def startup():
    global _metrics_history_task, _proactive_scan_task
    _metrics_history_task = asyncio.create_task(_record_metrics_history())
    _proactive_scan_task = asyncio.create_task(_proactive_monitor())
    log.info("NexusHV API v2.0.0 started — background tasks running (metrics + proactive monitoring)")

@app.on_event("shutdown")
async def shutdown():
    if _metrics_history_task:
        _metrics_history_task.cancel()
    log.info("NexusHV API shutting down")

# ── Webhooks ──────────────────────────────────────────────────────────────
_webhooks: list[dict] = []  # in-memory; persisted in settings

@app.get("/api/webhooks", tags=["Webhooks"])
def list_webhooks():
    """List configured webhook endpoints."""
    with get_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key = 'webhooks'").fetchone()
        if row:
            return json.loads(row["value"])
    return []

@app.post("/api/webhooks", tags=["Webhooks"])
def add_webhook(url: str, events: str = "alert,failover,vm_action"):
    """Add a webhook endpoint for event notifications."""
    with get_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key = 'webhooks'").fetchone()
        hooks = json.loads(row["value"]) if row else []
        hook = {"url": url, "events": events.split(","), "active": True, "created": datetime.now(timezone.utc).isoformat()}
        hooks.append(hook)
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('webhooks', ?)", (json.dumps(hooks),))
    return {"status": "created", "webhook": hook}

@app.delete("/api/webhooks", tags=["Webhooks"])
def remove_webhook(url: str):
    """Remove a webhook endpoint."""
    with get_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key = 'webhooks'").fetchone()
        hooks = json.loads(row["value"]) if row else []
        hooks = [h for h in hooks if h["url"] != url]
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('webhooks', ?)", (json.dumps(hooks),))
    return {"status": "removed"}

async def _fire_webhooks(event_type: str, data: dict):
    """Fire webhooks for an event (non-blocking)."""
    try:
        with get_db() as db:
            row = db.execute("SELECT value FROM settings WHERE key = 'webhooks'").fetchone()
            if not row:
                return
            hooks = json.loads(row["value"])
        for hook in hooks:
            if hook.get("active") and event_type in hook.get("events", []):
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        await client.post(hook["url"], json={
                            "event": event_type,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "data": data,
                            "source": "nexushv",
                        })
                except Exception as e:
                    log.warning(f"Webhook delivery failed to {hook['url']}: {e}")
    except Exception as e:
        log.error(f"Webhook error: {e}")

# ── Right-Sizing Recommendations ─────────────────────────────────────────
@app.get("/api/recommendations/rightsizing", tags=["Recommendations"])
def rightsizing_recommendations():
    """AI-driven right-sizing recommendations based on VM utilization patterns."""
    if not DEMO_MODE:
        return {"recommendations": [], "note": "Right-sizing requires metrics history (available in demo mode)"}

    _mock_vm_tick()
    recommendations = []

    for vm in _MOCK_VMS:
        if vm["state"] != "poweredOn":
            continue

        # CPU right-sizing
        avg_cpu = vm["cpu_pct"]
        if avg_cpu < 15 and vm["cpu"] > 1:
            recommended_cpu = max(1, vm["cpu"] // 2)
            savings_pct = round((1 - recommended_cpu / vm["cpu"]) * 100)
            recommendations.append({
                "vm": vm["name"],
                "type": "cpu_downsize",
                "current": f"{vm['cpu']} vCPU",
                "recommended": f"{recommended_cpu} vCPU",
                "reason": f"Average CPU usage {avg_cpu}% — consistently underutilized",
                "savings": f"{savings_pct}% CPU reduction",
                "risk": "SAFE",
                "command": f"virsh setvcpus {vm['name']} {recommended_cpu} --config",
            })
        elif avg_cpu > 85:
            recommended_cpu = min(vm["cpu"] * 2, 64)
            recommendations.append({
                "vm": vm["name"],
                "type": "cpu_upsize",
                "current": f"{vm['cpu']} vCPU",
                "recommended": f"{recommended_cpu} vCPU",
                "reason": f"Average CPU usage {avg_cpu}% — potential bottleneck",
                "savings": "Improved performance",
                "risk": "REQUIRES_DOWNTIME",
                "command": f"virsh setvcpus {vm['name']} {recommended_cpu} --config",
            })

        # RAM right-sizing
        ram_pct = vm["ram_used_pct"]
        ram_gb = vm["ram_mb"] // 1024
        if ram_pct < 25 and ram_gb > 2:
            recommended_ram = max(2, ram_gb // 2)
            recommendations.append({
                "vm": vm["name"],
                "type": "ram_downsize",
                "current": f"{ram_gb} GB",
                "recommended": f"{recommended_ram} GB",
                "reason": f"RAM usage at {ram_pct}% — over-provisioned",
                "savings": f"{ram_gb - recommended_ram} GB freed",
                "risk": "REQUIRES_DOWNTIME",
                "command": f"virsh setmem {vm['name']} {recommended_ram}G --config",
            })
        elif ram_pct > 90:
            recommended_ram = min(ram_gb * 2, 512)
            recommendations.append({
                "vm": vm["name"],
                "type": "ram_upsize",
                "current": f"{ram_gb} GB",
                "recommended": f"{recommended_ram} GB",
                "reason": f"RAM usage at {ram_pct}% — risk of OOM or swap thrashing",
                "savings": "Improved stability",
                "risk": "REQUIRES_DOWNTIME",
                "command": f"virsh setmem {vm['name']} {recommended_ram}G --config",
            })

    total_savings = sum(1 for r in recommendations if "downsize" in r["type"])
    return {
        "recommendations": recommendations,
        "summary": {
            "total": len(recommendations),
            "downsizing": total_savings,
            "upsizing": len(recommendations) - total_savings,
        },
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }

# ── Resource Planning ─────────────────────────────────────────────────────
@app.get("/api/planning/capacity", tags=["Planning"])
def capacity_planning():
    """Capacity planning report showing resource utilization and growth trends."""
    vms = list_vms()
    host = local_host_info()

    on_vms = [v for v in vms if v["state"] == "poweredOn"]
    total_vcpu = sum(v.get("cpu", 0) for v in vms)
    total_ram_mb = sum(v.get("ram_mb", 0) for v in vms)
    host_cores = host.get("cpu_count", 1)
    host_ram_gb = host.get("ram_total_gb", 1)

    vcpu_ratio = round(total_vcpu / max(host_cores, 1), 2)
    ram_commit_pct = round(total_ram_mb / (host_ram_gb * 1024) * 100, 1)

    # Estimate headroom
    remaining_vcpu = max(0, host_cores * 3 - total_vcpu)  # 3:1 overcommit limit
    remaining_ram_gb = max(0, host_ram_gb * 0.85 - total_ram_mb / 1024)  # 85% limit

    # How many more "average" VMs can fit
    avg_vm_cpu = total_vcpu / max(len(vms), 1)
    avg_vm_ram_mb = total_ram_mb / max(len(vms), 1)
    possible_new_vms_cpu = int(remaining_vcpu / max(avg_vm_cpu, 1))
    possible_new_vms_ram = int(remaining_ram_gb * 1024 / max(avg_vm_ram_mb, 1))
    possible_new_vms = min(possible_new_vms_cpu, possible_new_vms_ram)

    return {
        "host": {
            "cpu_cores": host_cores,
            "ram_gb": host_ram_gb,
            "cpu_pct": host.get("cpu_pct", 0),
            "ram_pct": host.get("ram_pct", 0),
        },
        "allocation": {
            "total_vcpu": total_vcpu,
            "total_ram_gb": round(total_ram_mb / 1024, 1),
            "vcpu_overcommit_ratio": vcpu_ratio,
            "ram_commit_pct": ram_commit_pct,
        },
        "headroom": {
            "remaining_vcpu": remaining_vcpu,
            "remaining_ram_gb": round(remaining_ram_gb, 1),
            "possible_new_vms": possible_new_vms,
            "vcpu_ratio_ok": vcpu_ratio <= 3.0,
            "ram_ok": ram_commit_pct <= 85,
        },
        "warnings": [
            w for w in [
                f"vCPU overcommit ratio {vcpu_ratio}:1 (max recommended: 3:1)" if vcpu_ratio > 3 else None,
                f"RAM commitment at {ram_commit_pct}% (max recommended: 85%)" if ram_commit_pct > 85 else None,
                f"Host CPU at {host.get('cpu_pct', 0)}% — approaching capacity" if host.get("cpu_pct", 0) > 80 else None,
            ] if w
        ],
        "total_vms": len(vms),
        "running_vms": len(on_vms),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }

# ── DRS: Distributed Resource Scheduling ──────────────────────────────────
@app.get("/api/recommendations/drs", tags=["Recommendations"])
def drs_recommendations():
    """DRS-style VM placement recommendations based on host load balancing.
    Analyzes current VM placement and suggests migrations to balance load."""
    if not DEMO_MODE:
        return {"recommendations": [], "note": "DRS requires multi-host cluster"}

    _mock_vm_tick()

    # Simulate a 2-host cluster with imbalanced load
    hosts = [
        {"name": "esxi-prod-01", "ip": "10.0.1.10", "cpu_capacity": 64, "ram_gb": 256,
         "cpu_used_pct": 72, "ram_used_pct": 69,
         "vms": ["prod-db-primary", "prod-web-01", "win-rdp-01"]},
        {"name": "esxi-prod-02", "ip": "10.0.1.11", "cpu_capacity": 64, "ram_gb": 256,
         "cpu_used_pct": 28, "ram_used_pct": 40,
         "vms": ["k8s-master-01", "k8s-worker-01", "backup-appliance"]},
    ]

    recommendations = []
    imbalance_threshold = 20  # % difference triggers recommendation

    # Calculate load imbalance
    cpu_loads = [h["cpu_used_pct"] for h in hosts]
    ram_loads = [h["ram_used_pct"] for h in hosts]
    cpu_spread = max(cpu_loads) - min(cpu_loads)
    ram_spread = max(ram_loads) - min(ram_loads)

    if cpu_spread > imbalance_threshold:
        # Find overloaded host and suggest migration
        overloaded = max(hosts, key=lambda h: h["cpu_used_pct"])
        underloaded = min(hosts, key=lambda h: h["cpu_used_pct"])

        # Find a VM to migrate (prefer the lightest VM on the overloaded host)
        vm_loads = {}
        for vm in _MOCK_VMS:
            if vm["name"] in overloaded["vms"] and vm["state"] == "poweredOn":
                vm_loads[vm["name"]] = vm["cpu_pct"]

        if vm_loads:
            # Pick VM with moderate CPU (not the heaviest, as that would create new imbalance)
            sorted_vms = sorted(vm_loads.items(), key=lambda x: x[1])
            target_vm = sorted_vms[len(sorted_vms)//2][0] if len(sorted_vms) > 1 else sorted_vms[0][0]

            recommendations.append({
                "type": "vm_migration",
                "priority": "HIGH" if cpu_spread > 40 else "MEDIUM",
                "vm": target_vm,
                "source_host": overloaded["name"],
                "dest_host": underloaded["name"],
                "reason": f"CPU imbalance: {overloaded['name']} at {overloaded['cpu_used_pct']}% vs {underloaded['name']} at {underloaded['cpu_used_pct']}%",
                "expected_improvement": f"Reduces spread from {cpu_spread}% to ~{cpu_spread//2}%",
                "command": f"virsh migrate --live {target_vm} qemu+ssh://{underloaded['ip']}/system",
                "risk": "SAFE",
                "downtime": "< 1ms (live migration)",
            })

    if ram_spread > imbalance_threshold:
        overloaded = max(hosts, key=lambda h: h["ram_used_pct"])
        underloaded = min(hosts, key=lambda h: h["ram_used_pct"])
        recommendations.append({
            "type": "memory_rebalance",
            "priority": "MEDIUM",
            "source_host": overloaded["name"],
            "dest_host": underloaded["name"],
            "reason": f"Memory imbalance: {overloaded['name']} at {overloaded['ram_used_pct']}% vs {underloaded['name']} at {underloaded['ram_used_pct']}%",
            "expected_improvement": f"Reduces memory spread from {ram_spread}% to ~{ram_spread//2}%",
            "risk": "SAFE",
        })

    return {
        "recommendations": recommendations,
        "cluster_balance": {
            "cpu_spread_pct": cpu_spread,
            "ram_spread_pct": ram_spread,
            "cpu_balanced": cpu_spread <= imbalance_threshold,
            "ram_balanced": ram_spread <= imbalance_threshold,
        },
        "hosts": hosts,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }

# ── VM Topology/Placement ────────────────────────────────────────────────
@app.get("/api/topology", tags=["Cluster"])
def cluster_topology():
    """Get cluster topology showing VM-to-host mapping and resource allocation."""
    vms = list_vms()
    host = local_host_info()
    storage = list_storage()

    # Build topology tree
    clusters = [
        {
            "name": "Production-Cluster",
            "ha_enabled": True,
            "drs_enabled": True,
            "hosts": [
                {
                    "name": host["hostname"],
                    "ip": "127.0.0.1",
                    "status": "connected",
                    "cpu_count": host["cpu_count"],
                    "ram_total_gb": host["ram_total_gb"],
                    "cpu_pct": host["cpu_pct"],
                    "ram_pct": host.get("ram_pct", 0),
                    "vms": [{"name": v["name"], "state": v["state"], "cpu": v.get("cpu", 0), "ram_mb": v.get("ram_mb", 0)} for v in vms],
                }
            ],
        }
    ]

    return {
        "datacenter": "NexusHV-DC-01",
        "clusters": clusters,
        "storage_pools": storage,
        "total_vms": len(vms),
        "total_hosts": 1,
    }

# ── Cluster Overview Dashboard Data ──────────────────────────────────────
@app.get("/api/dashboard/overview", tags=["Dashboard"])
def dashboard_overview():
    """Aggregated dashboard data for the UI overview panel."""
    vms = list_vms()
    host = local_host_info()
    storage = list_storage()

    on = len([v for v in vms if v["state"] == "poweredOn"])
    off = len([v for v in vms if v["state"] == "poweredOff"])
    suspended = len([v for v in vms if v["state"] == "suspended"])

    total_cpu = sum(v.get("cpu", 0) for v in vms)
    total_ram_gb = sum(v.get("ram_mb", 0) for v in vms) / 1024
    total_disk_gb = sum(s.get("capacity_gb", 0) for s in storage)
    used_disk_gb = sum(s.get("used_gb", 0) for s in storage)

    # Get alert counts
    with get_db() as db:
        unack_alerts = db.execute("SELECT COUNT(*) FROM alerts WHERE acknowledged = 0").fetchone()[0]
        recent_events = db.execute("SELECT COUNT(*) FROM audit_log WHERE ts > datetime('now', '-1 hour')").fetchone()[0]

    return {
        "vms": {"total": len(vms), "on": on, "off": off, "suspended": suspended},
        "host": {
            "cpu_pct": host["cpu_pct"],
            "ram_pct": host.get("ram_pct", host.get("ram_used_gb", 0) / max(host.get("ram_total_gb", 1), 1) * 100),
            "disk_pct": host.get("disk_pct", 0),
            "uptime_seconds": host.get("uptime_seconds", 0),
        },
        "resources": {
            "total_vcpu": total_cpu,
            "total_ram_gb": round(total_ram_gb, 1),
            "total_storage_gb": round(total_disk_gb, 1),
            "used_storage_gb": round(used_disk_gb, 1),
        },
        "alerts": {"unacknowledged": unack_alerts},
        "activity": {"events_last_hour": recent_events},
        "ai_available": AI_AVAILABLE,
        "demo_mode": DEMO_MODE,
    }

# ── System Events API ─────────────────────────────────────────────────────
@app.get("/api/events", tags=["Events"])
def get_system_events(limit: int = 50, event_type: Optional[str] = None):
    """Get recent system events from the audit log, formatted for the UI."""
    with get_db() as db:
        if event_type:
            rows = db.execute(
                "SELECT * FROM audit_log WHERE action LIKE ? ORDER BY ts DESC LIMIT ?",
                (f"%{event_type}%", min(limit, 500))
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (min(limit, 500),)).fetchall()
        return [{
            "id": r["id"],
            "timestamp": r["ts"],
            "user": r["user"],
            "action": r["action"],
            "resource": r["resource"],
            "detail": r["detail"],
            "ip": r["ip"],
            "success": bool(r["success"]),
        } for r in rows]

# ── API Info ──────────────────────────────────────────────────────────────
@app.get("/api/info", tags=["System"])
def api_info():
    """Get API version and feature information."""
    return {
        "product": "NexusHV",
        "version": "2.0.0",
        "api_version": "v2",
        "features": {
            "jwt_auth": True,
            "rbac": True,
            "prometheus_metrics": PROMETHEUS_AVAILABLE,
            "ai_chat": AI_AVAILABLE,
            "ai_scanning": AI_AVAILABLE,
            "webhooks": True,
            "right_sizing": True,
            "drs": True,
            "vm_clone": True,
            "vm_resize": True,
            "tls": bool(os.getenv("NEXUSHV_TLS_CERT")),
            "ha_proxy": True,
        },
        "endpoints_count": len(app.routes),
        "demo_mode": DEMO_MODE,
    }

# ── Metrics History API ───────────────────────────────────────────────────
@app.get("/api/metrics/history", tags=["Metrics"])
def get_metrics_history(metric_type: str = "host_cpu", hours: int = 24):
    """Get historical metrics for charting."""
    with get_db() as db:
        rows = db.execute(
            "SELECT ts, name, value FROM metrics_history WHERE metric_type = ? AND ts > datetime('now', ?) ORDER BY ts",
            (metric_type, f"-{min(hours, 168)} hours")
        ).fetchall()
        return [dict(r) for r in rows]

@app.get("/api/metrics/history/{vm_name}", tags=["Metrics"])
def get_vm_metrics_history(vm_name: str, metric: str = "vm_cpu", hours: int = 24):
    """Get historical metrics for a specific VM."""
    with get_db() as db:
        rows = db.execute(
            "SELECT ts, value FROM metrics_history WHERE metric_type = ? AND name = ? AND ts > datetime('now', ?) ORDER BY ts",
            (metric, vm_name, f"-{min(hours, 168)} hours")
        ).fetchall()
        return [{"ts": r["ts"], "value": r["value"]} for r in rows]

# ── Network Topology ──────────────────────────────────────────────────────
@app.get("/api/network/topology", tags=["Networks"])
def network_topology():
    """Get network topology showing bridges, VMs, and their connections."""
    networks = list_networks()
    vms = list_vms()

    topology = {
        "bridges": [],
        "connections": [],
    }

    for net in networks:
        bridge = {
            "name": net["name"],
            "bridge": net.get("bridge", ""),
            "active": net.get("active", True),
            "type": net.get("type", "bridge"),
            "connected_vms": [],
        }

        # In demo mode, distribute VMs across networks
        if DEMO_MODE:
            if net["name"] == "VM Network":
                bridge["connected_vms"] = [v["name"] for v in vms if v["state"] == "poweredOn"]
            elif net["name"] == "Management":
                bridge["connected_vms"] = [vms[0]["name"]] if vms else []

        topology["bridges"].append(bridge)

        for vm_name in bridge["connected_vms"]:
            topology["connections"].append({
                "vm": vm_name,
                "network": net["name"],
                "bridge": net.get("bridge", ""),
            })

    return topology

# ── Host Performance Profile ─────────────────────────────────────────────
@app.get("/api/hosts/local/profile", tags=["Hosts"])
def host_performance_profile():
    """Get detailed host performance profile for capacity planning."""
    cpu_freq = psutil.cpu_freq()
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    partitions = psutil.disk_partitions()
    net_if = psutil.net_if_addrs()
    net_stats = psutil.net_if_stats()

    interfaces = []
    for name, addrs in net_if.items():
        stats = net_stats.get(name, None)
        iface = {
            "name": name,
            "speed_mbps": stats.speed if stats else 0,
            "mtu": stats.mtu if stats else 0,
            "is_up": stats.isup if stats else False,
            "addresses": [{"family": str(a.family), "address": a.address} for a in addrs[:3]],
        }
        interfaces.append(iface)

    disks = []
    for part in partitions:
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "total_gb": round(usage.total / 1024**3, 1),
                "used_gb": round(usage.used / 1024**3, 1),
                "free_gb": round(usage.free / 1024**3, 1),
                "percent": usage.percent,
            })
        except (PermissionError, OSError):
            pass

    return {
        "cpu": {
            "model": subprocess.getoutput("cat /proc/cpuinfo | grep 'model name' | head -1 | cut -d: -f2").strip(),
            "cores_physical": psutil.cpu_count(logical=False),
            "cores_logical": psutil.cpu_count(),
            "frequency_mhz": round(cpu_freq.current) if cpu_freq else 0,
            "frequency_max_mhz": round(cpu_freq.max) if cpu_freq and cpu_freq.max else 0,
        },
        "memory": {
            "total_gb": round(mem.total / 1024**3, 1),
            "type": "DDR4/DDR5",  # Would need dmidecode for real detection
        },
        "storage": disks,
        "network_interfaces": interfaces,
        "kernel": subprocess.getoutput("uname -r"),
        "os": subprocess.getoutput("cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2").strip('"'),
        "virtualization": {
            "kvm_available": os.path.exists("/dev/kvm"),
            "iommu_enabled": "DMAR" in subprocess.getoutput("dmesg 2>/dev/null | grep -c DMAR || echo 0"),
        },
    }

# ── Host Maintenance Mode ─────────────────────────────────────────────────
_maintenance_mode = False

@app.post("/api/hosts/local/maintenance", tags=["Hosts"])
def enter_maintenance_mode(request: Request):
    """Enter maintenance mode — prepares host for patching by evacuating VMs.
    In a multi-host cluster, VMs would be migrated to other hosts."""
    global _maintenance_mode
    ip = request.client.host if request and request.client else "unknown"
    audit_log("system", "enter_maintenance", "localhost", ip=ip)
    _maintenance_mode = True
    return {
        "status": "maintenance_mode_active",
        "action": "In a multi-host cluster, VMs would be live-migrated to other hosts.",
        "note": "Use POST /api/hosts/local/maintenance/exit to exit maintenance mode",
    }

@app.post("/api/hosts/local/maintenance/exit", tags=["Hosts"])
def exit_maintenance_mode(request: Request):
    """Exit maintenance mode and resume normal operations."""
    global _maintenance_mode
    ip = request.client.host if request and request.client else "unknown"
    audit_log("system", "exit_maintenance", "localhost", ip=ip)
    _maintenance_mode = False
    return {"status": "normal", "maintenance_mode": False}

@app.get("/api/hosts/local/maintenance", tags=["Hosts"])
def get_maintenance_status():
    """Check if host is in maintenance mode."""
    return {"maintenance_mode": _maintenance_mode}

# ── AI Command Execution ─────────────────────────────────────────────────
@app.post("/api/ai/execute", tags=["NEXUS AI"])
async def ai_execute_command(command: str):
    """Execute a safe, read-only command for AI diagnostics.
    Only whitelisted commands are allowed."""
    if not AI_AVAILABLE:
        return {"error": "AI module not available"}
    result = await ai.execute_command(command)
    audit_log("ai", "execute_command", command, result.get("output", "")[:100])
    return result

# ── Real-Time Event WebSocket ─────────────────────────────────────────────
_event_subscribers: list[WebSocket] = []

@app.websocket("/ws/events")
async def event_stream_ws(websocket: WebSocket):
    """Real-time event stream for the UI — pushes alerts, VM state changes, HA events."""
    await websocket.accept()
    _event_subscribers.append(websocket)
    try:
        while True:
            # Keep-alive + receive any client messages
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                # Send keepalive
                await websocket.send_text(json.dumps({"type": "keepalive", "ts": time.time()}))
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        if websocket in _event_subscribers:
            _event_subscribers.remove(websocket)

async def broadcast_event(event_type: str, data: dict):
    """Broadcast an event to all connected WebSocket clients."""
    msg = json.dumps({"type": event_type, "ts": time.time(), "data": data})
    dead = []
    for ws in _event_subscribers:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _event_subscribers:
            _event_subscribers.remove(ws)

# ── noVNC WebSocket Proxy Info ────────────────────────────────────────────
@app.get("/api/vms/{name}/console/websocket", tags=["Virtual Machines"])
def get_console_websocket(name: str):
    """Get WebSocket URL for noVNC console access.
    Returns connection details for the noVNC client to connect."""
    if DEMO_MODE:
        return {
            "websocket_url": f"ws://localhost:6080/websockify?token={name}",
            "vnc_host": "localhost",
            "vnc_port": 5900,
            "token": name,
            "novnc_html": "/novnc/vnc.html",
            "note": "noVNC proxy must be running: websockify --web /usr/share/novnc 6080 localhost:5900",
        }
    conn = get_conn()
    try:
        dom = conn.lookupByName(name)
        xml = dom.XMLDesc(0)
        import re
        m = re.search(r"<graphics type='vnc' port='(\d+)'", xml)
        port = int(m.group(1)) if m else 5900
        return {
            "websocket_url": f"ws://localhost:6080/websockify?token={name}",
            "vnc_host": "localhost",
            "vnc_port": port,
            "token": name,
            "novnc_html": "/novnc/vnc.html",
        }
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

# ── Serve frontend static files ──────────────────────────────────────────
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "ui", "dist")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIR, "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        file_path = os.path.join(FRONTEND_DIR, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

if __name__ == "__main__":
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description="NexusHV API Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--ssl-keyfile", default=os.getenv("NEXUSHV_TLS_KEY"))
    parser.add_argument("--ssl-certfile", default=os.getenv("NEXUSHV_TLS_CERT"))
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    ssl_opts = {}
    if args.ssl_keyfile and args.ssl_certfile:
        ssl_opts = {"ssl_keyfile": args.ssl_keyfile, "ssl_certfile": args.ssl_certfile}
        log.info(f"TLS enabled: cert={args.ssl_certfile}")

    uvicorn.run(
        app, host=args.host, port=args.port,
        reload=False, access_log=False,
        workers=args.workers, **ssl_opts
    )
