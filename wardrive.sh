#!/usr/bin/env bash
# wardrive.sh — main launcher for the warDrive passive capture system.
# Spawns independent collectors: Kismet (WiFi), rtl_433 (SDR), ESP32 BLE, GPS.
#
# Usage:
#   sudo ./wardrive.sh
#
# All collectors are passive / receive-only. No packet injection, no
# transmissions. Stop the session cleanly with Ctrl-C.
#
# Each collector runs under a supervisor that restarts it on crash, with
# exponential backoff — so a USB blip mid-drive recovers automatically.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/config/wardrive.conf"

# ── Load configuration ─────────────────────────────────────────────────────────
if [[ ! -f "${CONFIG}" ]]; then
    echo "[wardrive] ERROR: config file not found: ${CONFIG}"
    echo "[wardrive] Copy config/wardrive.conf.example to config/wardrive.conf and edit it."
    exit 1
fi
# shellcheck source=config/wardrive.conf
source "${CONFIG}"

# Supervisor tunables (override in wardrive.conf if desired)
RESTART_MAX="${RESTART_MAX:-10}"
RESTART_BACKOFF_INITIAL="${RESTART_BACKOFF_INITIAL:-2}"
RESTART_BACKOFF_MAX="${RESTART_BACKOFF_MAX:-60}"
RESTART_RESET_AFTER="${RESTART_RESET_AFTER:-300}"

# GPS defaults — override in wardrive.conf
GPS_DEVICE="${GPS_DEVICE:-/dev/ttyACM0}"
GPS_WAIT_FIX="${GPS_WAIT_FIX:-60}"
GPS_MIN_SATS="${GPS_MIN_SATS:-4}"
ENABLE_GPS="${ENABLE_GPS:-false}"

# ── Session setup ──────────────────────────────────────────────────────────────
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SESSION_NAME="${TIMESTAMP}_${SESSION_LABEL:-wardrive}"
SESSION_DIR="${SCRIPT_DIR}/capture/raw/${SESSION_NAME}"
LOG_DIR="${SCRIPT_DIR}/capture/logs"

mkdir -p "${SESSION_DIR}"/{wifi,sdr,bt,gps} "${LOG_DIR}"

SESSION_LOG="${LOG_DIR}/${SESSION_NAME}.log"
exec > >(tee -a "${SESSION_LOG}") 2>&1

echo "[wardrive] Session: ${SESSION_NAME}"
echo "[wardrive] Dir:     ${SESSION_DIR}"

# ── PID tracking ───────────────────────────────────────────────────────────────
CHILD_PIDS=()
INHIBITOR_PID=""
CLEANUP_DONE=0

cleanup() {
    [[ "${CLEANUP_DONE}" -eq 1 ]] && return
    CLEANUP_DONE=1

    if [[ -n "${INHIBITOR_PID:-}" ]] && kill -0 "${INHIBITOR_PID}" 2>/dev/null; then
        kill "${INHIBITOR_PID}" 2>/dev/null || true
        echo "[wardrive] Sleep inhibitor released — normal lid/sleep behavior restored"
    fi

    echo "[wardrive] Shutting down collectors…"
    if [[ ${#CHILD_PIDS[@]} -gt 0 ]]; then
        for pid in "${CHILD_PIDS[@]}"; do
            kill -TERM "${pid}" 2>/dev/null || true
        done
        sleep 2
        for pid in "${CHILD_PIDS[@]}"; do
            kill -KILL "${pid}" 2>/dev/null || true
        done
    fi

    # Restore WiFi interface — airmon-ng handles the MT7612U teardown correctly
    local mon_iface
    mon_iface="$(iw dev 2>/dev/null | awk '/Interface/ {print $2}' \
        | grep -E "mon$" | head -1 || true)"
    if [[ -n "${mon_iface}" ]]; then
        echo "[wardrive] Restoring ${mon_iface} to managed mode…"
        airmon-ng stop "${mon_iface}" >/dev/null 2>&1 || true
        sleep 1
        systemctl start NetworkManager 2>/dev/null || true
    fi

    rm -f "/tmp/wardrive.pid"
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
  "gps_device": "${GPS_DEVICE}",
  "collectors": {
    "wifi":  false,
    "sdr":   false,
    "esp32": false,
    "gps":   false
  }
}
EOF
}

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
    [[ -f "${MANIFEST}" ]] || return
    python3 - "${MANIFEST}" <<'PYEOF'
import sys, json
from datetime import datetime, timezone
path = sys.argv[1]
with open(path) as f: m = json.load(f)
m["ended_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
with open(path, "w") as f: json.dump(m, f, indent=2)
PYEOF
}

# ── Power management ───────────────────────────────────────────────────────────
inhibit_sleep() {
    if [[ "${KEEP_AWAKE:-true}" != "true" ]]; then
        echo "[wardrive] KEEP_AWAKE=false — laptop will sleep normally"
        return
    fi
    if ! command -v systemd-inhibit &>/dev/null; then
        echo "[wardrive] WARNING: systemd-inhibit not found — lid-close may suspend the session"
        return
    fi
    systemd-inhibit \
        --what="sleep:idle:handle-lid-switch:handle-suspend-key" \
        --who="wardrive.sh" \
        --why="Active wardriving capture session" \
        --mode=block \
        sleep infinity &
    INHIBITOR_PID=$!
    if kill -0 "${INHIBITOR_PID}" 2>/dev/null; then
        echo "[wardrive] Sleep/lid-close inhibited (pid ${INHIBITOR_PID})"
        echo "[wardrive] Laptop stays awake with lid closed until session ends"
    else
        echo "[wardrive] WARNING: Failed to start sleep inhibitor — lid-close may suspend"
        INHIBITOR_PID=""
    fi
}

# ══════════════════════════════════════════════════════════════════════════════
# Pre-flight: evict processes blocking our collectors
# ══════════════════════════════════════════════════════════════════════════════

_kill_process() {
    local pid="$1" name="$2" reason="$3"
    echo "[preflight]   Killing ${name} (pid ${pid}): ${reason}"
    kill -TERM "${pid}" 2>/dev/null || true
    local i=0
    while kill -0 "${pid}" 2>/dev/null && (( i < 10 )); do
        sleep 0.5; (( i++ )) || true
    done
    kill -KILL "${pid}" 2>/dev/null || true
}

_prompt_kill() {
    local pid="$1" name="$2" device="$3"
    echo "[preflight] WARN: '${name}' (pid ${pid}) is holding ${device}"
    read -r -p "[preflight]       Kill it? [y/N] " answer
    if [[ "${answer}" =~ ^[Yy]$ ]]; then
        _kill_process "${pid}" "${name}" "user confirmed"
        return 0
    fi
    echo "[preflight]       Skipping — collector may fail to open device."
    return 1
}

preflight_rtl_sdr() {
    [[ "${ENABLE_SDR:-false}" != "true" ]] && return 0
    echo "[preflight] Checking RTL-SDR…"
    if ! lsusb | grep -qE "RTL283[28]|0bda:283[28]"; then
        echo "[preflight]   RTL-SDR not detected via lsusb — SDR collector will be skipped"
        return 0
    fi
    local known=("readsb" "dump1090" "dump1090-fa" "dump1090-mutability"
                 "tar1090" "rtl_tcp" "rtl_test" "rtl_433" "rtl_fm"
                 "gqrx" "sdrangel")
    for proc in "${known[@]}"; do
        local pid
        pid="$(pgrep -x "${proc}" 2>/dev/null | head -1 || true)"
        [[ -n "${pid}" ]] && _kill_process "${pid}" "${proc}" "known RTL-SDR consumer"
    done
    local holder
    holder="$(lsof 2>/dev/null | awk '/librtlsdr/ {print $2":"$1}' | sort -u | head -1 || true)"
    if [[ -n "${holder}" ]]; then
        local hpid hname
        hpid="${holder%%:*}"; hname="${holder##*:}"
        kill -0 "${hpid}" 2>/dev/null && _prompt_kill "${hpid}" "${hname}" "RTL-SDR" || true
    fi
    sleep 1
    echo "[preflight]   RTL-SDR: clear"
}

preflight_wifi() {
    [[ "${ENABLE_WIFI:-false}" != "true" ]] && return 0
    echo "[preflight] Checking WiFi (${WIFI_INTERFACE})…"

    # Remove any stale monitor interface from a previous crashed session
    local stale
    stale="$(iw dev 2>/dev/null | awk '/Interface/ {print $2}' \
        | grep -E "mon$" | head -1 || true)"
    if [[ -n "${stale}" ]]; then
        echo "[preflight]   Removing stale monitor interface: ${stale}"
        airmon-ng stop "${stale}" >/dev/null 2>&1 || true
        sleep 1
    fi

    if ! ip link show "${WIFI_INTERFACE}" &>/dev/null; then
        echo "[preflight]   WARNING: ${WIFI_INTERFACE} not found — WiFi collector will be skipped"
        iw dev 2>/dev/null | awk '/Interface/ {print "    " $2}' || true
        return 1
    fi

    # airmon-ng check kill handles wpa_supplicant, NetworkManager, and any
    # other process interfering with the wireless interface in one shot.
    echo "[preflight]   Running airmon-ng check kill…"
    airmon-ng check kill >/dev/null 2>&1 || true
    sleep 1

    echo "[preflight]   WiFi: clear"
}

preflight_gps() {
    [[ "${ENABLE_GPS}" != "true" ]] && return 0
    echo "[preflight] Checking GPS device (${GPS_DEVICE})…"
    if [[ ! -e "${GPS_DEVICE}" ]]; then
        echo "[preflight]   WARNING: ${GPS_DEVICE} not found — GPS collector will be skipped"
        return 1
    fi
    if ! systemctl is-active --quiet gpsd 2>/dev/null; then
        echo "[preflight]   gpsd not running — starting it…"
        systemctl start gpsd 2>/dev/null || {
            echo "[preflight]   ERROR: could not start gpsd"
            return 1
        }
        sleep 2
    fi
    echo "[preflight]   GPS: clear"
}

preflight_esp32() {
    [[ "${ENABLE_ESP32:-false}" != "true" ]] && return 0
    echo "[preflight] Checking ESP32 serial port…"
    local port="${ESP32_DEVICE:-}"
    if [[ -z "${port}" ]] || [[ ! -e "${port}" ]]; then
        for candidate in /dev/ttyUSB0 /dev/ttyUSB1 /dev/ttyUSB2 \
                         /dev/ttyACM1 /dev/ttyACM2; do
            [[ "${candidate}" == "${GPS_DEVICE}" ]] && continue
            [[ -e "${candidate}" ]] && { port="${candidate}"; break; }
        done
    fi
    if [[ -z "${port}" ]] || [[ ! -e "${port}" ]]; then
        echo "[preflight]   No ESP32 serial port found (${GPS_DEVICE} excluded as GPS)"
        echo "[preflight]   Ports present: $(ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null \
            | tr '\n' ' ' || echo 'none')"
        echo "[preflight]   Check:"
        echo "[preflight]     1. Cable is a DATA cable (not charge-only)"
        echo "[preflight]     2. ESP32 is in a DATA port on the hub"
        echo "[preflight]     3. sudo dmesg -w  then re-plug the ESP32"
        return 1
    fi
    echo "[preflight]   Found ESP32 port: ${port}"
    local holder
    holder="$(fuser "${port}" 2>/dev/null | tr -s ' ' '\n' | grep -v '^$' | head -1 || true)"
    if [[ -n "${holder}" ]]; then
        local hname
        hname="$(ps -p "${holder}" -o comm= 2>/dev/null || echo 'unknown')"
        local auto=0
        local auto_kill=("screen" "minicom" "picocom" "python3" "python"
                         "arduino-cli" "arduino" "java" "tio" "cu")
        for proc in "${auto_kill[@]}"; do
            [[ "${hname}" == "${proc}"* ]] && { auto=1; break; }
        done
        if [[ "${auto}" -eq 1 ]]; then
            echo "[preflight]   Auto-closing '${hname}' (pid ${holder}) — serial terminal left open"
            _kill_process "${holder}" "${hname}" "serial terminal blocking ESP32"
        else
            _prompt_kill "${holder}" "${hname}" "${port}" || true
        fi
        sleep 0.5
    fi
    echo "[preflight]   ESP32: clear on ${port}"
}

preflight_all() {
    echo ""
    echo "[preflight] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "[preflight] Pre-flight device checks"
    echo "[preflight] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    preflight_gps     || true
    preflight_rtl_sdr || true
    preflight_wifi    || true
    preflight_esp32   || true
    echo "[preflight] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
}

# ══════════════════════════════════════════════════════════════════════════════
# GPS — fix query and NMEA logger
# ══════════════════════════════════════════════════════════════════════════════
#
# GPS FIX: Uses gpspipe (proven to work) rather than raw socket code in a
# heredoc. Heredocs inside $() command substitutions under set -euo pipefail
# are fragile — gpspipe sidesteps this entirely.
#
# gpspipe -w streams gpsd JSON watch objects one per line to stdout.
# We read up to 60 objects (enough to see multiple TPV+SKY cycles) with an
# 8-second hard timeout, then parse for a fix.

gps_query_fix() {
    local min_sats="${GPS_MIN_SATS:-4}"

    # gpspipe -w: stream JSON watch objects from gpsd
    # timeout 8:  hard ceiling so we never block more than 8 seconds
    # -n 60:      read at most 60 objects (avoids hanging if gpsd is chatty)
    local result
    result=$(
        timeout 8 gpspipe -w -n 60 2>/dev/null | \
        python3 -c "
import sys, json

min_sats = ${min_sats}
lat = lon = alt = None
sats = 0
mode = 0

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
    except Exception:
        continue
    cls = obj.get('class', '')
    if cls == 'TPV':
        m = obj.get('mode', 0)
        if m > mode:
            mode = m
        if obj.get('lat') is not None:
            lat = obj['lat']
        if obj.get('lon') is not None:
            lon = obj['lon']
        if obj.get('alt') is not None:
            alt = obj['alt']
    elif cls == 'SKY':
        used = sum(1 for sv in obj.get('satellites', []) if sv.get('used'))
        if used > sats:
            sats = used
    # Exit early once we have a solid fix
    if mode >= 2 and lat is not None and lon is not None and sats >= min_sats:
        break

if mode >= 2 and lat is not None and lon is not None and sats >= min_sats:
    dims = '3D' if mode == 3 else '2D'
    print(f'{lat:.6f} {lon:.6f} {sats} {dims}')
else:
    print('NO_FIX')
" 2>/dev/null
    ) || true

    echo "${result:-NO_FIX}"
}

gps_wait_for_fix() {
    local deadline=$(( $(date +%s) + GPS_WAIT_FIX ))
    echo -n "[gps] Waiting for fix (up to ${GPS_WAIT_FIX}s, need ${GPS_MIN_SATS}+ sats)… "
    while [[ $(date +%s) -lt "${deadline}" ]]; do
        local fix
        fix="$(gps_query_fix)"
        if [[ "${fix}" != "NO_FIX"* ]]; then
            echo ""; echo "[gps] Fix acquired: ${fix}"; return 0
        fi
        echo -n "."; sleep 3
    done
    echo ""
    echo "[gps] WARNING: No fix after ${GPS_WAIT_FIX}s — starting without GPS coordinates"
    echo "[gps]   lat/lon will be NULL in this session. Make sure puck has sky view."
    return 1
}

launch_gps_logger() {
    exec gpspipe -r -o "${SESSION_DIR}/gps/nmea.log"
}

start_gps_collector() {
    if [[ "${ENABLE_GPS}" != "true" ]]; then
        echo "[gps] Disabled in config — skipping"
        return
    fi
    if ! systemctl is-active --quiet gpsd 2>/dev/null; then
        echo "[gps] ERROR: gpsd not running — GPS collector skipped"
        return
    fi
    if [[ ! -e "${GPS_DEVICE}" ]]; then
        echo "[gps] ERROR: ${GPS_DEVICE} not found — GPS collector skipped"
        return
    fi
    gps_wait_for_fix || true
    if ! command -v gpspipe &>/dev/null; then
        echo "[gps] WARNING: gpspipe not found — install gpsd-clients for NMEA logging"
        update_manifest gps true
        return
    fi
    echo "[gps] Starting NMEA logger (under supervisor)"
    supervise_collector gps launch_gps_logger &
    CHILD_PIDS+=($!)
    update_manifest gps true
    echo "[gps] supervisor PID ${CHILD_PIDS[-1]}"
    echo "[gps] Raw NMEA → ${SESSION_DIR}/gps/nmea.log"
}

# ══════════════════════════════════════════════════════════════════════════════
# Generic collector supervisor
# ══════════════════════════════════════════════════════════════════════════════
supervise_collector() {
    local label="$1"
    local launcher="$2"

    local restart_count=0
    local backoff="${RESTART_BACKOFF_INITIAL}"
    local current_pid=""

    trap '
        if [[ -n "${current_pid}" ]] && kill -0 "${current_pid}" 2>/dev/null; then
            kill -TERM "${current_pid}" 2>/dev/null || true
        fi
        exit 0
    ' TERM INT

    while true; do
        local start_time
        start_time=$(date +%s)

        echo "[${label}] supervisor: launching collector…"

        ${launcher} &
        current_pid=$!

        local exit_code=0
        wait "${current_pid}" 2>/dev/null || exit_code=$?
        current_pid=""

        local end_time
        end_time=$(date +%s)
        local lifetime=$(( end_time - start_time ))

        if (( lifetime >= RESTART_RESET_AFTER )); then
            if (( restart_count > 0 )); then
                echo "[${label}] supervisor: ran stably for ${lifetime}s, resetting restart counter"
            fi
            restart_count=0
            backoff="${RESTART_BACKOFF_INITIAL}"
        fi

        echo "[${label}] supervisor: collector exited (code=${exit_code}, lifetime=${lifetime}s)"

        restart_count=$(( restart_count + 1 ))

        if (( restart_count > RESTART_MAX )); then
            echo "[${label}] supervisor: ${RESTART_MAX} restarts in a row — giving up on ${label}"
            return 1
        fi

        echo "[${label}] supervisor: restart ${restart_count}/${RESTART_MAX} in ${backoff}s…"
        sleep "${backoff}"
        backoff=$(( backoff * 2 ))
        (( backoff > RESTART_BACKOFF_MAX )) && backoff="${RESTART_BACKOFF_MAX}"
    done
}

# ── WiFi collector (Kismet) ────────────────────────────────────────────────────
# MT7612U FIX: We let Kismet create the monitor interface itself (it has
# internal retry logic), but run airmon-ng first to put the interface in
# monitor mode — airmon-ng has chipset-specific handling that avoids the
# RTNETLINK timeout. We then pass the resulting wlan1mon directly to Kismet
# so it doesn't try to recreate it, just use it.
setup_monitor_interface() {
    local iface="${WIFI_INTERFACE}"
    echo "[wifi] Bringing up monitor interface via airmon-ng…"

    # airmon-ng handles the MT7612U ioctl path correctly and names the result
    airmon-ng start "${iface}" >/dev/null 2>&1 || true

    # Detect what airmon-ng created (usually wlan1mon or similar)
    local mon
    mon="$(iw dev 2>/dev/null | awk '/Interface/ {print $2}' \
        | grep -E "^${iface}mon$|mon$" | head -1 || true)"

    if [[ -z "${mon}" ]]; then
        echo "[wifi] ERROR: airmon-ng did not create a monitor interface — WiFi SKIPPED"
        return 1
    fi

    echo "[wifi] Monitor interface ready: ${mon}"
    MON_IFACE="${mon}"
    return 0
}

# MON_IFACE is set by setup_monitor_interface and used by launch_kismet
MON_IFACE=""

launch_kismet() {
    local wifi_dir="${SESSION_DIR}/wifi"
    # Pass the pre-created monitor interface so Kismet doesn't try to
    # manage interface state itself (avoids the MT7612U ioctl timeout).
    exec kismet \
        --no-ncurses \
        -c "${MON_IFACE}:type=linuxwifi,name=alfa" \
        --log-prefix="${wifi_dir}/" \
        --log-types kismet,pcapng
}

start_wifi_collector() {
    if [[ "${ENABLE_WIFI:-false}" != "true" ]]; then
        echo "[wifi] Disabled in config — skipping"
        return
    fi

    setup_monitor_interface || return

    echo "[wifi] Starting Kismet on ${MON_IFACE} (under supervisor)"
    supervise_collector wifi launch_kismet &
    CHILD_PIDS+=($!)
    update_manifest wifi true
    echo "[wifi] supervisor PID ${CHILD_PIDS[-1]}"
}

# ── SDR collector (rtl_433) ────────────────────────────────────────────────────
launch_rtl433() {
    local sdr_out="${SESSION_DIR}/sdr/${SESSION_NAME}_rtl433.ndjson"
    exec rtl_433 \
        -f "${SDR_FREQUENCY_MHZ:-915}M" \
        -M time:utc \
        -M protocol \
        -M level \
        -F "json:${sdr_out}"
}

start_sdr_collector() {
    if [[ "${ENABLE_SDR:-false}" != "true" ]]; then
        echo "[sdr] Disabled in config — skipping"
        return
    fi
    if ! lsusb | grep -qE "RTL283[28]|0bda:283[28]"; then
        echo "[sdr] WARNING: RTL-SDR not detected — skipping"
        return
    fi
    if lsof 2>/dev/null | grep -q librtlsdr; then
        echo "[sdr] WARNING: RTL-SDR busy — skipping"
        return
    fi
    echo "[sdr] Starting rtl_433 on ${SDR_FREQUENCY_MHZ:-915} MHz (under supervisor)"
    supervise_collector sdr launch_rtl433 &
    CHILD_PIDS+=($!)
    update_manifest sdr true
    echo "[sdr] supervisor PID ${CHILD_PIDS[-1]}"
}

# ── Wideband SDR collector (600-6000 MHz spectrum scanner) ──────────────────
launch_wideband_scanner() {
    local sdr_out="${SESSION_DIR}/sdr"
    mkdir -p "${sdr_out}"
    local scanner_script="${SCRIPT_DIR}/processing/rtl_wideband.py"
    exec python3 "${scanner_script}" \
        "${sdr_out}" \
        "${WIDEBAND_FREQ_START_MHZ:-600}" \
        "${WIDEBAND_FREQ_END_MHZ:-6000}" \
        "${WIDEBAND_SCAN_STEP_MHZ:-1}" \
        "${WIDEBAND_SCAN_TIME:-10}" \
        "${WIDEBAND_LOCKUP_TIME:-30}" \
        "${WIDEBAND_PEAK_THRESHOLD:--40}"
}

start_wideband_collector() {
    if [[ "${ENABLE_WIDEBAND_SDR:-false}" != "true" ]]; then
        echo "[wideband] Disabled in config — skipping"
        return
    fi
    if ! lsusb | grep -qE "RTL283[28]|0bda:283[28]"; then
        echo "[wideband] WARNING: RTL-SDR not detected — skipping"
        return
    fi
    if ! command -v rtl_power &>/dev/null; then
        echo "[wideband] ERROR: rtl_power not found — skipping"
        return
    fi
    echo "[wideband] Starting spectrum scanner (under supervisor)"
    supervise_collector wideband launch_wideband_scanner &
    CHILD_PIDS+=($!)
    update_manifest sdr true
    echo "[wideband] supervisor PID ${CHILD_PIDS[-1]}"
}

# ── ESP32 BLE collector ────────────────────────────────────────────────────────
launch_esp32_reader() {
    local port="${ESP32_DEVICE:-}"
    # Re-detect at each launch. Always skip GPS_DEVICE.
    if [[ -z "${port}" ]] || [[ ! -e "${port}" ]]; then
        for candidate in /dev/ttyUSB0 /dev/ttyUSB1 /dev/ttyUSB2 \
                         /dev/ttyACM1 /dev/ttyACM2; do
            [[ "${candidate}" == "${GPS_DEVICE}" ]] && continue
            [[ -e "${candidate}" ]] && { port="${candidate}"; break; }
        done
    fi
    if [[ -z "${port}" ]] || [[ ! -e "${port}" ]]; then
        echo "[esp32] launcher: no serial port available — exiting (supervisor will retry)"
        return 1
    fi
    local baud="${ESP32_BAUD:-921600}"
    stty -F "${port}" "${baud}" raw -echo 2>/dev/null || true
    local bt_out="${SESSION_DIR}/bt/esp32_ble.ndjson"
    local reader_script="${SCRIPT_DIR}/processing/esp32_reader.py"
    local gps_flag=""
    [[ "${ENABLE_GPS}" == "true" ]] && gps_flag="--gpsd"
    exec python3 "${reader_script}" \
        --port "${port}" \
        --baud "${baud}" \
        --output "${bt_out}" \
        ${gps_flag}
}

start_esp32_collector() {
    if [[ "${ENABLE_ESP32:-false}" != "true" ]]; then
        echo "[esp32] Disabled in config — skipping"
        return
    fi
    local reader_script="${SCRIPT_DIR}/processing/esp32_reader.py"
    if [[ ! -f "${reader_script}" ]]; then
        echo "[esp32] ERROR: reader script not found: ${reader_script}"
        return
    fi
    local found=""
    for candidate in /dev/ttyUSB0 /dev/ttyUSB1 /dev/ttyUSB2 \
                     /dev/ttyACM1 /dev/ttyACM2; do
        [[ "${candidate}" == "${GPS_DEVICE}" ]] && continue
        [[ -e "${candidate}" ]] && { found="${candidate}"; break; }
    done
    if [[ -z "${found}" ]]; then
        echo "[esp32] ERROR: No serial port found (${GPS_DEVICE} excluded as GPS)"
        echo "[esp32] Collector SKIPPED."
        return
    fi
    echo "[esp32] Starting BLE reader on ${found} (under supervisor)"
    [[ "${ENABLE_GPS}" == "true" ]] && echo "[esp32] GPS injection enabled"
    supervise_collector esp32 launch_esp32_reader &
    CHILD_PIDS+=($!)
    update_manifest esp32 true
    echo "[esp32] supervisor PID ${CHILD_PIDS[-1]}"
}

# ── Heartbeat monitor ──────────────────────────────────────────────────────────
heartbeat_monitor() {
    while true; do
        sleep 60
        local alive=0
        if [[ ${#CHILD_PIDS[@]} -gt 0 ]]; then
            for pid in "${CHILD_PIDS[@]}"; do
                kill -0 "${pid}" 2>/dev/null && (( alive++ )) || true
            done
        fi
        local total=${#CHILD_PIDS[@]}
        local collectors_alive=$(( alive > 0 ? alive - 1 : 0 ))
        local collectors_total=$(( total > 0 ? total - 1 : 0 ))

        local gps_status=""
        if [[ "${ENABLE_GPS}" == "true" ]]; then
            local fix
            fix="$(gps_query_fix)"
            if [[ "${fix}" == "NO_FIX"* ]]; then
                gps_status=" | GPS: NO FIX"
            else
                gps_status=" | GPS: ${fix}"
            fi
        fi

        echo "[wardrive] heartbeat: ${collectors_alive}/${collectors_total} supervisors alive — $(date -u +%H:%M:%SZ)${gps_status}"
        if (( collectors_alive == 0 )) && (( collectors_total > 0 )); then
            echo "[wardrive] ERROR: all collector supervisors gave up — shutting down"
            exit 1
        fi
    done
}

# ── Main ───────────────────────────────────────────────────────────────────────
init_manifest
preflight_all
inhibit_sleep

# ── PID file for webapp stop control ───────────────────────────────────────────
WARDRIVE_PID_FILE="/tmp/wardrive.pid"
echo $$ > "${WARDRIVE_PID_FILE}"
echo "[wardrive] PID file: ${WARDRIVE_PID_FILE} (PID $$)"

echo "[wardrive] Starting collectors with auto-restart supervisors…"
start_gps_collector
start_wifi_collector
start_sdr_collector
start_wideband_collector
start_esp32_collector

if [[ ${#CHILD_PIDS[@]} -eq 0 ]]; then
    echo "[wardrive] No collectors started — check config/wardrive.conf. Exiting."
    exit 1
fi

echo "[wardrive] All collectors running. Ctrl-C to stop."
echo "[wardrive] Heartbeat will log every 60 s."
echo "[wardrive] Each collector will auto-restart up to ${RESTART_MAX} times if it crashes."

heartbeat_monitor &
CHILD_PIDS+=($!)

wait
