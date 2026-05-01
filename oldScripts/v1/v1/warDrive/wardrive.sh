#!/usr/bin/env bash
#
# wardrive.sh — main launcher for a wardriving capture session
#
# Spawns one collector per available sensor in parallel, each writing raw
# output to a timestamped session directory. Designed so collectors are
# independent: if one dies, the others keep running.
#
# Usage:
#   sudo ./wardrive.sh [--name <session_name>] [--no-wifi] [--no-sdr] [--no-keep-awake]
#
# All collectors are passive / receive-only. No packet injection, no
# transmissions. Stop the session cleanly with Ctrl-C.

set -euo pipefail

# --- Resolve script directory so we can be run from anywhere -----------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CAPTURE_ROOT="${SCRIPT_DIR}/capture"
CONFIG_FILE="${SCRIPT_DIR}/config/wardrive.conf"

# --- Defaults (overridable via config file or CLI flags) ---------------------
SESSION_NAME=""
ENABLE_WIFI=1
ENABLE_SDR=1
KEEP_AWAKE=1                               # block sleep/lid-close while running
INHIBITOR_PID=""                           # systemd-inhibit child pid
WIFI_IFACE="${WIFI_IFACE:-wlan1}"          # Alfa adapter device name
SDR_DEVICE_INDEX="${SDR_DEVICE_INDEX:-0}"  # rtl_433 -d <n>
RTL433_FREQS=(433920000 315000000 868000000 915000000)  # multi-freq hop

# Load user config if present (overrides defaults)
[[ -f "$CONFIG_FILE" ]] && source "$CONFIG_FILE"

# --- CLI parsing -------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)          SESSION_NAME="$2"; shift 2 ;;
    --no-wifi)       ENABLE_WIFI=0; shift ;;
    --no-sdr)        ENABLE_SDR=0; shift ;;
    --no-keep-awake) KEEP_AWAKE=0; shift ;;
    --iface)         WIFI_IFACE="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

# --- Must be root for monitor mode + USB device access -----------------------
if [[ $EUID -ne 0 ]]; then
  echo "ERROR: this script must run as root (monitor mode + USB access)." >&2
  echo "       sudo $0 $*" >&2
  exit 1
fi

# --- Build session directory -------------------------------------------------
SESSION_TS="$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -n "$SESSION_NAME" ]]; then
  SESSION_DIR="${CAPTURE_ROOT}/raw/${SESSION_TS}_${SESSION_NAME}"
else
  SESSION_DIR="${CAPTURE_ROOT}/raw/${SESSION_TS}"
fi
mkdir -p "$SESSION_DIR" "${CAPTURE_ROOT}/logs"

LOG_FILE="${CAPTURE_ROOT}/logs/${SESSION_TS}.log"

log() {
  local msg="[$(date -u +%H:%M:%SZ)] $*"
  echo "$msg" | tee -a "$LOG_FILE"
}

# --- Session manifest --------------------------------------------------------
write_manifest() {
  cat > "${SESSION_DIR}/manifest.json" <<EOF
{
  "session_id": "${SESSION_TS}${SESSION_NAME:+_}${SESSION_NAME}",
  "started_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "hostname": "$(hostname)",
  "kernel": "$(uname -r)",
  "collectors": {
    "wifi": $([ "$ENABLE_WIFI" = 1 ] && echo true || echo false),
    "sdr":  $([ "$ENABLE_SDR" = 1 ]  && echo true || echo false),
    "gps":  false,
    "esp32": false
  },
  "wifi_interface": "${WIFI_IFACE}",
  "sdr_device_index": ${SDR_DEVICE_INDEX},
  "rtl433_freqs_hz": [$(IFS=,; echo "${RTL433_FREQS[*]}")],
  "schema_version": 1,
  "notes": "Raw capture session. Process with processing/enrich.py to produce queryable output."
}
EOF
}

# --- Power management: prevent laptop sleep / lid-close suspend --------------
# Uses systemd-inhibit, the clean modern way: it holds a lock for as long as
# the child process lives, and releases automatically on script exit. No
# permanent config changes — your normal sleep/lockscreen behavior comes
# right back when the session ends, even if the script crashes.
inhibit_sleep() {
  if [[ "$KEEP_AWAKE" != 1 ]]; then
    log "Power management: --no-keep-awake set, laptop will sleep normally"
    return 0
  fi
  if ! command -v systemd-inhibit >/dev/null; then
    log "WARN: systemd-inhibit not found — cannot prevent sleep"
    return 0
  fi
  # Block: idle-triggered sleep, lid-close suspend, suspend key, idle timeouts.
  # The 'sleep infinity' child holds the lock until cleanup kills it.
  systemd-inhibit \
    --what="sleep:idle:handle-lid-switch:handle-suspend-key" \
    --who="wardrive.sh" \
    --why="Active wardriving capture — sleeping would interrupt collection" \
    --mode="block" \
    sleep infinity &
  INHIBITOR_PID=$!
  if kill -0 "$INHIBITOR_PID" 2>/dev/null; then
    log "Power management: sleep/lid-close inhibited (pid $INHIBITOR_PID)"
    log "  Laptop stays awake until script exits, even with lid closed"
  else
    log "WARN: failed to inhibit sleep — capture may stop if lid closes"
    INHIBITOR_PID=""
  fi
}

release_sleep() {
  if [[ -n "$INHIBITOR_PID" ]] && kill -0 "$INHIBITOR_PID" 2>/dev/null; then
    log "Power management: releasing sleep inhibitor"
    kill -TERM "$INHIBITOR_PID" 2>/dev/null || true
    wait "$INHIBITOR_PID" 2>/dev/null || true
    log "  Original sleep/lid behavior restored"
  fi
}

# --- Pre-flight checks -------------------------------------------------------
preflight() {
  local missing=()
  if [[ "$ENABLE_WIFI" = 1 ]]; then
    command -v airmon-ng    >/dev/null || missing+=("aircrack-ng")
    command -v kismet       >/dev/null || missing+=("kismet")
    if ! ip link show "$WIFI_IFACE" &>/dev/null; then
      log "WARN: WiFi interface '$WIFI_IFACE' not found. Use --iface or edit config."
      log "      Available interfaces:"
      ip -br link | awk '$1 ~ /^wl/ {print "        " $1}' | tee -a "$LOG_FILE"
      ENABLE_WIFI=0
    fi
  fi
  if [[ "$ENABLE_SDR" = 1 ]]; then
    command -v rtl_433 >/dev/null || missing+=("rtl-433")
    command -v rtl_test >/dev/null || missing+=("rtl-sdr")
    # Use lsusb instead of rtl_test — rtl_test exits non-zero on R820T
    # tuners due to its E4000 check, which trips up set -o pipefail.
    if ! lsusb | grep -qE "RTL283[28]|0bda:283[28]"; then
      log "WARN: RTL-SDR not detected via lsusb. SDR collector disabled."
      ENABLE_SDR=0
    elif lsof 2>/dev/null | grep -q librtlsdr; then
      log "WARN: RTL-SDR busy. Run: sudo lsof | grep librtlsdr"
      ENABLE_SDR=0
    fi
  fi
  if [[ ${#missing[@]} -gt 0 ]]; then
    log "ERROR: missing dependencies: ${missing[*]}"
    log "Install with: sudo apt install ${missing[*]}"
    exit 1
  fi
}

# --- Track child PIDs so we can clean them up on exit ------------------------
declare -a CHILD_PIDS=()

cleanup() {
  log "Stopping session — cleaning up collectors..."
  release_sleep
  for pid in "${CHILD_PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  # Give them a chance to exit cleanly
  sleep 2
  for pid in "${CHILD_PIDS[@]:-}"; do
    kill -KILL "$pid" 2>/dev/null || true
  done
  # Restore wifi interface to managed mode if we touched it
  if [[ "$ENABLE_WIFI" = 1 ]] && [[ -n "${MON_IFACE:-}" ]]; then
    log "Returning $MON_IFACE to managed mode..."
    airmon-ng stop "$MON_IFACE" >/dev/null 2>&1 || true
  fi
  # Stamp completion in manifest
  if [[ -f "${SESSION_DIR}/manifest.json" ]]; then
    local end_ts
    end_ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    # naive but safe: append an end timestamp file rather than rewriting JSON
    echo "$end_ts" > "${SESSION_DIR}/ended_at_utc.txt"
  fi
  log "Session ${SESSION_TS} complete. Data in: ${SESSION_DIR}"
}
trap cleanup EXIT INT TERM

# --- Collector: Alfa WiFi via Kismet ----------------------------------------
start_wifi_collector() {
  log "Starting WiFi collector on ${WIFI_IFACE}..."
  # Bring up monitor mode
  airmon-ng check kill >/dev/null 2>&1 || true
  local mon_out
  mon_out="$(airmon-ng start "$WIFI_IFACE" 2>&1)" || {
    log "ERROR: failed to start monitor mode on $WIFI_IFACE"
    return 1
  }
  # Modern airmon-ng renames the iface (e.g. wlan1mon) — detect what it became
  MON_IFACE="$(iw dev | awk '/Interface/ {print $2}' | grep -E "^${WIFI_IFACE}|mon$" | head -1)"
  [[ -z "$MON_IFACE" ]] && MON_IFACE="${WIFI_IFACE}mon"
  log "Monitor interface: $MON_IFACE"

  local kismet_dir="${SESSION_DIR}/wifi"
  mkdir -p "$kismet_dir"

  # Kismet writes its own .kismet (sqlite), .pcapng, and JSON logs.
  # --no-ncurses keeps it headless; --override stops it from prompting.
  kismet \
    -c "${MON_IFACE}:type=linuxwifi,name=alfa" \
    --no-ncurses-wrapper \
    --no-line-wrap \
    --override wardrive \
    --log-prefix "$kismet_dir" \
    --log-title "session_${SESSION_TS}" \
    >> "${kismet_dir}/kismet.stdout.log" 2>&1 &

  CHILD_PIDS+=($!)
  log "Kismet started (pid $!) — logs in $kismet_dir"
}

# --- Collector: RTL-SDR via rtl_433 -----------------------------------------
start_sdr_collector() {
  log "Starting SDR collector (rtl_433)..."
  local sdr_dir="${SESSION_DIR}/sdr"
  mkdir -p "$sdr_dir"

  # rtl_433 with multiple frequencies hops between them.
  # -F json emits one JSON object per decoded packet.
  # -M time:utc adds ISO timestamps. -M protocol annotates protocol id.
  # -M level adds RSSI and SNR. -M stats:1:60 emits hourly stats.
  local freq_args=()
  for f in "${RTL433_FREQS[@]}"; do
    freq_args+=(-f "$f")
  done

  rtl_433 \
    -d "$SDR_DEVICE_INDEX" \
    "${freq_args[@]}" \
    -M time:utc \
    -M protocol \
    -M level \
    -M stats:1:60 \
    -F "json:${sdr_dir}/rtl433.ndjson" \
    -F "log:${sdr_dir}/rtl433.log" \
    >> "${sdr_dir}/rtl433.stdout.log" 2>&1 &

  CHILD_PIDS+=($!)
  log "rtl_433 started (pid $!) — output: ${sdr_dir}/rtl433.ndjson"
}

# --- GPS collector stub (activated when puck arrives) ------------------------
start_gps_collector() {
  log "GPS collector: SKIPPED (no puck connected yet)"
  log "  When puck arrives: enable in config/wardrive.conf and edit this script"
  log "  Will log NMEA + computed positions to \${SESSION_DIR}/gps/"
}

# --- ESP32 collector stub (activated when sensors deployed) ------------------
start_esp32_collector() {
  log "ESP32 collector: SKIPPED (sensors not yet deployed)"
  log "  Future: receive serial/MQTT stream from ESP32 sniffer fleet"
}

# --- Session monitor: watches collectors, restarts if needed -----------------
session_monitor() {
  while true; do
    sleep 30
    local alive=0
    for pid in "${CHILD_PIDS[@]:-}"; do
      kill -0 "$pid" 2>/dev/null && ((alive++)) || true
    done
    log "Heartbeat: ${alive}/${#CHILD_PIDS[@]} collectors alive"
    if [[ $alive -eq 0 ]]; then
      log "ERROR: all collectors died, exiting"
      exit 1
    fi
  done
}

# === MAIN ====================================================================
log "==========================================="
log "Wardrive session: $SESSION_TS"
log "Output dir:       $SESSION_DIR"
log "==========================================="

preflight
write_manifest
inhibit_sleep

[[ "$ENABLE_WIFI" = 1 ]] && start_wifi_collector
[[ "$ENABLE_SDR" = 1 ]]  && start_sdr_collector
start_gps_collector
start_esp32_collector

if [[ ${#CHILD_PIDS[@]} -eq 0 ]]; then
  log "ERROR: no collectors started. Check hardware and config."
  exit 1
fi

log "All active collectors running. Press Ctrl-C to stop."
log ""

session_monitor
