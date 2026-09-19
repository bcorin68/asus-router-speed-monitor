#!/bin/bash
set -e

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as your normal Linux user, not as root."
    echo "The installer will use sudo when needed."
    exit 1
fi

RUN_USER="$(id -un)"
RUN_HOME="$HOME"

echo
echo "Router Speed Monitor installer"
echo "=============================="
echo
echo "Linux user: $RUN_USER"
echo

for cmd in python3 ssh systemctl sudo; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "ERROR: Required command '$cmd' was not found."
        exit 1
    fi
done

DEFAULT_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

read -rp "Router IP/address: " ROUTER_HOST
read -rp "Router SSH username: " ROUTER_USER

DEFAULT_KEY="$RUN_HOME/.ssh/router-speedtest"
read -rp "SSH private key [$DEFAULT_KEY]: " SSH_KEY
SSH_KEY="${SSH_KEY:-$DEFAULT_KEY}"

DEFAULT_KNOWN="$RUN_HOME/.ssh/known_hosts"
read -rp "SSH known_hosts file [$DEFAULT_KNOWN]: " KNOWN_HOSTS
KNOWN_HOSTS="${KNOWN_HOSTS:-$DEFAULT_KNOWN}"

read -rp "Dashboard listen address [$DEFAULT_IP]: " LISTEN_ADDRESS
LISTEN_ADDRESS="${LISTEN_ADDRESS:-$DEFAULT_IP}"

read -rp "Dashboard TCP port [8090]: " PORT
PORT="${PORT:-8090}"

read -rp "History retention in days [90]: " RETENTION
RETENTION="${RETENTION:-90}"

DEFAULT_TIMEZONE="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
DEFAULT_TIMEZONE="${DEFAULT_TIMEZONE:-America/New_York}"

read -rp "Speed-test schedule timezone [$DEFAULT_TIMEZONE]: " TIMEZONE
TIMEZONE="${TIMEZONE:-$DEFAULT_TIMEZONE}"

if [ ! -e "/usr/share/zoneinfo/$TIMEZONE" ]; then
    echo
    echo "ERROR: '$TIMEZONE' does not appear to be a valid timezone."
    echo "Examples: America/New_York, America/Chicago, America/Los_Angeles, Europe/London"
    exit 1
fi

if [ ! -f "$SSH_KEY" ]; then
    echo
    echo "ERROR: SSH key does not exist:"
    echo "$SSH_KEY"
    exit 1
fi

if [ ! -f "$KNOWN_HOSTS" ]; then
    echo
    echo "ERROR: known_hosts file does not exist:"
    echo "$KNOWN_HOSTS"
    exit 1
fi

echo
echo "Checking router host key..."
if ! ssh-keygen -F "$ROUTER_HOST" -f "$KNOWN_HOSTS" >/dev/null 2>&1; then
    echo
    echo "ERROR: $ROUTER_HOST is not present in:"
    echo "$KNOWN_HOSTS"
    echo
    echo "SSH to the router manually once and verify its host key first."
    exit 1
fi

echo
echo "Installing application..."

sudo mkdir -p /opt/router-speed-monitor
sudo mkdir -p /var/lib/router-speed-monitor/private
sudo mkdir -p /var/lib/router-speed-monitor/www

sudo cp collector.py server.py index.html /opt/router-speed-monitor/

sudo chown -R root:root /opt/router-speed-monitor
sudo chmod 755 /opt/router-speed-monitor
sudo chmod 644 /opt/router-speed-monitor/*.py
sudo chmod 644 /opt/router-speed-monitor/index.html

sudo chown -R "$RUN_USER:$RUN_USER" /var/lib/router-speed-monitor
sudo chmod 750 /var/lib/router-speed-monitor
sudo chmod 700 /var/lib/router-speed-monitor/private
sudo chmod 750 /var/lib/router-speed-monitor/www

sudo python3 - "$ROUTER_HOST" "$ROUTER_USER" "$SSH_KEY" \
    "$KNOWN_HOSTS" "$LISTEN_ADDRESS" "$PORT" "$RETENTION" "$RUN_USER" <<'PY'
import json
import os
import sys

router_host, router_user, ssh_key, known_hosts, listen_address, port, retention, run_user = sys.argv[1:]

config = {
    "router_host": router_host,
    "router_user": router_user,
    "ssh_key": ssh_key,
    "known_hosts": known_hosts,
    "state_dir": "/var/lib/router-speed-monitor",
    "listen_address": listen_address,
    "port": int(port),
    "retention_days": int(retention)
}

path = "/etc/router-speed-monitor.json"

with open(path, "w") as f:
    json.dump(config, f, indent=2)
    f.write("\n")

os.chmod(path, 0o640)
PY

sudo chown root:"$RUN_USER" /etc/router-speed-monitor.json

echo "Installing systemd services..."

sudo tee /etc/systemd/system/router-speedtest.service >/dev/null <<UNIT
[Unit]
Description=Collect an ASUS router Internet speed test
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=/var/lib/router-speed-monitor
ExecStart=/usr/bin/python3 /opt/router-speed-monitor/collector.py
TimeoutStartSec=330
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/var/lib/router-speed-monitor
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
UNIT

sudo tee /etc/systemd/system/router-speed-dashboard.service >/dev/null <<UNIT
[Unit]
Description=Router speed history dashboard
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=/var/lib/router-speed-monitor
ExecStart=/usr/bin/python3 /opt/router-speed-monitor/server.py
Restart=on-failure
RestartSec=10
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
UNIT

sudo tee /etc/systemd/system/router-speedtest.timer >/dev/null <<UNIT
[Unit]
Description=Test router Internet speed every six hours

[Timer]
OnCalendar=*-*-* 00,06,12,18:07:00 $TIMEZONE
AccuracySec=1min
Persistent=false
Unit=router-speedtest.service

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now router-speed-dashboard.service
sudo systemctl enable --now router-speedtest.timer

echo
echo "Installation complete."
echo
echo "Dashboard:"
echo "  http://$LISTEN_ADDRESS:$PORT/"
echo
echo "A speed test was NOT started automatically."
echo "Tests can consume a very large amount of bandwidth."
echo
echo "Run one manually with:"
echo
echo "  sudo systemctl start router-speedtest.service"
echo
echo "Check it with:"
echo
echo "  sudo journalctl -u router-speedtest.service -n 30 --no-pager"
echo
echo "Schedule timezone: $TIMEZONE"
echo
