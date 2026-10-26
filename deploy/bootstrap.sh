#!/usr/bin/env bash
# One-time server preparation. Run ONCE as root on a fresh Ubuntu 24.04 / Debian 12 server:
#   bash bootstrap.sh
# Read it first; it changes the firewall and installs packages. It does not touch SSH settings
# (disabling password logins is a manual step in docs/30-deployment-runbook.md, after you test your key).
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "run as root" >&2; exit 1; fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y -o Dpkg::Options::=--force-confold upgrade
# docker.io and the compose plugin come from the distribution's own repository.
apt-get -y install ca-certificates curl git ufw unattended-upgrades docker.io docker-compose-v2

# Firewall: inbound SSH only. Nothing else is published (compose.prod.yaml has no ports).
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

# Security updates install themselves.
dpkg-reconfigure -f noninteractive unattended-upgrades

# A non-root account owns the deployment. Note: membership of the docker group is effectively root
# on this machine, so treat the atl login (your SSH key) as the keys to the server.
id atl >/dev/null 2>&1 || useradd --create-home --shell /bin/bash atl
usermod -aG docker atl
install -d -o atl -g atl -m 750 /opt/atl
install -d -o atl -g atl -m 700 /var/backups/atl
# Let the atl account log in with the same key you used for root.
if [ -f /root/.ssh/authorized_keys ]; then
  install -d -o atl -g atl -m 700 /home/atl/.ssh
  install -o atl -g atl -m 600 /root/.ssh/authorized_keys /home/atl/.ssh/authorized_keys
fi

# Keep container logs from filling the disk even outside the per-service limits.
if [ ! -f /etc/docker/daemon.json ]; then
  cat >/etc/docker/daemon.json <<'JSON'
{"log-driver": "json-file", "log-opts": {"max-size": "10m", "max-file": "5"}}
JSON
fi
systemctl enable --now docker
systemctl restart docker

# The nightly backup timer (the unit skips itself until the code is in /opt/atl/ai-trading-lab).
here="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$here/atl-backup.service" ] && [ -f "$here/atl-backup.timer" ]; then
  install -m 644 "$here/atl-backup.service" "$here/atl-backup.timer" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now atl-backup.timer
else
  echo "WARNING: atl-backup.service/.timer not found next to bootstrap.sh; copy the whole deploy/ folder" >&2
fi

if [ -f /var/run/reboot-required ]; then echo "A reboot is required (kernel update): run 'reboot' now, before section 4."; fi

echo "bootstrap done. Next: log in as atl and follow docs/30-deployment-runbook.md, section 3."
