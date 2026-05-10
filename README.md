# Wardrive

Bootable Raspberry Pi 3B wardriving appliance. Flash, boot, drive. All collectors run simultaneously with automatic crash recovery, USB power budget enforcement, and a live web dashboard accessible from any device on your local network.

| Collector | Hardware | Data |
|-----------|----------|------|
| Kismet | Alfa AWUS036ACM (monitor mode) | 802.11 APs + clients |
| BLE | ESP32 DevKit V1 (via USB serial) | Bluetooth LE advertisements |
| GPS | U-blox or any NMEA puck | Coordinates injected into all observations |
| rtl_433 | RTL-SDR dongle | 433/915 MHz ISM devices |
| Wideband SDR | RTL-SDR dongle | 600–6000 MHz spectrum scans |
| HackRF | HackRF One | Wideband (stub — future hardware) |

RTL-SDR narrow-band (`rtl_433`) and wideband modes share the same dongle — switch between them live from the web UI during a drive. All other collectors run in parallel.

---

## Hardware

- Raspberry Pi 3 Model B (rev 1.2 tested)
- MicroSD card, 16 GB+ (storage falls back here if no USB drive present)
- USB drive labeled **WARDRIVE** for database + capture storage (auto-detected on boot)
- Alfa AWUS036ACM or compatible 802.11ac adapter (MT7612U chipset)
- RTL-SDR dongle (RTL2832U)
- ESP32 DevKit V1 flashed with the BLE scanner firmware
- U-blox or NMEA GPS puck
- HackRF One (optional — stub implemented, full support coming)

---

## Quick start

### 1 — Flash the image

Download the latest `wardrive-pi-*.img.xz` from [Releases](../../releases) and flash it:

```bash
xz -dc wardrive-pi-*.img.xz | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync
```

Or use the Makefile to build a fresh image:

```bash
# Requires: docker, git
make -C image build
make -C image flash DEV=/dev/sdX
```

### 2 — First boot

1. Insert the SD card and power on the Pi.
2. Plug in a USB drive labeled **WARDRIVE** for capture storage (optional but recommended).
3. Connect your device to the same network as the Pi, or connect to the AP fallback (see below).
4. Open **http://wardrive.local:8000** in a browser.

### 3 — WiFi AP fallback

If the Pi has no network uplink at boot, it brings up a WiFi access point:

| Setting | Value |
|---------|-------|
| SSID | `rpiwifi2_4ghz` |
| Passphrase | `wardrivelocal` |
| Gateway | `192.168.88.1` |
| Web UI | `http://192.168.88.1:8000` |

The SSID is intentionally generic so it does not appear in Kismet scan results as something interesting.

### 4 — SSH access

```
Host:     wardrive.local  (or 192.168.88.1 via AP)
User:     wardrive
Password: wardrive
```

---

## ESP32 firmware

### Flash (Arduino IDE)

1. Open `firmware/esp32_ble_scanner/esp32_ble_scanner.ino`
2. **Tools → Board → ESP32 Arduino → ESP32 Dev Module**
3. **Tools → Port → /dev/ttyUSB0** (or your port)
4. Click **Upload**

### Verify

Open Serial Monitor at **921600** baud. Expected output:

```
# esp32_ble_scanner ready
{"ms":1234,"addr":"AA:BB:CC:DD:EE:FF","addr_type":1,"rand":1,"rssi":-72,...}
```

Lines starting with `#` are status messages. Lines starting with `{` are BLE observations.

**Supported chips:**

| Chip | Bluetooth | Status |
|------|-----------|--------|
| ESP32 (original) | BT 4.2 / BLE | Supported |
| ESP32-S3 | BLE 5.0 | Supported |
| ESP32-C3 | BLE 5.0 | Supported |
| ESP32-S2 | None | Cannot be used — no Bluetooth |

---

## Configuration

Edit `/opt/wardrive/config/wardrive.conf` on the Pi (or before flashing). All settings use `KEY=value` syntax (no spaces around `=`).

```bash
# ── Storage ────────────────────────────────────────────────────────────────────
USB_DRIVE_LABEL=WARDRIVE          # auto-mount label; falls back to DATA_FALLBACK_DIR
DATA_FALLBACK_DIR=/opt/wardrive/data

# ── USB power budget ───────────────────────────────────────────────────────────
USB_BUDGET_MA=900                 # total mA budget; shed lowest-priority collectors if exceeded
# Collector priorities (lower = shed first)
PRIORITY_GPS=100
PRIORITY_WIFI=90
PRIORITY_ESP32=70
PRIORITY_RTL433=60
PRIORITY_WIDEBAND=50
PRIORITY_HACKRF=40

# ── Collectors ─────────────────────────────────────────────────────────────────
ENABLE_WIFI=true
WIFI_INTERFACE=wlan1
ENABLE_ESP32=true
ESP32_BAUD=921600
ENABLE_GPS=true
ENABLE_RTL433=true
ENABLE_WIDEBAND=true
SDR_MODE=rtl433                   # active RTL-SDR mode: rtl433 or wideband
ENABLE_HACKRF=false               # stub; enable when hardware arrives

# ── Supervisor ─────────────────────────────────────────────────────────────────
HANG_TIMEOUT=120                  # seconds with no output before a collector is killed
RESTART_MAX=10
RESTART_BACKOFF_INITIAL=2
RESTART_BACKOFF_MAX=60
RESTART_RESET_AFTER=300

# ── Access point fallback ──────────────────────────────────────────────────────
AP_SSID=rpiwifi2_4ghz
AP_PASSPHRASE=wardrivelocal
AP_CHANNEL=6
AP_IP=192.168.88.1

# ── Debug API ──────────────────────────────────────────────────────────────────
DEBUG_TOKEN=changeme-set-a-real-token
```

---

## Web UI

Open **http://wardrive.local:8000** from any device on your network.

| Page | Description |
|------|-------------|
| `/` | Dashboard — collector health, live device counts, session info |
| `/map` | Live map — all geolocated devices; GPS track overlay |
| `/analytics` | Charts — signal strength, device types, manufacturers, timeline |
| `/storage` | Disk usage, session management, delete sessions |

The dashboard shows live collector status and allows toggling the RTL-SDR mode (rtl_433 ↔ wideband) without restarting the session.

---

## Debug API

The debug API is protected by a Bearer token (`DEBUG_TOKEN` in `wardrive.conf`).

```bash
TOKEN="changeme-set-a-real-token"
BASE="http://wardrive.local:8000/api/debug"

# Collector status
curl -H "Authorization: Bearer $TOKEN" $BASE/collectors

# Tail a collector's log
curl -H "Authorization: Bearer $TOKEN" "$BASE/collector/wifi/log?lines=50"

# Restart a collector
curl -X POST -H "Authorization: Bearer $TOKEN" $BASE/collector/wifi/restart

# Switch SDR mode live
curl -X POST -H "Authorization: Bearer $TOKEN" $BASE/sdr/mode/wideband

# USB power state
curl -H "Authorization: Bearer $TOKEN" $BASE/usb

# Cycle a USB port
curl -X POST -H "Authorization: Bearer $TOKEN" $BASE/usb/port/1-1.2/cycle

# Database health
curl -H "Authorization: Bearer $TOKEN" $BASE/db/health

# Recent events
curl -H "Authorization: Bearer $TOKEN" $BASE/events

# System stats
curl -H "Authorization: Bearer $TOKEN" $BASE/system
```

WebSocket stream of all supervisor events:

```
ws://wardrive.local:8000/api/debug/ws?token=changeme-set-a-real-token
```

---

## Supervisor architecture

The Python asyncio supervisor (`supervisor/main.py`) manages all collectors:

- **Health state machine:** `STARTING → RUNNING → CRASHED → UNAVAILABLE → DISABLED`
- **Hang detection:** kills any collector that produces no output for `HANG_TIMEOUT` seconds
- **Exponential backoff:** waits 2 → 4 → 8 … → 60 s between restarts; resets if stable for 5 min
- **USB power budget:** sheds lowest-priority collector when combined draw would exceed `USB_BUDGET_MA`
- **Mutex groups:** `rtl_433` and wideband share the same dongle — only one runs at a time
- **udev monitoring:** re-enables a collector automatically when its USB device is re-plugged
- **systemd integration:** `Type=notify` + `WatchdogSec=60s` — systemd restarts the supervisor if it hangs

---

## Database

SQLite (`wardrive.db`) with WAL mode, stored on the WARDRIVE USB drive when present.

| Table | Description |
|-------|-------------|
| `sessions` | One row per capture session |
| `wifi_aps` | Unique APs (BSSID, SSID, encryption, channel, signal) |
| `wifi_clients` | Unique WiFi clients |
| `wifi_obs` | Per-packet WiFi observations with GPS |
| `rf_devices` | Unique rtl_433 devices |
| `rf_obs` | Per-observation rtl_433 records |
| `bt_devices` | Unique BLE devices |
| `bt_obs` | Per-advertisement BLE observations |
| `collector_events` | Supervisor state changes and crashes |
| `power_events` | USB power budget and shedding events |
| `hackrf_obs` | HackRF observations (schema stub) |

---

## Project structure

```
wardrive/
├── config/
│   └── wardrive.conf               # main configuration
├── supervisor/
│   ├── main.py                     # asyncio supervisor entry point (systemd Type=notify)
│   ├── registry.py                 # collector runner, watchdog, power budget, mutex
│   ├── config.py                   # config parser with SIGHUP reload
│   ├── event_bus.py                # async pub/sub event bus
│   ├── db.py                       # async SQLite (executor-offloaded, WAL)
│   ├── udev_monitor.py             # pyudev → asyncio bridge for USB hotplug
│   ├── power.py                    # uhubctl wrapper for USB port power cycling
│   └── collectors/
│       ├── base.py                 # CollectorPlugin dataclass + HealthState enum
│       ├── wifi.py                 # Kismet (MT7612U monitor-mode workaround)
│       ├── esp32.py                # ESP32 BLE serial reader
│       ├── gps.py                  # gpspipe NMEA collector
│       ├── rtlsdr.py               # rtl_433 + wideband (mutex group "rtlsdr")
│       └── hackrf.py               # HackRF One stub
├── processing/
│   ├── migrate.py                  # numbered SQL migration runner
│   └── migrations/
│       ├── 001_initial.sql         # core tables
│       ├── 002_supervisor_events.sql
│       └── 003_hackrf_stub.sql
├── webapp/
│   ├── main.py                     # FastAPI app (0.0.0.0:8000, CORS open for LAN)
│   ├── api/
│   │   └── debug.py                # /api/debug/* with Bearer token auth
│   ├── ws/
│   │   └── events.py               # WebSocket broadcast hub
│   ├── templates/                  # Jinja2 HTML pages
│   └── static/                     # CSS and JS assets
├── firmware/
│   └── esp32_ble_scanner/
│       └── esp32_ble_scanner.ino   # Arduino BLE scanner firmware
├── systemd/
│   ├── wardrive-supervisor.service
│   └── wardrive-webapp.service
├── image/
│   ├── Makefile                    # make build / make flash DEV=/dev/sdX
│   └── stage-wardrive/             # pi-gen custom stage
│       ├── 00-packages             # apt package list
│       ├── files/
│       │   ├── 99-wardrive-udev.rules
│       │   ├── hostapd.conf
│       │   ├── dnsmasq-wardrive.conf
│       │   └── wardrive-ap.service
│       └── 01-wardrive/
│           └── 00-run.sh           # chroot install script
└── .github/
    └── workflows/
        └── build-image.yml         # builds .img.xz on tag push; creates GitHub Release
```

---

## Building the image locally

```bash
# Requires: docker, git (~20–40 min first run)
make -C image build

# Flash to SD card (destructive!)
make -C image flash DEV=/dev/sdX
```

The GitHub Actions workflow (`.github/workflows/build-image.yml`) builds and publishes a release image automatically on every `v*` tag push.

---

## Troubleshooting

**Collectors not starting**
```bash
journalctl -u wardrive-supervisor -f
curl -H "Authorization: Bearer $TOKEN" http://wardrive.local:8000/api/debug/collectors
```

**USB device not detected**
```bash
dmesg | grep -E "tty|usb" | tail -20
lsusb
```

**WiFi monitor mode fails**
- Verify `WIFI_INTERFACE` in `wardrive.conf` matches your adapter (`ip link`)
- The supervisor runs `airmon-ng check kill` before starting Kismet

**RTL-SDR busy**
- Only one process can use the dongle at a time
- Switch modes via the web UI or debug API — the supervisor handles the handoff

**No GPS fix**
- GPS puck needs a clear sky view; may not acquire indoors
- Observations will have `lat`/`lon` = NULL until a fix is available
