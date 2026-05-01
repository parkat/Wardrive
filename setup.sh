#!/usr/bin/env bash
# setup.sh — one-shot dependency installer for the warDrive project.
# Run once as a normal user with sudo privileges.
#
# USAGE AFTER DOWNLOADING:
#   1. Unzip the downloaded archive anywhere, e.g.:
#        unzip warDrive.zip -d ~/warDrive
#   2. cd ~/warDrive
#   3. bash setup.sh
#
# The script will move any files that landed in the wrong place
# (flat zip layout) into the correct directory structure, then
# install all dependencies.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== warDrive setup ==="

# ── Fix directory structure ────────────────────────────────────────────────────
# When all files are downloaded as a zip and extracted flat (or into a single
# folder), this section moves each file to its correct location.
echo "[setup] Checking directory structure…"

# Directories that must exist
mkdir -p \
    "${SCRIPT_DIR}/config" \
    "${SCRIPT_DIR}/firmware/esp32_ble_scanner" \
    "${SCRIPT_DIR}/processing" \
    "${SCRIPT_DIR}/capture/raw" \
    "${SCRIPT_DIR}/capture/logs"

# Map: filename → correct relative path
# If a file is found directly in SCRIPT_DIR but belongs elsewhere, move it.
declare -A FILE_MAP=(
    ["wardrive.conf"]="config/wardrive.conf"
    ["esp32_ble_scanner.ino"]="firmware/esp32_ble_scanner/esp32_ble_scanner.ino"
    ["enrich.py"]="processing/enrich.py"
    ["esp32_reader.py"]="processing/esp32_reader.py"
)

for filename in "${!FILE_MAP[@]}"; do
    target="${SCRIPT_DIR}/${FILE_MAP[$filename]}"
    # Check root of the project folder first
    if [[ -f "${SCRIPT_DIR}/${filename}" && ! -f "${target}" ]]; then
        echo "[setup]   Moving ${filename} → ${FILE_MAP[$filename]}"
        mv "${SCRIPT_DIR}/${filename}" "${target}"
    fi
    # Also check one level up in case the zip extracted alongside setup.sh
    # (some unzip tools strip or add a top-level folder)
    parent="$(dirname "${SCRIPT_DIR}")"
    if [[ -f "${parent}/${filename}" && ! -f "${target}" ]]; then
        echo "[setup]   Moving ../${filename} → ${FILE_MAP[$filename]}"
        mv "${parent}/${filename}" "${target}"
    fi
done

# Verify critical files are now in place
MISSING=()
for filename in "${!FILE_MAP[@]}"; do
    target="${SCRIPT_DIR}/${FILE_MAP[$filename]}"
    if [[ ! -f "${target}" ]]; then
        MISSING+=("${FILE_MAP[$filename]}")
    fi
done
if [[ -f "${SCRIPT_DIR}/wardrive.sh" ]]; then
    chmod +x "${SCRIPT_DIR}/wardrive.sh"
fi
if [[ -f "${SCRIPT_DIR}/processing/esp32_reader.py" ]]; then
    chmod +x "${SCRIPT_DIR}/processing/esp32_reader.py"
fi

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo ""
    echo "[setup] WARNING: The following files were not found and must be placed manually:"
    for f in "${MISSING[@]}"; do
        echo "         ${SCRIPT_DIR}/${f}"
    done
    echo "        Re-download the full zip and re-run setup.sh."
    echo ""
else
    echo "[setup] Directory structure OK ✓"
fi

# ── System packages ───────────────────────────────────────────────────────────
echo "[setup] Installing system packages…"
sudo apt-get update -qq
sudo apt-get install -y \
    kismet \
    rtl-sdr \
    rtl-433 \
    python3 \
    python3-pip \
    screen

# ── Python packages ───────────────────────────────────────────────────────────
echo "[setup] Installing Python packages…"
pip3 install --break-system-packages \
    pyserial

# ── RTL-SDR udev rule (existing) ─────────────────────────────────────────────
echo "[setup] Configuring RTL-SDR udev rule…"
sudo tee /etc/udev/rules.d/20-rtlsdr.rules > /dev/null <<'EOF'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", GROUP="plugdev", MODE="0664", TAG+="uaccess"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", GROUP="plugdev", MODE="0664", TAG+="uaccess"
EOF
sudo udevadm control --reload-rules
sudo usermod -aG plugdev "${USER}"

# ── ESP32 serial port permissions ─────────────────────────────────────────────
# The ESP32 appears as /dev/ttyUSB* (CP2102 USB-UART) or /dev/ttyACM*
# (CH340 / CDC-ACM). Both are owned by the 'dialout' group on Debian/Kali.
echo "[setup] Adding ${USER} to 'dialout' group for ESP32 serial access…"
sudo usermod -aG dialout "${USER}"

# udev rule so the port is always accessible without re-plugging
sudo tee /etc/udev/rules.d/50-esp32.rules > /dev/null <<'EOF'
# CP2102 USB-UART (common on ESP32 DevKit V1)
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", \
    GROUP="dialout", MODE="0664", SYMLINK+="esp32"

# CH340 USB-UART (alternative chip on some DevKit boards)
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", \
    GROUP="dialout", MODE="0664", SYMLINK+="esp32"

# ESP32-S3 native USB (if board uses CDC-ACM directly)
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", \
    GROUP="dialout", MODE="0664", SYMLINK+="esp32"
EOF
sudo udevadm control --reload-rules

echo ""
echo "=== Setup complete ==="
echo ""
echo "IMPORTANT: Log out and back in (or run 'newgrp dialout') for the"
echo "           group changes to take effect before using the ESP32."
echo ""
echo "To verify your ESP32 serial port:"
echo "  ls -la /dev/ttyUSB* /dev/ttyACM* /dev/esp32 2>/dev/null"
echo "  screen /dev/ttyUSB0 921600    # Ctrl-A then K to exit"
echo ""
echo "Arduino IDE board package URL (add in File > Preferences):"
echo "  https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json"
