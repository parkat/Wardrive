#!/bin/bash -e

on_chroot << EOF

# Create wardrive system user
useradd -r -s /usr/sbin/nologin \
    -G dialout,plugdev,netdev,gpio,video \
    wardrive 2>/dev/null || true

# Set ownership on app dir (files placed here by CI via rootfs overlay)
chown -R wardrive:wardrive /opt/wardrive
chmod +x /opt/wardrive/supervisor/main.py

# Initialize empty database
mkdir -p /opt/wardrive/data
python3 /opt/wardrive/processing/migrate.py /opt/wardrive/data/wardrive.db
chown -R wardrive:wardrive /opt/wardrive/data

# sudoers for kismet, airmon-ng, uhubctl
cat > /etc/sudoers.d/wardrive << 'SUDOERS'
wardrive ALL=(root) NOPASSWD: /usr/bin/kismet
wardrive ALL=(root) NOPASSWD: /usr/sbin/airmon-ng
wardrive ALL=(root) NOPASSWD: /usr/bin/uhubctl
wardrive ALL=(root) NOPASSWD: /bin/kill
SUDOERS
chmod 440 /etc/sudoers.d/wardrive

# Increase USB current budget (Pi 3B: 1.2A instead of 600mA)
echo "max_usb_current=1" >> /boot/firmware/config.txt

# Enable services
systemctl enable wardrive-supervisor wardrive-webapp wardrive-ap gpsd ssh

# Hostname
echo "wardrive" > /etc/hostname
sed -i "s/raspberrypi/wardrive/g" /etc/hosts

EOF
