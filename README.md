# warDrive

Passive RF capture system for a mobile Kali Linux laptop.
Three independent collectors run in parallel; raw data is immutable and enrichment is re-runnable.

| Collector | Hardware | Data |
|-----------|----------|------|
| Kismet    | WiFi adapter (monitor mode) | 802.11 APs + clients |
| rtl_433   | RTL-SDR dongle | 433/915 MHz ISM devices |
| Wideband SDR | RTL-SDR dongle | 600-6000 MHz spectrum scans + peak recordings |
| ESP32 BLE | ESP32 DevKit via USB | Bluetooth LE advertisements |

---

## Hardware requirements

### Laptop
- Kali Linux (Debian-based)
- USB ports: one for RTL-SDR, one for ESP32

### ESP32 BLE collector
**Chip variant matters.** Before flashing, confirm you have a supported chip:

```bash
# Plug in the ESP32 via USB, then:
esptool.py --port /dev/ttyUSB0 chip_id
```

| Chip | Bluetooth | Status |
|------|-----------|--------|
| ESP32 (original) | BT 4.2 / BLE | ✅ Supported |
| ESP32-S3 | BLE 5.0 | ✅ Supported |
| ESP32-S2 | None | ❌ Cannot be used for BLE |
| ESP32-C3 | BLE 5.0 | ✅ Supported (select correct board in IDE) |

The silver metal can on the top of the module is usually labeled. Look for "ESP32", "ESP32-S3", etc.

---

## First-time setup

```bash
git clone <this repo> ~/warDrive
cd ~/warDrive
bash setup.sh
# Log out and back in (or: newgrp dialout)
```

`setup.sh` installs Kismet, rtl-433, pyserial, and configures udev rules so the RTL-SDR and ESP32 are accessible without sudo.

---

## Flashing the ESP32 firmware

### Step 1 — Install Arduino IDE

Download from https://www.arduino.cc/en/software (Linux AppImage or tarball).

```bash
chmod +x arduino-ide_*.AppImage
./arduino-ide_*.AppImage   # launches the IDE
```

### Step 2 — Add the ESP32 board package

1. Open **File → Preferences**
2. In "Additional boards manager URLs", paste:
   ```
   https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
   ```
3. Click OK
4. Open **Tools → Board → Boards Manager**
5. Search for `esp32` by Espressif Systems
6. Install version **2.x** (latest stable)

### Step 3 — Open the firmware

**File → Open** → navigate to `firmware/esp32_ble_scanner/esp32_ble_scanner.ino`

### Step 4 — Select your board

**Tools → Board → ESP32 Arduino → ESP32 Dev Module**

(Use "ESP32-S3 Dev Module" if you have an S3.)

### Step 5 — Select the port

**Tools → Port → /dev/ttyUSB0** (or whichever port appeared when you plugged in the ESP32)

If no port appears:
```bash
ls /dev/ttyUSB* /dev/ttyACM*  # check what's connected
dmesg | tail -20               # look for USB serial messages
```

### Step 6 — Flash

Click **Upload** (→ arrow button). The IDE will compile and flash. You'll see:

```
Connecting........_____....
Chip is ESP32-D0WDQ6 (revision v1.0)
...
Hash of data verified.
Leaving...
Hard resetting via RTS pin...
```

### Step 7 — Verify

Open **Tools → Serial Monitor**, set baud to **921600**. You should see:

```
# esp32_ble_scanner ready
{"ms":1234,"addr":"AA:BB:CC:DD:EE:FF","addr_type":1,"rand":1,"rssi":-72,...}
{"ms":1289,"addr":"11:22:33:44:55:66","addr_type":1,"rand":1,"rssi":-81,...}
```

Lines starting with `#` are informational. Lines starting with `{` are BLE observations.

If you see garbled output, try baud rate **115200** in both the Serial Monitor and `wardrive.conf`.

---

## Configuration

Edit `config/wardrive.conf`:

```bash
# Enable/disable each collector
ENABLE_WIFI=false           # requires Kismet + monitor-mode adapter
ENABLE_SDR=false            # requires RTL-SDR dongle (narrow-band: 433/915 MHz)
ENABLE_WIDEBAND_SDR=false   # requires RTL-SDR dongle (wide-band: 600-6000 MHz)
ENABLE_ESP32=true           # requires flashed ESP32 on USB

# SDR options (narrow-band)
SDR_FREQUENCY_MHZ="433.92"  # ISM band center frequency

# Wideband SDR options (experimental)
WIDEBAND_FREQ_START_MHZ=600      # scan start
WIDEBAND_FREQ_END_MHZ=6000       # scan end
WIDEBAND_SCAN_STEP_MHZ=1         # frequency resolution
WIDEBAND_SCAN_TIME=10            # seconds per scan
WIDEBAND_LOCKUP_TIME=30          # seconds to record interesting signals
WIDEBAND_PEAK_THRESHOLD=-40      # dBm threshold for "interesting"

# Serial port — leave blank for auto-detect
ESP32_DEVICE=""        # or "/dev/ttyUSB0" to be explicit

# Baud rate — must match firmware SERIAL_BAUD
ESP32_BAUD=921600
```

---

## Running a capture session

```bash
cd ~/warDrive
bash wardrive.sh
```

Press **Ctrl-C** to stop all collectors cleanly. Session data is in:

```
capture/raw/<timestamp>_wardrive/
├── manifest.json          # session metadata
├── wifi/                  # Kismet output
├── sdr/
│   ├── rtl433.ndjson     # narrow-band ISM devices (if rtl_433 enabled)
│   ├── scan_*.csv        # spectrum scans (if wideband enabled)
│   ├── peaks_*.json      # detected peaks log (if wideband enabled)
│   └── lockup_*.wav      # recorded signals from peaks (if wideband enabled)
└── bt/
    └── esp32_ble.ndjson   # BLE observations (one JSON object per line)
```

---

## Enrichment

After a session (or any time), run:

```bash
cd ~/warDrive
python3 processing/enrich.py
```

Options:
```
--raw-dir PATH    path to capture/raw/ (default: auto-detected)
--db PATH         path to wardrive.db (default: processing/wardrive.db)
--session NAME    process only one session folder
--all             re-process sessions already in the DB
```

---

## Example queries

Open the database:
```bash
sqlite3 processing/wardrive.db
```

**Devices by manufacturer:**
```sql
SELECT manufacturer, COUNT(*) AS cnt
FROM bt_devices
WHERE manufacturer IS NOT NULL
GROUP BY manufacturer
ORDER BY cnt DESC;
```

**Apple Continuity device types seen:**
```sql
SELECT apple_continuity_type, COUNT(*) AS cnt
FROM bt_devices
WHERE apple_continuity_type IS NOT NULL
GROUP BY apple_continuity_type
ORDER BY cnt DESC;
```

**Strongest-signal devices (likely nearby):**
```sql
SELECT address, name, manufacturer, apple_continuity_type, max_rssi_dbm
FROM bt_devices
ORDER BY max_rssi_dbm DESC
LIMIT 20;
```

**Stable (public) vs randomized addresses:**
```sql
SELECT
    CASE is_randomized WHEN 0 THEN 'public/stable' ELSE 'randomized' END AS addr_type,
    COUNT(*) AS cnt
FROM bt_devices
GROUP BY is_randomized;
```

**All observations in a session, newest first:**
```sql
SELECT o.timestamp_utc, o.address, d.name, d.manufacturer, o.rssi_dbm
FROM bt_obs o
LEFT JOIN bt_devices d USING (address)
WHERE o.session_id = '20250101T120000Z_wardrive'
ORDER BY o.timestamp_utc DESC
LIMIT 50;
```

**Most frequently observed devices across all sessions:**
```sql
SELECT address, name, manufacturer, obs_count
FROM bt_devices
ORDER BY obs_count DESC
LIMIT 20;
```

---

## Project structure

```
warDrive/
├── wardrive.sh              # main launcher
├── setup.sh                 # one-shot dependency installer
├── config/
│   └── wardrive.conf        # editable settings
├── firmware/
│   └── esp32_ble_scanner/
│       └── esp32_ble_scanner.ino   # ESP32 firmware (Arduino IDE)
├── capture/
│   ├── raw/                 # immutable session folders
│   │   └── <ts>_<name>/
│   │       ├── manifest.json
│   │       ├── wifi/
│   │       ├── sdr/
│   │       └── bt/
│   └── logs/
└── processing/
    ├── esp32_reader.py      # host-side serial reader (spawned by wardrive.sh)
    ├── enrich.py            # post-capture enrichment
    └── wardrive.db          # output database
```

---

## Database schema (v1)

Schema version 1. BLE tables are additive — existing WiFi/SDR data is unaffected.

| Table | Description |
|-------|-------------|
| `sessions` | One row per capture session |
| `wifi_aps` | Unique APs seen across all sessions |
| `wifi_clients` | Unique WiFi clients |
| `wifi_obs` | Per-observation WiFi records |
| `rf_devices` | Unique rtl_433 devices |
| `rf_obs` | Per-observation SDR records |
| `bt_devices` | Unique BLE devices (deduplicated by BD_ADDR) |
| `bt_obs` | Per-advertisement BLE observations |
| `oui_lookup` | MAC OUI prefix → organization (shared by WiFi + BLE) |

`lat` / `lon` columns exist in all obs tables and are `NULL` until GPS integration.

---

## Quick Start: Wideband Spectrum Scanning

To search for signals across 600-6000 MHz with your omni antenna:

```bash
# 1. Enable wideband scanner in config
vi config/wardrive.conf
# Set: ENABLE_SDR=false
# Set: ENABLE_WIDEBAND_SDR=true

# 2. Optionally adjust scan parameters (default: 600-6000 MHz, 1 MHz steps, 10 sec scans)
# WIDEBAND_SCAN_STEP_MHZ=5       # faster scans (5 MHz resolution)
# WIDEBAND_PEAK_THRESHOLD=-35    # find weaker signals (-35 dBm vs default -40)

# 3. Start capture session
sudo bash wardrive.sh

# 4. While running, in another terminal:
tail -f capture/logs/*.log        # watch scan progress
python3 processing/view_scans.py capture/raw/*/sdr/scan_*.csv  # visualize live

# 5. After session, analyze results:
ls -lh capture/raw/<session>/sdr/   # see all scans and recordings
sqlite3 processing/wardrive.db "SELECT * FROM sessions ORDER BY started_at_utc DESC LIMIT 1;"
```

---

## Wideband SDR Scanner

The optional wideband SDR collector (`ENABLE_WIDEBAND_SDR=true`) runs a continuous spectrum reconnaissance loop:

1. **Scan phase:** `rtl_power` sweeps 600-6000 MHz (configurable range) in 1 MHz steps (configurable) for 10 seconds
2. **Peak detection:** Identifies frequencies with power above the threshold (default -40 dBm)
3. **Lock-on phase:** Records the top 5 peaks for 30 seconds each using `rtl_fm`
4. **Loop:** Waits 5 seconds and rescans

**Output files:**
- `scan_*.csv` — Full spectrum data from each scan pass (rtl_power format)
- `peaks_*.json` — Top 20 frequencies per scan with power levels
- `lockup_*.wav` — Audio/IQ recordings from each locked frequency

**Important:** Only one SDR collector can use the RTL-SDR at a time. Choose:
- `ENABLE_SDR=true, ENABLE_WIDEBAND_SDR=false` — narrow-band ISM monitoring (default)
- `ENABLE_SDR=false, ENABLE_WIDEBAND_SDR=true` — wide-band spectrum reconnaissance

**Configuration tuning:**
```bash
WIDEBAND_SCAN_STEP_MHZ=1       # 1 MHz = 6000 sweeps; 5 MHz = 1200 sweeps (faster)
WIDEBAND_SCAN_TIME=10          # 10 sec per scan; increase for cleaner data
WIDEBAND_PEAK_THRESHOLD=-40    # -40 dBm is aggressive; try -30 or -50
WIDEBAND_LOCKUP_TIME=30        # 30 sec per peak; increase for longer recordings
```

**Performance notes:**
- Narrowing the frequency range (e.g., 600-3000 MHz) significantly speeds up scans
- Wider step sizes (5-10 MHz) sacrifice granularity for speed
- Longer scan time produces cleaner spectrum data but delays peak detection

**Viewing scan results:**
```bash
# Quick preview of a spectrum scan
python3 processing/view_scans.py capture/raw/<session>/sdr/scan_*.csv

# View detected peaks
python3 processing/view_scans.py capture/raw/<session>/sdr/peaks_*.json

# Listen to a locked recording (if audio)
ffplay capture/raw/<session>/sdr/lockup_*.wav
```

**Modulation modes:** The scanner currently locks onto signals using wideband FM (`rtl_fm -M wbfm`). To capture other modulation types, you can modify the `launch_wideband_scanner()` function or extend the script:
- `wbfm` — wideband FM (broadcast radio, FM capture)
- `nbfm` — narrowband FM (two-way radio, PMR)
- `am` — amplitude modulation
- `usb`, `lsb` — single-sideband (amateur radio, SSB)
- `cw` — Morse code / continuous wave
- `raw` — raw IQ data (advanced processing)

**Known limitations:**
- Only records audio (wbfm mode); other modulation schemes may need `rtl_fm -M <mode>` changes
- Peak detection is simple threshold-based; strong broadband noise may cause false positives
- Cannot record multiple frequencies in parallel (one RTL-SDR dongle per session)

---

## Design notes

**Passive-only.** The ESP32 firmware uses `setActiveScan(false)`. No scan requests, no connections, no transmissions of any kind.

**Privacy-rotating MACs.** Modern phones rotate their BLE MAC every ~15 minutes. The `address_type` and `is_randomized` fields flag these; no de-anonymization is attempted.

**Timestamps.** The ESP32 clock is not synced to wall time. The firmware emits `"ms"` (millis since boot). `esp32_reader.py` injects `"ts"` (UTC ISO 8601) when each line arrives at the laptop, which has accurate system time.

**Apple Continuity.** The firmware decodes the leading type byte of Apple manufacturer data (ID 0x004C) to identify AirPods, iPhone, Watch, etc. No further parsing of the payload is done.

---

## Troubleshooting

**ESP32 not detected (`/dev/ttyUSB0` missing)**
```bash
dmesg | grep -E "tty|usb" | tail -20
lsusb   # look for CP2102, CH340, or Silicon Labs
```
Try a different USB cable — many cables are charge-only with no data lines.

**Permission denied on serial port**
```bash
groups   # confirm 'dialout' is listed
# If not: log out and back in, or run:
newgrp dialout
```

**Garbled output / no JSON lines**
- Baud rate mismatch. Check that `ESP32_BAUD` in `wardrive.conf` matches `SERIAL_BAUD` in the firmware.
- Try `115200` as a fallback.

**Very few devices seen**
- Confirm passive scan is working: `addr_type` values should include `1` (random).
- Dense environments (malls, downtown) will show hundreds of devices; quiet suburban areas may show only 5-20.

---

## Hardware roadmap

Planned additions (not yet implemented):
1. **GPS puck** — will populate `lat`/`lon` in all obs tables
2. **Cellular omni antenna** — passive cell tower mapping via RTL-SDR
3. **2.4 GHz Yagi** — directional WiFi triangulation
4. **Second RTL-SDR** — parallel band monitoring
5. **ESP32 fleet** — four additional boards at fixed properties (Vista, Oceanside, Fallbrook) via WiFi/MQTT
# Wardrive
