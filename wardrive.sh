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

# Parse flags before anything else
NO_TEE=false
for _arg in "$@"; do
    [[ "${_arg}" == "--no-tee" ]] && NO_TEE=true
done

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

# ── TUI command channel ────────────────────────────────────────────────────────
PID_DIR="${SCRIPT_DIR}/capture/pids"
CMD_FILE="${SCRIPT_DIR}/capture/wardrive.cmd"

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
_CAPTURE_ROOT="${CAPTURE_BASE_DIR:-${SCRIPT_DIR}/capture}"
SESSION_DIR="${_CAPTURE_ROOT}/raw/${SESSION_NAME}"
LOG_DIR="${_CAPTURE_ROOT}/logs"

mkdir -p "${SESSION_DIR}"/{wifi,sdr,bt,gps} "${LOG_DIR}"

SESSION_LOG="${LOG_DIR}/${SESSION_NAME}.log"
if [[ "${NO_TEE}" != "true" ]]; then
    exec > >(tee -a "${SESSION_LOG}") 2>&1
fi

echo "[wardrive] Session: ${SESSION_NAME}"
echo "[wardrive] Dir:     ${SESSION_DIR}"

# ── PID tracking ───────────────────────────────────────────────────────────────
CHILD_PIDS=()
INHIBITOR_PID=""
CLEANUP_DONE=0
SCREEN_BLANK_DISPLAY=""
SCREEN_BLANK_XAUTH=""

cleanup() {
    [[ "${CLEANUP_DONE}" -eq 1 ]] && return
    CLEANUP_DONE=1

    if [[ -n "${INHIBITOR_PID:-}" ]] && kill -0 "${INHIBITOR_PID}" 2>/dev/null; then
        kill "${INHIBITOR_PID}" 2>/dev/null || true
        echo "[wardrive] Sleep inhibitor released — normal lid/sleep behavior restored"
    fi

    if [[ -n "${SCREEN_BLANK_DISPLAY:-}" ]]; then
        DISPLAY="${SCREEN_BLANK_DISPLAY}" XAUTHORITY="${SCREEN_BLANK_XAUTH}" \
            xset s default 2>/dev/null || true
        DISPLAY="${SCREEN_BLANK_DISPLAY}" XAUTHORITY="${SCREEN_BLANK_XAUTH}" \
            xset +dpms 2>/dev/null || true
        echo "[wardrive] Screen blanking restored"
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

    # Clean up TUI command channel and per-collector PID files
    if [[ -d "${PID_DIR:-}" ]]; then
        for _pid_f in "${PID_DIR}"/*.pid; do
            [[ -f "${_pid_f}" ]] || continue
            _pid_val=$(cat "${_pid_f}" 2>/dev/null) || continue
            [[ -n "${_pid_val}" ]] && kill -KILL "${_pid_val}" 2>/dev/null || true
        done
        rm -rf "${PID_DIR}" 2>/dev/null || true
    fi
    rm -f "${CMD_FILE:-}" 2>/dev/null || true

    rm -f "/tmp/wardrive.pid" "${SCRIPT_DIR}/capture/wardrive.pid"
    finalize_manifest
    echo "[wardrive] Session closed: ${SESSION_NAME}"
}
trap cleanup EXIT
trap '{ cleanup; exit 1; }' INT TERM

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

inhibit_screen_blank() {
    [[ "${KEEP_AWAKE:-true}" != "true" ]] && return

    local disp="${DISPLAY:-}"
    local xauth="${XAUTHORITY:-}"

    # When running as sudo DISPLAY/XAUTHORITY are stripped; recover them from
    # the invoking user's environment and the active X lock file.
    if [[ -z "${disp}" && -n "${SUDO_USER:-}" ]]; then
        local user_home
        user_home="$(getent passwd "${SUDO_USER}" | cut -d: -f6)"
        for candidate in :0 :1; do
            [[ -f "/tmp/.X${candidate#:}-lock" ]] && { disp="${candidate}"; break; }
        done
        [[ -z "${xauth}" && -f "${user_home}/.Xauthority" ]] \
            && xauth="${user_home}/.Xauthority"
    fi

    [[ -z "${disp}" ]] && return

    if ! DISPLAY="${disp}" XAUTHORITY="${xauth}" xset q &>/dev/null; then
        echo "[wardrive] WARNING: cannot reach display ${disp} — screen may blank during session"
        return
    fi

    DISPLAY="${disp}" XAUTHORITY="${xauth}" xset s off  2>/dev/null || true
    DISPLAY="${disp}" XAUTHORITY="${xauth}" xset -dpms  2>/dev/null || true
    SCREEN_BLANK_DISPLAY="${disp}"
    SCREEN_BLANK_XAUTH="${xauth}"
    echo "[wardrive] Screen blanking disabled (display ${disp})"
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

    # Skip airmon-ng check kill — we use a dedicated monitor adapter (wlan1)
    # so NetworkManager managing wlan0 does not interfere. Killing NM here
    # would drop any active internet connection on wlan0.
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

    if ! [[ "${min_sats}" =~ ^[0-9]+$ ]]; then
        echo "[gps] ERROR: GPS_MIN_SATS must be a non-negative integer, got '${min_sats}'" >&2
        min_sats=4
    fi

    # gpspipe -w: stream JSON watch objects from gpsd
    # timeout 8:  hard ceiling so we never block more than 8 seconds
    # -n 60:      read at most 60 objects (avoids hanging if gpsd is chatty)
    #
    # NOTE: the Python parser lives in processing/gps_query.py. We cannot use
    # a heredoc here because the heredoc would override the pipe on python3's
    # stdin, causing the gpspipe data to go unread and always returning NO_FIX.
    local gps_script="${SCRIPT_DIR}/processing/gps_query.py"
    local result
    # Per-attempt timeout: capped at 8s minimum, or half GPS_WAIT_FIX so the
    # outer loop in gps_wait_for_fix gets at least 2 tries within the budget.
    local per_attempt=$(( GPS_WAIT_FIX / 2 ))
    (( per_attempt < 8 )) && per_attempt=8
    result=$(timeout "${per_attempt}" gpspipe -w -n 60 2>/dev/null | python3 "${gps_script}" "${min_sats}") || true

    echo "${result:-NO_FIX}"
}

gps_wait_for_fix() {
    local now deadline
    now=$(date +%s)
    deadline=$(( now + GPS_WAIT_FIX ))
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
    _sv_pid=$!
    CHILD_PIDS+=("${_sv_pid}")
    printf '%d\n' "${_sv_pid}" > "${PID_DIR}/gps.pid"
    update_manifest gps true
    echo "[gps] supervisor PID ${_sv_pid}"
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
    _sv_pid=$!
    CHILD_PIDS+=("${_sv_pid}")
    printf '%d\n' "${_sv_pid}" > "${PID_DIR}/wifi.pid"
    update_manifest wifi true
    echo "[wifi] supervisor PID ${_sv_pid}"
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
    local freq_int="${SDR_FREQUENCY_MHZ:-915}"
    freq_int="${freq_int%.*}"  # strip decimal if present
    if ! [[ "${freq_int}" =~ ^[0-9]+$ ]] || (( freq_int < 24 || freq_int > 1766 )); then
        echo "[sdr] ERROR: SDR_FREQUENCY_MHZ=${SDR_FREQUENCY_MHZ} is outside RTL-SDR range (24-1766 MHz) — skipping"
        return 1
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
    _sv_pid=$!
    CHILD_PIDS+=("${_sv_pid}")
    printf '%d\n' "${_sv_pid}" > "${PID_DIR}/sdr.pid"
    update_manifest sdr true
    echo "[sdr] supervisor PID ${_sv_pid}"
}

# ── Wideband SDR collector (600-6000 MHz spectrum scanner) ──────────────────
launch_wideband_scanner() {
    local sdr_out="${SESSION_DIR}/sdr"
    mkdir -p "${sdr_out}"
    local scanner_script="${SCRIPT_DIR}/processing/rtl_wideband.py"
    exec python3 "${scanner_script}" \
        "${sdr_out}" \
        "${WIDEBAND_FREQ_START_MHZ:-100}" \
        "${WIDEBAND_FREQ_END_MHZ:-1700}" \
        "${WIDEBAND_SCAN_STEP_MHZ:-2}" \
        "${WIDEBAND_SCAN_TIME:-1}" \
        "${WIDEBAND_PEAK_THRESHOLD:--40}"
}

start_wideband_collector() {
    if [[ "${ENABLE_WIDEBAND_SDR:-false}" != "true" ]]; then
        echo "[wideband] Disabled in config — skipping"
        return
    fi
    # rtl_433 and wideband scanner both require exclusive access to the RTL-SDR dongle.
    if [[ "${ENABLE_SDR:-false}" == "true" ]]; then
        echo "[wideband] ERROR: ENABLE_SDR and ENABLE_WIDEBAND_SDR are both true."
        echo "[wideband]   They share the same RTL-SDR dongle — disable one in config/wardrive.conf."
        echo "[wideband]   Wideband scanner skipped."
        return
    fi
    if ! lsusb | grep -qE "RTL283[28]|0bda:283[28]"; then
        echo "[wideband] WARNING: RTL-SDR not detected — skipping"
        return
    fi
    if ! command -v rtl_power &>/dev/null; then
        echo "[wideband] ERROR: rtl_power not found — install rtl-sdr tools"
        return
    fi
    echo "[wideband] Starting spectrum scanner ${WIDEBAND_FREQ_START_MHZ:-100}-${WIDEBAND_FREQ_END_MHZ:-1700} MHz (under supervisor)"
    supervise_collector wideband launch_wideband_scanner &
    _sv_pid=$!
    CHILD_PIDS+=("${_sv_pid}")
    printf '%d\n' "${_sv_pid}" > "${PID_DIR}/wideband.pid"
    update_manifest sdr true
    echo "[wideband] supervisor PID ${_sv_pid}"
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
    local -a gps_args=()
    [[ "${ENABLE_GPS}" == "true" ]] && gps_args=("--gpsd")
    exec python3 "${reader_script}" \
        --port "${port}" \
        --baud "${baud}" \
        --output "${bt_out}" \
        "${gps_args[@]}"
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
    _sv_pid=$!
    CHILD_PIDS+=("${_sv_pid}")
    printf '%d\n' "${_sv_pid}" > "${PID_DIR}/esp32.pid"
    update_manifest esp32 true
    echo "[esp32] supervisor PID ${_sv_pid}"
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
        # CHILD_PIDS in this subshell contains only collector supervisors
        # (the heartbeat's own PID is appended in the parent after this
        # subshell was forked, so it is NOT visible here — no -1 adjustment).
        local total=${#CHILD_PIDS[@]}
        local collectors_alive=${alive}
        local collectors_total=${total}

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

# ── TUI command channel listener ───────────────────────────────────────────────
cmd_listener() {
    local last_size=0
    while [[ "${CLEANUP_DONE}" -eq 0 ]]; do
        local cur_size
        cur_size=$(wc -c < "${CMD_FILE}" 2>/dev/null || echo "${last_size}")
        if (( cur_size > last_size )); then
            local new_data
            new_data=$(tail -c +$(( last_size + 1 )) "${CMD_FILE}" 2>/dev/null || true)
            last_size="${cur_size}"
            while IFS= read -r _cmd; do
                [[ -z "${_cmd}" ]] && continue
                echo "[wardrive] cmd channel: ${_cmd}"
                handle_cmd_channel "${_cmd}"
            done <<< "${new_data}"
        fi
        sleep 1
    done
}

handle_cmd_channel() {
    local _cmd="$1"
    case "${_cmd}" in
        start:wifi)      start_wifi_collector ;;
        start:sdr)       start_sdr_collector ;;
        start:wideband)  start_wideband_collector ;;
        start:esp32)     start_esp32_collector ;;
        start:gps)       start_gps_collector ;;
        *) echo "[wardrive] Unknown command: ${_cmd}" ;;
    esac
}

# ── Main ───────────────────────────────────────────────────────────────────────
init_manifest
preflight_all
inhibit_sleep
inhibit_screen_blank

mkdir -p "${PID_DIR}"
printf '' > "${CMD_FILE}" 2>/dev/null || true

# ── PID file for webapp stop control ───────────────────────────────────────────
# Write to project-local capture/ instead of /tmp (which is world-writable).
# Using /tmp would let any local user plant an arbitrary PID and get the webapp
# to signal it. The capture/ directory is owned by root when wardrive runs.
WARDRIVE_PID_FILE="${SCRIPT_DIR}/capture/wardrive.pid"
printf '%d\n' "$$" > "${WARDRIVE_PID_FILE}" || true
chmod 600 "${WARDRIVE_PID_FILE}" 2>/dev/null || true
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

cmd_listener &
CHILD_PIDS+=($!)

wait
