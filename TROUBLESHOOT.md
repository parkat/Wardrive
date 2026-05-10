# Wardrive Pi — Troubleshooting Context

Hand this file to Claude at the start of a troubleshooting session.

---

## What this is

A bootable Raspberry Pi 3B wardriving appliance. The OS image is built via
pi-gen (arm64 branch) and contains a Python asyncio supervisor daemon that
manages multiple RF/BLE/GPS collectors, a FastAPI web UI, and a debug API.

**GitHub repo:** https://github.com/parkat/Wardrive  
**Branch:** main  
**Image tag:** v0.1.6 (latest build)

---

## Hardware

- **Board:** Raspberry Pi 3 Model B rev 1.2 (BCM2837, Cortex-A53, 1 GB RAM)
- **OS image:** 64-bit Raspberry Pi OS Lite (bookworm), built from pi-gen arm64 branch
- **Image file:** `2026-05-10-wardrive-pi-lite.img.xz` (downloaded from GitHub Actions artifact)
- **Boot:** SD card (flashed with `xz -dc *.img.xz | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync`)
- **Storage:** USB drive labeled `WARDRIVE` for capture data (falls back to SD card)
- **Network:** `eth0` on wired LAN for SSH/API access; `wlan0` for WiFi AP fallback; `wlan1` (Alfa adapter) for wardriving capture

---

## Current symptom

**Kernel panic on boot.** Previously saw: solid red LED, no green ACT LED, no
HDMI output, no AP visible. After further observation: kernel panic.

The image is a plain Raspberry Pi OS Lite arm64 (our wardrive stage may not
have been applied — see Build Status below).

---

## Build status (important)

The CI build pipeline has been iterating. Key facts:

- **v0.1.5** was the first build that completed (`Build finished` logged). But
  our custom `stage-wardrive` **did not run** — the rootfs was never populated
  because `prerun.sh` (which must call `copy_previous()`) was missing.
- **v0.1.6** adds `prerun.sh` and fixes the artifact glob and release
  permissions. This build may not have completed yet.
- The downloaded image (`2026-05-10-wardrive-pi-lite.img.xz`) is likely the
  **v0.1.5** artifact: a plain Pi OS Lite arm64 image **without** the wardrive
  stack installed.

**So the kernel panic is from a stock Pi OS arm64 image on a Pi 3B.** This is
a hardware/image compatibility issue, not a wardrive software issue.

---

## Pi 3B boot process (important for debugging)

The Pi 3B uses a different boot sequence than Pi 4/5:

- **Requires `bootcode.bin` on the SD card** — Pi 3B has no ROM bootloader.
  Pi 4/5 have it in ROM. If the arm64 pi-gen image omits `bootcode.bin`,
  Pi 3B cannot boot at all.
- **Boot partition** should be FAT32, mounted at `/boot/firmware/` in the OS
  but directly accessible as the first partition on the SD card.
- **64-bit boot** on Pi 3B requires `arm_64bit=1` in `config.txt` (or the
  arm64 pi-gen branch should set this automatically).
- **Kernel:** should be `kernel8.img` for Pi 3B arm64. `kernel_2712.img` is
  Pi 5 only.

---

## Wardrive stack (when correctly installed)

### Services (systemd)
| Service | Description |
|---------|-------------|
| `wardrive-supervisor` | Python asyncio supervisor, manages all collectors |
| `wardrive-webapp` | FastAPI web UI + debug API on port 8000 |
| `wardrive-ap` | hostapd + dnsmasq WiFi AP fallback |
| `gpsd` | GPS daemon |

### Key paths on the Pi
| Path | Description |
|------|-------------|
| `/opt/wardrive/` | Main application directory |
| `/opt/wardrive/config/wardrive.conf` | Configuration file |
| `/opt/wardrive/supervisor/main.py` | Supervisor entry point |
| `/opt/wardrive/processing/wardrive.db` | SQLite database (or on USB drive) |
| `/var/log/wardrive/` | Log files |
| `/etc/systemd/system/wardrive-*.service` | Systemd units |
| `/boot/firmware/config.txt` | Pi boot config |

### Network access (when booted)
- **SSH:** `ssh wardrive@wardrive.local` — password: `wardrive`
- **Web UI:** `http://wardrive.local:8000`
- **Debug API:** `http://wardrive.local:8000/api/debug/collectors`
  - Bearer token: `changeme-set-a-real-token` (in wardrive.conf)
- **WiFi AP fallback** (if no wired uplink): SSID `rpiwifi2_4ghz`, passphrase `wardrivelocal`, gateway `192.168.88.1`

### Debug API endpoints
```bash
TOKEN="changeme-set-a-real-token"
BASE="http://wardrive.local:8000/api/debug"

curl -H "Authorization: Bearer $TOKEN" $BASE/collectors      # collector states
curl -H "Authorization: Bearer $TOKEN" $BASE/system          # CPU/RAM/disk
curl -H "Authorization: Bearer $TOKEN" $BASE/events          # recent events
curl -H "Authorization: Bearer $TOKEN" $BASE/db/health       # database status
curl -H "Authorization: Bearer $TOKEN" $BASE/usb             # USB power state
curl -H "Authorization: Bearer $TOKEN" "$BASE/collector/wifi/log?lines=50"
```

---

## Config file (`/opt/wardrive/config/wardrive.conf`)

```bash
USB_DRIVE_LABEL=WARDRIVE
DATA_FALLBACK_DIR=/opt/wardrive/data
USB_BUDGET_MA=900
PRIORITY_GPS=100
PRIORITY_WIFI=90
PRIORITY_ESP32=70
PRIORITY_RTL433=60
PRIORITY_WIDEBAND=50
PRIORITY_HACKRF=40
ENABLE_WIFI=true
WIFI_INTERFACE=wlan1
ENABLE_ESP32=true
ESP32_BAUD=921600
ENABLE_GPS=true
ENABLE_RTL433=true
ENABLE_WIDEBAND=true
SDR_MODE=rtl433
ENABLE_HACKRF=false
HANG_TIMEOUT=120
RESTART_MAX=10
RESTART_BACKOFF_INITIAL=2
RESTART_BACKOFF_MAX=60
RESTART_RESET_AFTER=300
AP_SSID=rpiwifi2_4ghz
AP_PASSPHRASE=wardrivelocal
AP_CHANNEL=6
AP_IP=192.168.88.1
DEBUG_TOKEN=changeme-set-a-real-token
```

---

## Collector priority / power shedding

All collectors run simultaneously. If USB power draw exceeds `USB_BUDGET_MA`,
the lowest-priority collector is shed first. Priority (higher = keep running):

1. GPS (100)
2. WiFi / Kismet (90)
3. ESP32 BLE (70)
4. RTL-SDR rtl_433 (60)
5. RTL-SDR wideband (50)
6. HackRF (40, disabled — future hardware)

RTL-SDR rtl_433 and wideband share the same dongle (mutex group). Only one
runs at a time; switch with the web UI or debug API.

---

## Useful diagnostic commands (once SSH'd in)

```bash
# Service status
systemctl status wardrive-supervisor wardrive-webapp wardrive-ap

# Live supervisor log
journalctl -u wardrive-supervisor -f

# Live webapp log
journalctl -u wardrive-webapp -f

# Collector process tree
pgrep -a kismet; pgrep -a rtl_433; pgrep -a gpspipe

# USB devices
lsusb
dmesg | grep -E "usb|tty" | tail -20

# GPS fix
gpspipe -w -n 5

# Database tables
sqlite3 /opt/wardrive/data/wardrive.db ".tables"

# Disk usage
df -h
du -sh /opt/wardrive/data/

# Boot config
cat /boot/firmware/config.txt
```

---

## CI / image build

- **Workflow:** `.github/workflows/build-image.yml`
- **Runner:** `ubuntu-24.04-arm` (native ARM64, no QEMU)
- **Method:** `sudo bash build.sh` (non-Docker pi-gen)
- **Stage list:** `stage0 stage1 stage2 stage-wardrive`
- **Our stage:** `image/stage-wardrive/` — clones repo, installs deps, enables services
- **Known issue:** v0.1.5 image was built without wardrive stage (missing `prerun.sh`). Fixed in v0.1.6.
- **Image naming:** pi-gen prefixes with date: `YYYY-MM-DD-wardrive-pi-lite.img.xz`
