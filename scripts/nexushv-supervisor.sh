#!/usr/bin/env bash
# NexusHV Process Supervisor
# Manages API, HA, and Ollama services with auto-restart
# Run: ./nexushv-supervisor.sh start|stop|status|restart

set -euo pipefail

NEXUSHV_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$NEXUSHV_DIR/venv"
LOG_DIR="$NEXUSHV_DIR/logs"
PID_DIR="$NEXUSHV_DIR/run"

mkdir -p "$LOG_DIR" "$PID_DIR"

API_PORT=${NEXUSHV_API_PORT:-8080}
HA_PORT=${NEXUSHV_HA_PORT:-8081}
API_PID="$PID_DIR/api.pid"
HA_PID="$PID_DIR/ha.pid"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [SUPERVISOR] $*" | tee -a "$LOG_DIR/supervisor.log"
}

is_running() {
    local pid_file=$1
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        rm -f "$pid_file"
    fi
    return 1
}

start_api() {
    if is_running "$API_PID"; then
        log "API already running (PID: $(cat $API_PID))"
        return
    fi
    log "Starting NexusHV API on port $API_PORT..."
    source "$VENV/bin/activate"
    cd "$NEXUSHV_DIR"
    nohup python3 -u api/nexushv_api.py >> "$LOG_DIR/api-stdout.log" 2>&1 &
    echo $! > "$API_PID"
    log "API started (PID: $!)"
}

start_ha() {
    if is_running "$HA_PID"; then
        log "HA already running (PID: $(cat $HA_PID))"
        return
    fi
    log "Starting NexusHV HA on port $HA_PORT..."
    source "$VENV/bin/activate"
    cd "$NEXUSHV_DIR"
    nohup python3 -u ha/nexushv_ha.py --standalone --port "$HA_PORT" >> "$LOG_DIR/ha-stdout.log" 2>&1 &
    echo $! > "$HA_PID"
    log "HA started (PID: $!)"
}

stop_service() {
    local pid_file=$1
    local name=$2
    if is_running "$pid_file"; then
        local pid=$(cat "$pid_file")
        log "Stopping $name (PID: $pid)..."
        kill "$pid" 2>/dev/null || true
        # Wait up to 10 seconds for graceful shutdown
        for i in $(seq 1 10); do
            if ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
            sleep 1
        done
        # Force kill if still running
        if kill -0 "$pid" 2>/dev/null; then
            log "Force killing $name (PID: $pid)"
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$pid_file"
        log "$name stopped"
    else
        log "$name not running"
    fi
}

check_health() {
    local port=$1
    local name=$2
    if curl -sf "http://localhost:$port/health" > /dev/null 2>&1; then
        echo "$name: HEALTHY (port $port)"
    else
        echo "$name: DOWN (port $port)"
    fi
}

case "${1:-}" in
    start)
        log "=== Starting NexusHV services ==="
        start_api
        sleep 2
        start_ha
        log "All services started"
        ;;
    stop)
        log "=== Stopping NexusHV services ==="
        stop_service "$HA_PID" "HA"
        stop_service "$API_PID" "API"
        log "All services stopped"
        ;;
    restart)
        "$0" stop
        sleep 2
        "$0" start
        ;;
    status)
        echo "=== NexusHV Service Status ==="
        if is_running "$API_PID"; then
            echo "API: RUNNING (PID: $(cat $API_PID))"
        else
            echo "API: STOPPED"
        fi
        if is_running "$HA_PID"; then
            echo "HA:  RUNNING (PID: $(cat $HA_PID))"
        else
            echo "HA:  STOPPED"
        fi
        echo ""
        check_health "$API_PORT" "API Health"
        check_health "$HA_PORT" "HA Health"
        echo ""
        echo "Ollama: $(systemctl is-active ollama 2>/dev/null || echo 'unknown')"
        ;;
    watch)
        # Watchdog mode — auto-restart crashed services
        log "Starting watchdog mode..."
        "$0" start
        while true; do
            sleep 15
            if ! is_running "$API_PID"; then
                log "API crashed — restarting..."
                start_api
            fi
            if ! is_running "$HA_PID"; then
                log "HA crashed — restarting..."
                start_ha
            fi
        done
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|watch}"
        exit 1
        ;;
esac
