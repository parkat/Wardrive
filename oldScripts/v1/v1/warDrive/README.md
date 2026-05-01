# Wardrive Capture System

A modular wardriving capture pipeline for Kali Linux. Designed to grow with
your hardware — start with the Alfa + RTL-SDR you have today, add GPS,
ESP32 sensors, and directional antennas as they arrive.

## What this does

Spawns parallel passive collectors that record every observable RF signal
to disk in raw form, then enriches the captures into a queryable SQLite
database that a future web app will read.

**Today it supports:** Alfa USB WiFi (monitor mode via Kismet) +
RTL-SDR (rtl_433 multi-frequency hopping).

**Stubs in place for:** GPS puck (gpsd), ESP32 distributed sensors,
directional antenna profiles.

## Architecture

```
wardriver/
├── wardrive.sh          ← main launcher, spawns collectors in parallel
├── setup.sh             ← one-time dependency installer
├── config/
│   └── wardrive.conf    ← edit interface names, SDR frequencies, etc.
├── capture/
│   ├── raw/             ← one folder per session, NEVER modified after capture
│   │   └── 20260101T120000Z_<name>/
│   │       ├── manifest.json
│   │       ├── wifi/    ← Kismet output (.kismet, .pcapng, JSON)
│   │       └── sdr/     ← rtl_433 NDJSON
│   └── logs/            ← session-level log files
├── processing/
│   ├── enrich.py        ← reads raw, writes wardrive.db
│   └── wardrive.db      ← SQLite, queryable by future web app
└── docs/                ← reference notes
```

## Design principles

1. **Raw captures are immutable.** Enrichment reads from `capture/raw/` and
   writes elsewhere. Re-run enrichment any time without losing data.
2. **Session-based.** Every wardrive run is a separate folder. Easy to
   delete, share, or analyze in isolation.
3. **Schema-versioned.** The DB carries a `schema_version` value so the
   web app can adapt as the schema grows.
4. **Collectors are independent.** If rtl_433 dies, Kismet keeps running.
5. **Receive-only by default.** No injection, no transmissions.

## Setup

```bash
sudo ./setup.sh
sudo reboot   # required for the RTL-SDR kernel-driver blacklist
```

After reboot, plug in your hardware and verify:

```bash
ip -br link              # find your Alfa interface (e.g. wlan1)
rtl_test -t              # confirm the RTL-SDR is detected
```

Edit `config/wardrive.conf` with the right `WIFI_IFACE` value.

## Running a session

```bash
sudo ./wardrive.sh --name neighborhood-pass-1
```

Press Ctrl-C to end the session cleanly. The script restores your WiFi
interface to managed mode on exit.

Useful flags:

```
--name <str>      Tag the session folder with a label
--no-wifi         Skip the Alfa collector (e.g. SDR-only run)
--no-sdr          Skip the RTL-SDR collector
--iface <name>    Override the WiFi interface from config
```

## Processing captures

After a session, enrich it into the database:

```bash
./processing/enrich.py capture/raw/20260101T120000Z_neighborhood-pass-1
```

Or process every session at once (re-runnable, idempotent):

```bash
./processing/enrich.py --all
```

The result is `processing/wardrive.db`, which any tool can query.

## Quick exploration with sqlite3

```bash
sqlite3 processing/wardrive.db

-- Top 20 strongest APs you've seen
SELECT last_ssid, bssid, vendor, max_signal_dbm
FROM wifi_aps
ORDER BY max_signal_dbm DESC
LIMIT 20;

-- Most-active RF devices (TPMS, weather stations, etc.)
SELECT model, device_id, obs_count, typical_freq_mhz
FROM rf_devices
ORDER BY obs_count DESC
LIMIT 20;

-- Session summary
SELECT session_id, started_at_utc,
       (SELECT COUNT(*) FROM wifi_obs WHERE session_id = s.session_id) AS wifi_obs,
       (SELECT COUNT(*) FROM rf_obs   WHERE session_id = s.session_id) AS rf_obs
FROM sessions s
ORDER BY started_at_utc DESC;
```

## Hardware roadmap

Listed in order of impact for a wardriving setup:

### 1. GPS puck (highest priority — incoming)
Without coordinates, every observation is location-blind. The schema
already has `lat`/`lon` columns waiting. When the puck arrives:
- Edit `config/wardrive.conf` → set `ENABLE_GPS=1` and `GPS_DEVICE`
- Add the `start_gps_collector()` body to `wardrive.sh`
- Update `enrich.py` to time-join GPS NMEA against observations

### 2. Cellular omni antenna (incoming)
Connect to RTL-SDR. Adds reception range for 700 MHz–2.7 GHz, including:
- 900 MHz Flock backhaul detection
- LTE/GSM cell tower mapping with `LTE-Cell-Scanner`
- Pager bands, LoRa gateways

Add a second SDR collector profile in the config that runs on this antenna.

### 3. 2.4 GHz Yagi (incoming)
Connect to Alfa. Use for targeted direction-finding rather than
broad sweeps. Add a `--directional` mode to `wardrive.sh` that disables
channel hopping and locks Kismet to one channel for sustained signal-
strength sampling along a bearing.

### 4. Second RTL-SDR
Lets you monitor two frequency bands simultaneously instead of hopping.
Pair one with the dipole at 433 MHz and one with the cellular omni at
915 MHz, both running in parallel. The launcher already supports
`SDR_DEVICE_INDEX`, so just spawn a second `start_sdr_collector` call.

### 5. ESP32 sensor fleet (already owned, deploy later)
Five ESP32s flashed as passive WiFi/BLE sniffers, deployed at fixed
points (your properties), publishing to MQTT. The `capture/` folder
already has a stub for this — when ready, add a `start_esp32_collector`
that subscribes to the MQTT topic and writes NDJSON.

### 6. HackRF or BladeRF (longer term)
Wider frequency coverage (1 MHz–6 GHz on HackRF) opens up everything
above the RTL-SDR ceiling: 2.4 GHz Bluetooth/WiFi at the SDR level, 5 GHz
sensing, drone control bands, etc. Would replace the RTL-SDR as the
primary SDR collector.

### 7. AirSpy or RSP1A
Higher dynamic range and lower noise than the RTL-SDR; better for
weak signal work like distant cell towers.

## Web app integration (planned)

The SQLite database is the integration point. The future web app can:

- Read `wardrive.db` directly (read-only) for queries
- Re-run `enrich.py` on demand to refresh after new sessions
- Use `wifi_obs` and `rf_obs` for time-series and (when GPS exists) map views
- Use `wifi_aps`, `wifi_clients`, `rf_devices` for unique-entity views

Recommended stack when you build it: FastAPI + Leaflet + a simple
React or HTMX frontend. The DB is small enough that you can serve it
directly with no caching layer for personal use.

## Legal posture

All collectors are receive-only. None of them transmit, inject, or
attempt to decrypt protected traffic. This puts you on the safe side of
the ECPA for unencrypted broadcasts (WiFi beacons, ADS-B, TPMS, etc.).

Things this system **does not do** and you should not add:
- Packet injection (deauths, evil-twin, MITM)
- Decryption of WPA/WPA2 traffic
- Decoding of encrypted P25 or cellular voice/data
- Active probing or scanning of any network

Be especially mindful when operating near military installations
(Camp Pendleton). Stay off military frequency allocations, and avoid
anything that could be construed as deliberate intelligence gathering
on the installation. Public-band reception while driving public roads
is fine.

## Troubleshooting

**Alfa isn't recognized:** `lsusb | grep -i realtek` (or whichever chipset
yours uses). If present but no `wlan` interface appears, you likely need
the right driver — `realtek-rtl88xxau-dkms` is the most common one for
Alfa AC adapters.

**RTL-SDR shows up but rtl_test fails:**
`sudo rmmod dvb_usb_rtl28xxu rtl2832 rtl2830` — the kernel driver may
have grabbed it before our blacklist was loaded. Reboot to make
permanent.

**Kismet won't start in monitor mode:** `airmon-ng check kill` first to
stop NetworkManager and wpa_supplicant from interfering.

**Session ends but interface is stuck in monitor mode:**
`sudo airmon-ng stop wlan1mon` then `sudo systemctl start NetworkManager`.

## Logs

Every session writes a log to `capture/logs/<session_id>.log` with a
heartbeat every 30 seconds. If a session ends unexpectedly, that file
will tell you which collector died first.
