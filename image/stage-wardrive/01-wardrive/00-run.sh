#!/bin/bash -e
# Pi-gen stage: install and configure the wardrive stack.
# Runs inside the pi-gen chroot.

on_chroot << EOF

# ── Create wardrive system user ────────────────────────────────────────────────
useradd -r -s /usr/sbin/nologin \
    -G dialout,plugdev,netdev,gpio,video \
    wardrive 2>/dev/null || true

# ── Clone repo ────────────────────────────────────────────────────────────────
git clone https://github.com/parkat/Wardrive /opt/wardrive
chown -R wardrive:wardrive /opt/wardrive

# ── Make supervisor executable ────────────────────────────────────────────────
chmod +x /opt/wardrive/supervisor/main.py

# ── Apply DB migrations (creates empty DB with correct schema) ────────────────
mkdir -p /opt/wardrive/data
python3 /opt/wardrive/processing/migrate.py /opt/wardrive/data/wardrive.db
chown wardrive:wardrive /opt/wardrive/data/wardrive.db

# ── Install systemd units ─────────────────────────────────────────────────────
cp /opt/wardrive/systemd/wardrive-supervisor.service /etc/systemd/system/
cp /opt/wardrive/systemd/wardrive-webapp.service     /etc/systemd/system/

# ── Install udev rules ────────────────────────────────────────────────────────
cp /opt/wardrive/image/stage-wardrive/files/99-wardrive-udev.rules \
   /etc/udev/rules.d/

# ── hostapd + dnsmasq for AP fallback ────────────────────────────────────────
cp /opt/wardrive/image/stage-wardrive/files/hostapd.conf /etc/hostapd/wardrive-hostapd.conf
cp /opt/wardrive/image/stage-wardrive/files/dnsmasq-wardrive.conf \
   /etc/dnsmasq.d/wardrive.conf
cp /opt/wardrive/image/stage-wardrive/files/wardrive-ap.service \
   /etc/systemd/system/

# ── sudoers for kismet and airmon-ng ─────────────────────────────────────────
cat > /etc/sudoers.d/wardrive << 'SUDOERS'
wardrive ALL=(root) NOPASSWD: /usr/bin/kismet
wardrive ALL=(root) NOPASSWD: /usr/sbin/airmon-ng
wardrive ALL=(root) NOPASSWD: /usr/bin/uhubctl
wardrive ALL=(root) NOPASSWD: /bin/kill
SUDOERS
chmod 440 /etc/sudoers.d/wardrive

# ── /boot/config.txt: increase USB current budget ─────────────────────────────
# Pi 3B: enables 1.2A USB budget instead of 600mA
echo "max_usb_current=1" >> /boot/config.txt

# ── Enable all services ───────────────────────────────────────────────────────
systemctl enable wardrive-supervisor
systemctl enable wardrive-webapp
systemctl enable wardrive-ap
systemctl enable gpsd

# ── Hostname ──────────────────────────────────────────────────────────────────
echo "wardrive" > /etc/hostname
sed -i "s/raspberrypi/wardrive/g" /etc/hosts

# ── SSH enabled by default ────────────────────────────────────────────────────
systemctl enable ssh

EOF
