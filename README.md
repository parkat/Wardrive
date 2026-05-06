# warDrive

Passive RF capture system for a mobile Kali Linux laptop. Four independent collectors run in parallel with automatic crash recovery; raw data is immutable and enrichment is re-runnable at any time.

| Collector | Hardware | Data |
|-----------|----------|------|
| Kismet | WiFi adapter (monitor mode) | 802.11 APs + clients |
| rtl_433 | RTL-SDR dongle | 433/915 MHz ISM devices |
| Wideband SDR | RTL-SDR dongle | 600–6000 MHz spectrum scans + peak recordings |
| ESP32 BLE | ESP32 DevKit via USB | Bluetooth LE advertisements |
| GPS | u-blox or any NMEA puck | Coordinates injected into all observations |

GPS coordinates are injected in real time into BLE records by `esp32_reader.py` and interpolated into WiFi/RF observations during enrichment. The RTL-SDR narrow-band (rtl_433) and wideband scanners are mutually exclusive — both require the same dongle.

---

## Hardware requirements

- Kali Linux laptop (Debian-based), USB ports for RTL-SDR, ESP32, and GPS puck
- RTL-SDR dongle (RTL2832U/RTL2838)
- ESP32 DevKit (original, S3, or C3 — not S2, which has no Bluetooth)
- GPS puck with NMEA output (u-blox 7 tested; appears as `/dev/ttyACM0`)
- WiFi adapter that supports monitor mode (e.g. Alfa AWUS036ACM with MT7612U)

**Confirm your ESP32 chip before flashing:**

```bash
esptool.py --port /dev/ttyUSB0 chip_id
```

| Chip | Bluetooth | Status |
|------|-----------|--------|
| ESP32 (original) | BT 4.2 / BLE | Supported |
| ESP32-S3 | BLE 5.0 | Supported |
| ESP32-C3 | BLE 5.0 | Supported |
| ESP32-S2 | None | Cannot be used for BLE |

---

## First-time setup

```bash
git clone <this repo> ~/warDrive
cd ~/warDrive
bash setup.sh
# Log out and back in (or: newgrp dialout)
```

`setup.sh` installs the following system packages: `kismet`, `rtl-sdr`, `rtl-433`, `python3`, `python3-pip`, `screen`. It also installs the `pyserial` Python package, configures RTL-SDR and ESP32 udev rules, and adds your user to the `dialout` and `plugdev` groups.

After running setup, the webapp requires additional Python packages:

```bash
pip3 install --break-system-packages fastapi uvicorn
```

---

## Flashing the ESP32 firmware

### Step 1 — Install Arduino IDE

Download from https://www.arduino.cc/en/software (Linux AppImage or tarball).

```bash
chmod +x arduino-ide_*.AppImage
./arduino-ide_*.AppImage
```

### Step 2 — Add the ESP32 board package

1. Open **File → Preferences**
2. In "Additional boards manager URLs", paste:
   ```
   https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
   ```
3. Open **Tools → Board → Boards Manager**, search `esp32` by Espressif Systems, install version **2.x**

### Step 3 — Flash

1. Open **File → Open** → `firmware/esp32_ble_scanner/esp32_ble_scanner.ino`
2. **Tools → Board → ESP32 Arduino → ESP32 Dev Module** (or S3 Dev Module for S3)
3. **Tools → Port → /dev/ttyUSB0** (adjust as needed)
4. Click **Upload**

### Step 4 — Verify

Open **Tools → Serial Monitor**, set baud to **921600**. Expected output:

```
# esp32_ble_scanner ready
{"ms":1234,"addr":"AA:BB:CC:DD:EE:FF","addr_type":1,"rand":1,"rssi":-72,...}
```

Lines starting with `#` are informational. Lines starting with `{` are BLE observations.

If output is garbled, verify `ESP32_BAUD` in `wardrive.conf` matches `SERIAL_BAUD` in the firmware. Try `115200` as a fallback.

---

## Configuration

Edit `config/wardrive.conf`. All options use Bash syntax (no spaces around `=`).

```bash
# ── Session ────────────────────────────────────────────────────────────────────
SESSION_LABEL="wardrive"    # appended to the timestamp folder name
KEEP_AWAKE=true             # block sleep/lid-close during capture (systemd-inhibit)

# ── Capture storage location ───────────────────────────────────────────────────
# Leave blank to use the default (project directory / capture/).
# Set to an absolute path to capture sessions directly to external storage, e.g.:
#   CAPTURE_BASE_DIR="/mnt/usb_drive/wardrive"
# The webapp reads this setting at runtime. wardrive.sh must be restarted to
# write new sessions to the new location. The raw/ and logs/ subdirectories are
# created automatically on first run.
CAPTURE_BASE_DIR=""

# ── WiFi (Kismet) ──────────────────────────────────────────────────────────────
ENABLE_WIFI=true
WIFI_INTERFACE="wlan1"      # set to your monitor-mode adapter interface

# ── SDR narrow-band (rtl_433) ─────────────────────────────────────────────────
ENABLE_SDR=true
SDR_FREQUENCY_MHZ="433.92"  # alternatives: 315, 868, 915

# ── SDR wideband spectrum scanner ─────────────────────────────────────────────
# Cannot run at the same time as ENABLE_SDR (same dongle).
ENABLE_WIDEBAND_SDR=false
WIDEBAND_FREQ_START_MHZ=600
WIDEBAND_FREQ_END_MHZ=6000
WIDEBAND_SCAN_STEP_MHZ=1        # MHz resolution (smaller = slower, more detail)
WIDEBAND_SCAN_TIME=10           # seconds per scan pass
WIDEBAND_LOCKUP_TIME=30         # seconds to record at each peak
WIDEBAND_PEAK_THRESHOLD=-40     # dBm — signals above this get recorded

# ── Online enrichment (optional) ──────────────────────────────────────────────
# Wigle.net: enables BLE + WiFi AP sighting lookup. Get your token at
# wigle.net → Account → API Token (base64 of "user:apiToken").
WIGLE_API_KEY="YOUR_WIGLE_API_TOKEN_HERE"

# deflock.me ALPR database (public, no key required).
# Enrichment queries this for known ALPR cameras near GPS fixes.
ENABLE_DEFLOCK=true

# ── ESP32 BLE ─────────────────────────────────────────────────────────────────
ENABLE_ESP32=true
ESP32_DEVICE=""             # leave blank to auto-detect /dev/ttyUSB0..2, ttyACM1..2
ESP32_BAUD=115200           # must match SERIAL_BAUD in firmware; try 921600 for faster

# ── GPS ───────────────────────────────────────────────────────────────────────
ENABLE_GPS=true
GPS_DEVICE=/dev/ttyACM0     # u-blox 7 default; adjust if your puck is on a different port
GPS_WAIT_FIX=30             # seconds to wait for initial fix before starting
GPS_MIN_SATS=4              # minimum satellites for a valid position
```

**Supervisor tunables** (override in `wardrive.conf` if needed):
```bash
RESTART_MAX=10              # give up after this many consecutive crashes
RESTART_BACKOFF_INITIAL=2   # seconds before first restart
RESTART_BACKOFF_MAX=60      # cap on exponential backoff
RESTART_RESET_AFTER=300     # reset crash counter if a collector ran stably this long
```

---

## Running a capture session

```bash
sudo python3 ~/warDrive/wardrive_ui.py
```

This launches the interactive terminal UI, which starts `wardrive.sh` in the background and displays a multi-panel dashboard:

```
┌ warDrive │ Session: 20260427T172720Z_wardrive │ 00:45:23 | 14:32:01 ┐
├──────────────────────────────────────────────────────────────────────┤
│ LIVE DATA                        │ COMMANDS                          │
│  Duration:       00:45:23        │  q/quit      stop session & exit  │
│                                  │  webapp on   start web explorer   │
│  Device Counts                   │  webapp off  stop web explorer    │
│  WiFi APs:       247             │                                   │
│  BLE Devices:    89              │  stop wifi   stop WiFi collector  │
│  RF Devices:     12              │  stop sdr    stop SDR collector   │
│                                  │  stop ble    stop BLE collector   │
│  Collectors                      │  stop gps    stop GPS collector   │
│   ✓  WiFi    (Kismet)            │                                   │
│   ✓  SDR     (rtl_433)          │  start wifi  restart WiFi         │
│   ✗  Wideband SDR               │  start sdr   restart SDR          │
│   ✓  BLE     (ESP32)            │  start ble   restart BLE          │
│   ✓  GPS     (gpspipe)          │  start gps   restart GPS          │
│                                  │                                   │
│  Web Explorer                    │  enrich      run post-enrichment  │
│   ✓  http://localhost:8000       │  ↑ / ↓       command history      │
│                                  │  PgUp / PgDn scroll log           │
├──────────────────────────────────────────────────────────────────────┤
│ LOG                                                                  │
│ [wardrive] Session: 20260427T172720Z_wardrive                        │
│ [wifi] Starting Kismet on wlan1mon (under supervisor)               │
│ [heartbeat] 4/4 supervisors alive — 00:45:00Z | GPS: 8 sats        │
├──────────────────────────────────────────────────────────────────────┤
│ CMD> _                                                               │
└──────────────────────────────────────────────────────────────────────┘
```

**Panels:**
- **LIVE DATA** — session duration, device counts (updated from the DB every 5 s), per-collector run status (read from PID files), web explorer status
- **COMMANDS** — full command reference always visible
- **LOG** — scrolling live output from `wardrive.sh`; scroll with PgUp/PgDn
- **CMD>** — interactive command prompt with ↑/↓ history

**Starting/stopping the web explorer from the TUI:**
```
CMD> webapp on     # start the explorer at http://localhost:8000
CMD> webapp off    # stop it
```

**Pausing and resuming individual collectors:**
```
CMD> stop wifi     # SIGTERM the Kismet supervisor (Kismet stops cleanly)
CMD> start wifi    # restart it without ending the session
```

`wardrive.sh` itself runs pre-flight checks (device detection, airmon-ng cleanup, GPS fix wait), then starts all enabled collectors under supervisors that auto-restart on crash with exponential backoff. `systemd-inhibit` blocks sleep and lid-close for the duration.

Type `q` or `quit` at the CMD prompt to stop the session cleanly. All collectors are sent SIGTERM, the WiFi monitor interface is torn down, and the session manifest is finalized.

**Session output:**

```
capture/raw/<timestamp>_wardrive/
├── manifest.json          # session metadata (start/end, enabled collectors)
├── wifi/
│   └── Kismet-*.kismet    # Kismet SQLite database
├── sdr/
│   ├── *_rtl433.ndjson    # narrow-band ISM device records (if rtl_433 enabled)
│   ├── scan_*.csv         # spectrum scan data (if wideband enabled)
│   ├── peaks_*.json       # top-20 peaks per scan pass (if wideband enabled)
│   └── lockup_*.wav       # wbfm recordings at peak frequencies (if wideband enabled)
├── bt/
│   └── esp32_ble.ndjson   # BLE observations, one JSON object per line
└── gps/
    └── nmea.log           # raw NMEA sentences from gpspipe (if GPS enabled)
```

**Heartbeat:** every 60 seconds, a log line reports how many collector supervisors are still alive and the current GPS fix (if enabled).

---

## Enrichment pipeline

After a session (or any time), run enrichment to load raw data into SQLite and apply online lookups:

```bash
python3 ~/warDrive/processing/enrich.py
```

**What it does:**

1. Reads each session folder under `capture/raw/` that has not yet been processed
2. Parses the GPS NMEA log and builds a timeline of fixes
3. Loads Kismet WiFi data (APs + clients + packet observations) with GPS interpolation
4. Loads rtl_433 NDJSON (RF devices + observations) with GPS interpolation
5. Loads ESP32 BLE NDJSON (devices + observations; GPS already embedded per-record)
6. For each device, runs online enrichment (results cached in `enrichment_cache` — each address/OUI is only fetched once across all runs):
   - **macvendors.com** — OUI → manufacturer name (free, no key)
   - **Bluetooth Numbers Database** (GitHub) — UUID → service name, appearance value → human label (no key)
   - **Wigle.net** — global sighting history for BLE devices and WiFi APs (API key required)
7. Classifies each device into a type: Phone, Wearable, Camera, ALPR, Flock Camera, Router/AP, IoT sensor, etc.

**Options:**

```
--raw-dir PATH    path to capture/raw/ (default: auto-detected relative to script)
--db PATH         path to wardrive.db (default: processing/wardrive.db)
--session NAME    process only one named session folder
--all             re-process sessions already in the DB
--offline         skip all network lookups; use local data only
--verbose / -v    show per-device enrichment detail (OUI source, Wigle outcome)
```

**Circuit breakers:** if macvendors.com or Wigle returns HTTP 401 or 429, that service is disabled for the rest of the run to avoid hammering rate limits. A future run will retry.

---

## Web dashboard

### Starting the webapp

The preferred way is from the TUI prompt during a session:
```
CMD> webapp on    # starts on http://localhost:8000
CMD> webapp off   # stops it
```

Or start it independently:
```bash
# Foreground:
bash ~/warDrive/webapp/run.sh

# Background:
bash ~/warDrive/webapp/manage.sh start
bash ~/warDrive/webapp/manage.sh stop
```

Open **http://127.0.0.1:8000** in a browser. The webapp is a FastAPI application (`webapp/main.py`) served by uvicorn on `127.0.0.1:8000`. It reads from `processing/wardrive.db` and from raw capture files directly. It is intended for reviewing data after a drive — the live view has been removed in favour of the terminal TUI.

### Pages

| URL | Description |
|-----|-------------|
| `/` | **Explorer** — browse devices by session; filter by table, vendor, RSSI, date, device type |
| `/dashboard` | **Dashboard** — aggregate stats: total unique devices, strongest device, busiest session, WiFi encryption breakdown, geographic bounds, last capture time |
| `/map` | **Map** — Leaflet map showing all geolocated BLE, WiFi, and RF devices; GPS track overlay from NMEA log |
| `/analytics` | **Analytics** — Chart.js visualizations: signal strength distributions (BLE + WiFi), device type breakdown, devices per hour timeline, top manufacturers, per-session comparison |
| `/report` | **Report** — session report view (note: the `/api/report/summary` endpoint is currently disabled; use the Explorer with filters instead) |
| `/storage` | **Storage** — disk usage overview (database size, capture files, filesystem free space); session management table with per-session device counts, duration, and disk size; individual and bulk session deletion (removes raw files + DB records); capture storage path configuration |

The header on every page shows live collector status (which collectors are enabled/running) and a start/stop button for `wardrive.sh`. Starting from the UI requires sudoers configuration:

```bash
# Add to /etc/sudoers.d/wardrive:
parkat ALL=(root) NOPASSWD: /home/parkat/warDrive/wardrive.sh
parkat ALL=(root) NOPASSWD: /usr/bin/kill
```

---

## Wideband SDR scanner

The wideband collector (`ENABLE_WIDEBAND_SDR=true`) runs a continuous scan-lock-record loop using `rtl_power` and `rtl_fm`:

1. **Scan:** `rtl_power` sweeps the configured range (default 600–6000 MHz) at the configured step and time
2. **Peak detection:** frequencies with average power above `WIDEBAND_PEAK_THRESHOLD` are sorted by strength
3. **Record:** the top 5 peaks are each recorded for `WIDEBAND_LOCKUP_TIME` seconds using `rtl_fm -M wbfm`
4. **Repeat:** 5-second pause, then rescan

**Important:** `ENABLE_SDR` and `ENABLE_WIDEBAND_SDR` are mutually exclusive — both use the same RTL-SDR dongle.

**Output files per session:**
- `scan_<ts>_#<n>.csv` — full spectrum data (rtl_power CSV format)
- `peaks_<ts>_#<n>.json` — top 20 peaks (freq_mhz, power_dbm)
- `lockup_<ts>_<freq>mhz.wav` — wbfm recording at that frequency

**Tuning:**
```bash
WIDEBAND_SCAN_STEP_MHZ=5    # faster scans, 5 MHz granularity
WIDEBAND_SCAN_TIME=20       # longer scan = cleaner data
WIDEBAND_PEAK_THRESHOLD=-35 # less aggressive threshold (fewer false positives)
WIDEBAND_LOCKUP_TIME=60     # longer recordings
# Narrow the range to speed things up:
WIDEBAND_FREQ_START_MHZ=700
WIDEBAND_FREQ_END_MHZ=2700
```

The lock-on mode is hardcoded to wideband FM (`rtl_fm -M wbfm`). To capture other modulations, modify `record_frequency()` in `processing/rtl_wideband.py`.

---

## Database schema (v1)

Location: `processing/wardrive.db` (SQLite, WAL mode)

| Table | Description |
|-------|-------------|
| `sessions` | One row per capture session; start/end times, hostname, which collectors were active |
| `wifi_aps` | Unique APs; BSSID, SSID, encryption, channel, max signal, manufacturer, Wigle location, device type, ALPR source flag |
| `wifi_clients` | Unique WiFi clients seen by Kismet |
| `wifi_obs` | Per-packet WiFi observations with timestamp, BSSID, signal, lat/lon |
| `rf_devices` | Unique rtl_433 devices (model, protocol, frequency, max RSSI/SNR) |
| `rf_obs` | Per-observation rtl_433 records with raw JSON and lat/lon |
| `bt_devices` | Unique BLE devices; address type, name, manufacturer, appearance, services, Apple Continuity type, Wigle history, device type |
| `bt_obs` | Per-advertisement BLE observations with RSSI and lat/lon |
| `oui_lookup` | MAC OUI prefix → organization (shared by WiFi + BLE) |
| `enrichment_cache` | Persistent cache for macvendors, Wigle, and Bluetooth Numbers DB lookups — each (source, key) is fetched at most once |
| `schema_meta` | Schema version tracking |

`lat`/`lon` in observation tables come from GPS. They are `NULL` when no GPS fix was available at the time of observation.

---

## Example SQL queries

```bash
sqlite3 processing/wardrive.db
```

**Devices by manufacturer:**
```sql
SELECT manufacturer, COUNT(*) AS cnt
FROM bt_devices
WHERE manufacturer IS NOT NULL
GROUP BY manufacturer ORDER BY cnt DESC;
```

**Apple Continuity device types:**
```sql
SELECT apple_continuity_type, COUNT(*) AS cnt
FROM bt_devices
WHERE apple_continuity_type IS NOT NULL
GROUP BY apple_continuity_type ORDER BY cnt DESC;
```

**Strongest-signal devices (likely nearby):**
```sql
SELECT address, name, manufacturer, device_type, max_rssi_dbm
FROM bt_devices ORDER BY max_rssi_dbm DESC LIMIT 20;
```

**Randomized vs stable addresses:**
```sql
SELECT
    CASE is_randomized WHEN 0 THEN 'public/stable' ELSE 'randomized' END AS addr_type,
    COUNT(*) AS cnt
FROM bt_devices GROUP BY is_randomized;
```

**All WiFi APs flagged as ALPR cameras:**
```sql
SELECT bssid, ssid, manufacturer, device_type, alpr_source
FROM wifi_aps WHERE device_type IN ('ALPR', 'Flock Camera');
```

**Observations in a session, newest first:**
```sql
SELECT o.timestamp_utc, o.address, d.name, d.manufacturer, o.rssi_dbm, o.lat, o.lon
FROM bt_obs o LEFT JOIN bt_devices d USING (address)
WHERE o.session_id = '20260502T200514Z_wardrive'
ORDER BY o.timestamp_utc DESC LIMIT 50;
```

---

## Project structure

```
warDrive/
├── wardrive_ui.py                  # interactive TUI — entry point (sudo python3 wardrive_ui.py)
├── wardrive.sh                     # collector launcher; spawns and supervises all collectors
├── setup.sh                        # one-shot dependency installer
├── config/
│   └── wardrive.conf               # editable settings (sourced by wardrive.sh)
├── firmware/
│   └── esp32_ble_scanner/
│       └── esp32_ble_scanner.ino   # ESP32 BLE scanner firmware (Arduino IDE)
├── processing/
│   ├── esp32_reader.py             # serial reader: ESP32 → NDJSON + GPS injection
│   ├── enrich.py                   # post-capture enrichment pipeline
│   ├── rtl_wideband.py             # wideband spectrum scanner (rtl_power + rtl_fm)
│   └── wardrive.db                 # SQLite output database
├── capture/
│   ├── raw/                        # immutable session folders
│   │   └── <ts>_wardrive/
│   │       ├── manifest.json
│   │       ├── wifi/
│   │       ├── sdr/
│   │       ├── bt/
│   │       └── gps/
│   ├── logs/                       # per-session wardrive.sh logs (non-TUI mode only)
│   ├── pids/                       # per-collector supervisor PID files (runtime, gitignored)
│   └── wardrive.cmd                # TUI→wardrive.sh command channel (runtime, gitignored)
└── webapp/
    ├── main.py                     # FastAPI application (post-run data explorer)
    ├── run.sh                      # foreground launcher
    ├── manage.sh                   # background start/stop/status/restart
    ├── static/                     # CSS and JS assets
    └── templates/                  # HTML pages (index, dashboard, map, analytics, report, storage)
```

---

## Design notes

**Passive-only.** The ESP32 firmware uses `setActiveScan(false)`. No scan requests, no connections, no transmissions.

**Privacy-rotating MACs.** Modern phones rotate their BLE MAC every ~15 minutes. The `address_type` and `is_randomized` fields in `bt_devices` flag these. OUI lookups are skipped for randomized MACs (the OUI is meaningless). No de-anonymization is attempted.

**Timestamps.** The ESP32 does not have a real-time clock. The firmware emits `"ms"` (milliseconds since boot). `esp32_reader.py` injects `"ts"` (UTC ISO 8601) when each line arrives on the laptop, which has accurate system time. GPS coordinates are injected at the same time from the live gpsd connection.

**Apple Continuity.** The firmware decodes the leading type byte of Apple manufacturer data (company ID 0x004C) to identify AirPods, iPhone, Find My trackers, etc. No further payload parsing is done.

**Enrichment caching.** All online lookups are stored in `enrichment_cache` within `wardrive.db`. Re-running enrichment on the same sessions does not re-hit the network. The macvendors.com free tier allows ~1 req/s and 1000 req/day; Wigle allows ~10 req/min.

**Raw data is immutable.** `capture/raw/` session folders are never modified by `enrich.py`. Enrichment can always be re-run from scratch with `--all`.

---

## Troubleshooting

**ESP32 not detected (`/dev/ttyUSB0` missing)**
```bash
dmesg | grep -E "tty|usb" | tail -20
lsusb   # look for CP2102, CH340, or Silicon Labs
```
Try a different cable — many USB cables are charge-only with no data lines.

**Permission denied on serial port**
```bash
groups   # confirm 'dialout' is listed
newgrp dialout   # activate without logging out
```

**Garbled BLE output / no JSON lines**
- Baud rate mismatch: verify `ESP32_BAUD` in `wardrive.conf` matches `SERIAL_BAUD` in the firmware
- Try `115200` as a fallback

**No GPS fix**
- Ensure the puck has a clear sky view; indoors it may never acquire
- `GPS_WAIT_FIX` controls how long `wardrive.sh` waits before starting without coordinates
- Observations from sessions with no fix will have `lat`/`lon` = NULL in the DB

**RTL-SDR not found or busy**
- Only one process can use the dongle at a time
- The pre-flight check kills known consumers (readsb, dump1090, gqrx, etc.) automatically
- If another process is holding it, the script prompts you to kill it

**WiFi monitor mode fails**
- Check that `WIFI_INTERFACE` in `wardrive.conf` matches your adapter (`ip link` or `iw dev`)
- The pre-flight runs `airmon-ng check kill` to stop NetworkManager and wpa_supplicant

---

## Hardware roadmap

Planned additions (not yet implemented):
1. **Second RTL-SDR** — parallel narrow-band + wideband monitoring
2. **Cellular omni antenna** — passive cell tower mapping
3. **2.4 GHz Yagi** — directional WiFi triangulation
4. **ESP32 fleet** — additional boards at fixed properties via WiFi/MQTT

---

## Improvement backlog

Output of a full multi-agent code audit run against the codebase. Items are prioritized by impact.

### Software

| Priority | Item |
|----------|------|
| HIGH | **Materialize session stats** — the dashboard runs `COUNT(DISTINCT)` subqueries per session on every load. Write a `session_stats` summary table at the end of each `enrich.py` run instead. Zero query-contract changes required. |
| HIGH | **Log rotation** — session logs (`tee -a SESSION_LOG`) have no size cap. Add a `logrotate` config at `/etc/logrotate.d/wardrive` rotating `capture/logs/*.log` at 50 MB with `compress`. Without this a multi-hour drive can fill an SD card. |
| MEDIUM | **Populate `oui_lookup` from IEEE MA-L CSV** — the table is defined in the schema but never loaded. A one-time loader that bulk-inserts the IEEE `oui.csv` (public domain) eliminates the per-lookup HTTP dependency on macvendors.com and works fully offline. |
| MEDIUM | **systemd units per collector** — replace the bash supervisor loop in `wardrive.sh` with individual `.service` files (`Restart=on-failure`, `RestartSec=`, `StartLimitIntervalSec=`). Gives free journal logging, restart counters, `systemctl status`, and fixes the manifest update race (manifest currently not updated when collectors crash mid-session). `wardrive.sh` becomes a thin orchestrator that starts a `wardrive-session.target`. |
| MEDIUM | **Post-session auto-enrichment** — a `systemd.path` unit watching `capture/raw/` for new `manifest.json` files can trigger `enrich.py` automatically when a session ends. Eliminates the manual enrichment step. |
| MEDIUM | **pytest suite** — zero test coverage currently. Highest-value targets: GPS interpolation unit tests, timestamp normalization (`Z` vs `+00:00`), schema idempotency (re-running `enrich.py` on a fixture session), and one integration test that starts uvicorn and hits `/api/status`. |
| LOW | **Vendor Chart.js locally** — copy `chart.umd.min.js` into `webapp/static/` and update the `<script>` tag in `analytics.html`. Eliminates CDN dependency for offline field use. |
| LOW | **Implement `/api/report/summary`** — the report page currently shows a "disabled" notice. The schema has all needed data; a single query joining the device tables with per-session aggregations would make the report page functional. |

### Hardware

| Priority | Item |
|----------|------|
| HIGH | **u-blox M9N or M10 GPS module** with a Tallysman TW4721 patch antenna. The M9N provides a 10 Hz fix rate vs the typical 1 Hz of cheap pucks, which dramatically improves GPS interpolation accuracy at driving speed. Multi-constellation (GPS + GLONASS + Galileo + BeiDou) cuts cold-start acquisition time. Currently ~31 % of WiFi observations and ~25 % of BLE observations have null GPS coordinates. |
| HIGH | **RTL-SDR V4 (R828D tuner)** — direct swap, same driver. Extends usable range to ~2.4 GHz with meaningful sensitivity improvement above 1.6 GHz (where the V3 R820T rolls off), a built-in bias tee for powered LNAs, and a hardware FM notch filter that reduces intermodulation from broadcast stations. |
| MEDIUM | **Second RTL-SDR dongle (~$30)** — run `rtl_433` on a fixed frequency *and* `rtl_power` wideband sweeping simultaneously instead of the current time-sharing approach. Two USB dongles, split by device index (`-d 0` / `-d 1`). |
| MEDIUM | **nRF52840 USB sniffer** (e.g. Makerdiary nRF52840 MDK USB Dongle, ~$20) running Sniffle firmware. The ESP32 BLE stack sees only advertising PDUs directed at or broadcast near it. The nRF52840 supports true promiscuous capture of all advertising PDUs on all three primary advertising channels with proper channel rotation, substantially increasing BLE observation count. |
| LOW | **Filtered LNA for the RTL-SDR** (e.g. Nooelec LaNA or SAWbird+ 915) — adds ~15 dB of in-band sensitivity and reduces intermodulation from strong out-of-band signals (FM broadcast, cellular). Plug-and-play with the V4's built-in bias tee. |
| LOW | **Cellular modem HAT** (e.g. Waveshare SIM7600G-H) — enables real-time Wigle enrichment during capture, remote SSH without a hotspot, and post-session rsync to a home server. Pairs with the `POST_SESSION_SYNC` operational improvement below. |

### Runtime / Operations

| Priority | Item |
|----------|------|
| HIGH | **WAL checkpoint at enrichment end** — already implemented: `PRAGMA wal_checkpoint(TRUNCATE)` is now called at the end of each `enrich.py` run. Without this, WAL frames accumulate across runs and degrade read performance. |
| MEDIUM | **Scheduled VACUUM** — after bulk enrichment inserts, SQLite page utilization degrades. Add `PRAGMA incremental_vacuum(1000)` to `enrich.py` post-import and a weekly cron job running `PRAGMA vacuum` on the database. Extends SD card life meaningfully on a 4 GB card. |
| MEDIUM | **`rsync` post-session backup** — add an optional `POST_SESSION_SYNC` variable to `wardrive.conf`. If set to an SSH target (`user@host:path`), trigger `rsync -az capture/raw/ $POST_SESSION_SYNC` after session teardown for automatic off-device backup. |
| LOW | **`wardrive_ui.py` log persistence** — when running via the TUI (`--no-tee` mode), `wardrive.sh` stdout is not written to `capture/logs/`. Add an option to the TUI to mirror the log panel to a file for post-session review. |
