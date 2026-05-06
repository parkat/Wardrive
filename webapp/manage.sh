#!/usr/bin/env bash
# warDrive webapp process manager

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${SCRIPT_DIR}/wardrive-webapp.pid"
LOG_FILE="${SCRIPT_DIR}/wardrive-webapp.log"

usage() {
    echo "Usage: $0 {start|stop|status|restart}"
    exit 1
}

start() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "[✓] warDrive webapp already running (PID: $PID)"
            exit 0
        fi
    fi

    echo "[→] Starting warDrive webapp..."
    cd "$SCRIPT_DIR"
    nohup python3 main.py > ${LOG_FILE} 2>&1 &
    PID=$!
    echo $PID > "$PID_FILE"

    sleep 2
    if kill -0 "$PID" 2>/dev/null; then
        echo "[✓] Started successfully (PID: $PID)"
        echo "    Open: http://127.0.0.1:8000"
        echo "    Logs: tail -f ${LOG_FILE}"
    else
        echo "[✗] Failed to start. Check logs:"
        cat ${LOG_FILE}
        exit 1
    fi
}

stop() {
    if [ ! -f "$PID_FILE" ]; then
        echo "[!] No PID file found. Searching for running processes..."
        PIDS=$(pgrep -f "python3 main.py" | grep -v grep || true)
        if [ -z "$PIDS" ]; then
            echo "[✓] No running webapp processes"
            exit 0
        fi
        echo "[→] Killing processes: $PIDS"
        echo "$PIDS" | xargs kill 2>/dev/null || true
        rm -f "$PID_FILE"
        echo "[✓] Stopped"
        exit 0
    fi

    PID=$(cat "$PID_FILE")
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "[✓] Process not running (stale PID file)"
        rm -f "$PID_FILE"
        exit 0
    fi

    echo "[→] Stopping warDrive webapp (PID: $PID)..."
    kill "$PID" 2>/dev/null || true
    rm -f "$PID_FILE"

    # Wait up to 5 seconds for graceful shutdown
    for i in {1..5}; do
        if ! kill -0 "$PID" 2>/dev/null; then
            echo "[✓] Stopped"
            exit 0
        fi
        sleep 1
    done

    # Force kill if needed
    echo "[→] Force killing..."
    kill -9 "$PID" 2>/dev/null || true
    echo "[✓] Stopped (forced)"
}

status() {
    if [ ! -f "$PID_FILE" ]; then
        echo "[✗] Not running (no PID file)"
        exit 1
    fi

    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "[✓] Running (PID: $PID)"
        echo "    URL: http://127.0.0.1:8000"
        echo "    Logs: tail -f ${LOG_FILE}"
        exit 0
    else
        echo "[✗] Not running (stale PID: $PID)"
        rm -f "$PID_FILE"
        exit 1
    fi
}

case "${1:-}" in
    start) start ;;
    stop) stop ;;
    status) status ;;
    restart) stop; sleep 1; start ;;
    *) usage ;;
esac
