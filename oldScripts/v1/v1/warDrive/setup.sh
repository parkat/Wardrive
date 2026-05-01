#!/usr/bin/env bash
#
# setup.sh — install everything wardrive.sh needs
#
# Run once on your Kali laptop:
#   sudo ./setup.sh

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo $0"
  exit 1
fi

echo "[setup] Updating package index..."
apt update

echo "[setup] Installing core capture dependencies..."
apt install -y \
  aircrack-ng \
  kismet \
  rtl-sdr \
  rtl-433 \
  python3 \
  python3-pip \
  jq \
  sqlite3 \
  usbutils \
  iw

echo "[setup] Blacklisting kernel DVB-T driver (it grabs the RTL-SDR)..."
cat > /etc/modprobe.d/rtl-sdr-blacklist.conf <<EOF
# Stop the kernel DVB-T driver from grabbing the RTL-SDR before rtl_433 can.
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
EOF

echo "[setup] Adding udev rule for non-root RTL-SDR access (optional)..."
if [[ ! -f /etc/udev/rules.d/20-rtlsdr.rules ]]; then
  cat > /etc/udev/rules.d/20-rtlsdr.rules <<EOF
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0666"
EOF
  udevadm control --reload-rules
fi

echo "[setup] Allowing your user to use Kismet without sudo (optional)..."
if [[ -n "${SUDO_USER:-}" ]]; then
  usermod -aG kismet "$SUDO_USER" || true
fi

echo "[setup] Done."
echo ""
echo "Next steps:"
echo "  1. Reboot (to apply the dvb_usb_rtl28xxu blacklist)."
echo "  2. Plug in the Alfa adapter and check: ip -br link"
echo "     Edit config/wardrive.conf with the correct WIFI_IFACE."
echo "  3. Plug in the RTL-SDR and check: rtl_test -t"
echo "  4. Start a session: sudo ./wardrive.sh --name first-test"
