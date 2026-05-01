#!/usr/bin/env bash
# wardrive.sh — main launcher for the warDrive passive capture system.
# Spawns independent collectors: Kismet (WiFi), rtl_433 (SDR), ESP32 BLE.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/config/wardrive.conf"

# ── Load configuration ─────────────────────────────────────────────────────────
# shellcheck source=config/wardrive.conf
source "${CONFIG}"

# ── Session setup ──────────────────────────────────────────────────────────────
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SESSION_NAME="${TIMESTAMP}_${SESSION_LABEL:-wardrive}"
SESSION_DIR="${SCRIPT_DIR}/capture/raw/${SESSION_NAME}"
LOG_DIR="${SCRIPT_DIR}/capture/logs"

mkdir -p "${SESSION_DIR}"/{wifi,sdr,bt} "${LOG_DIR}"

SESSION_LOG="${LOG_DIR}/${SESSION_NAME}.log"
exec > >(tee -a "${SESSION_LOG}") 2>&1

echo "[wardrive] Session: ${SESSION_NAME}"
echo "[wardrive] Dir:     ${SESSION_DIR}"

# ── Child-PID tracking ─────────────────────────────────────────────────────────
CHILD_PIDS=()

cleanup() {
    echo "[wardrive] Shutting down collectors…"
    for pid in "${CHILD_PIDS[@]:-}"; do
        kill "${pid}" 2>/dev/null || true
    done
    finalize_manifest
    echo "[wardrive] Session closed: ${SESSION_NAME}"
}
trap cleanup EXIT INT TERM

# ── Manifest helpers ───────────────────────────────────────────────────────────
MANIFEST="${SESSION_DIR}/manifest.json"

init_manifest() {
    cat > "${MANIFEST}" <<EOF
{
  "schema_version": 1,
  "session_id": "${SESSION_NAME}",
  "started_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "hostname": "$(hostname)",
  "collectors": {
    "wifi":  false,
    "sdr":   false,
    "esp32": false
  }
}
EOF
}

# Update a single boolean field in the manifest using python (no jq dependency).
update_manifest() {
    local key="$1" value="$2"
    python3 - "${MANIFEST}" "${key}" "${value}" <<'PYEOF'
import sys, json
path, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path) as f: m = json.load(f)
m["collectors"][key] = (val == "true")
with open(path, "w") as f: json.dump(m, f, indent=2)
PYEOF
}

finalize_manifest() {
    python3 - "${MANIFEST}" <<'PYEOF'
import sys, json
from datetime import datetime, timezone
path = sys.argv[1]
with open(path) as f: m = json.load(f)
m["ended_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
with open(path, "w") as f: json.dump(m, f, indent=2)
PYEOF
}

# ── WiFi collector (Kismet) ────────────────────────────────────────────────────
start_wifi_collector() {
    if [[ "${ENABLE_WIFI:-false}" != "true" ]]; then
        echo "[wifi] Disabled in config — skipping"
        return
    fi

    local wifi_dir="${SESSION_DIR}/wifi"
    echo "[wifi] Starting Kismet on ${WIFI_INTERFACE}"

    systemd-inhibit --what=sleep --who=wardrive --why="wifi capture" \
        kismet \
            --no-ncurses \
            --override=wigle_file=false \
            -c "${WIFI_INTERFACE}" \
            --log-prefix="${wifi_dir}/${SESSION_NAME}" \
            --log-types kismet,pcapng \
        &
    CHILD_PIDS+=($!)
    update_manifest wifi true
    echo "[wifi] PID ${CHILD_PIDS[-1]}"
}

# ── SDR collector (rtl_433) ────────────────────────────────────────────────────
start_sdr_collector() {
    if [[ "${ENABLE_SDR:-false}" != "true" ]]; then
        echo "[sdr] Disabled in config — skipping"
        return
    fi

    local sdr_out="${SESSION_DIR}/sdr/${SESSION_NAME}_rtl433.ndjson"
    echo "[sdr] Starting rtl_433 on ${SDR_FREQUENCY_MHZ} MHz"

    rtl_433 \
        -f "${SDR_FREQUENCY_MHZ}M" \
        -F "json:${sdr_out}" \
        -M utc \
        &
    CHILD_PIDS+=($!)
    update_manifest sdr true
    echo "[sdr] PID ${CHILD_PIDS[-1]}"
}

# ── ESP32 BLE collector ────────────────────────────────────────────────────────
start_esp32_collector() {
    if [[ "${ENABLE_ESP32:-false}" != "true" ]]; then
        echo "[esp32] Disabled in config — skipping"
        return
    fi

    # ── 1. Locate the serial port ──────────────────────────────────────────────
    local port="${ESP32_DEVICE:-}"

    if [[ -z "${port}" ]]; then
        # Auto-detect: prefer ttyUSB0, fall back to ttyACM0
        for candidate in /dev/ttyUSB0 /dev/ttyUSB1 /dev/ttyACM0 /dev/ttyACM1; do
            if [[ -e "${candidate}" ]]; then
                port="${candidate}"
                break
            fi
        done
    fi

    if [[ -z "${port}" ]] || [[ ! -e "${port}" ]]; then
        echo "[esp32] ERROR: Serial port not found (tried ${ESP32_DEVICE:-auto}). Is the ESP32 plugged in?"
        echo "[esp32] Collector SKIPPED — continuing without BLE data."
        return
    fi

    echo "[esp32] Found ESP32 on ${port}"

    # ── 2. Configure baud rate ─────────────────────────────────────────────────
    local baud="${ESP32_BAUD:-921600}"
    stty -F "${port}" "${baud}" raw -echo 2>/dev/null || {
        echo "[esp32] WARNING: Could not configure ${port} — check dialout group membership."
    }

    # ── 3. Verify device is alive (wait up to 5 s for a JSON line) ─────────────
    echo "[esp32] Verifying device output…"
    local verified=false
    local deadline=$(( $(date +%s) + 5 ))
    while [[ $(date +%s) -lt "${deadline}" ]]; do
        local line
        line=$(timeout 1 head -n 1 "${port}" 2>/dev/null || true)
        if [[ "${line}" == "{"* ]]; then
            verified=true
            break
        fi
    done

    if [[ "${verified}" != "true" ]]; then
        echo "[esp32] WARNING: Did not see valid NDJSON within 5 s. Device may still be booting."
        echo "[esp32] Starting collector anyway — output will be checked during enrichment."
    else
        echo "[esp32] Device verified ✓"
    fi

    # ── 4. Spawn the reader ────────────────────────────────────────────────────
    # The Python reader:
    #   • Reads raw lines from the serial port
    #   • Skips comment lines (start with '#')
    #   • Injects a wall-clock UTC timestamp as "ts" into each JSON object
    #   • Writes to the session NDJSON file
    local bt_out="${SESSION_DIR}/bt/esp32_ble.ndjson"
    local reader_script="${SCRIPT_DIR}/processing/esp32_reader.py"

    python3 "${reader_script}" \
        --port "${port}" \
        --baud "${baud}" \
        --output "${bt_out}" \
        &
    CHILD_PIDS+=($!)
    update_manifest esp32 true
    echo "[esp32] PID ${CHILD_PIDS[-1]} → ${bt_out}"
}

# ── Main ───────────────────────────────────────────────────────────────────────
init_manifest

echo "[wardrive] Starting collectors…"
start_wifi_collector
start_sdr_collector
start_esp32_collector

if [[ ${#CHILD_PIDS[@]} -eq 0 ]]; then
    echo "[wardrive] No collectors started — check config. Exiting."
    exit 1
fi

echo "[wardrive] All collectors running. Ctrl-C to stop."

# Keep the script alive; the trap handles cleanup on exit.
# systemd-inhibit is used per-collector above for the ones that need it.
wait
