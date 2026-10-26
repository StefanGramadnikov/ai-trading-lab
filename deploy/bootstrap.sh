#!/usr/bin/env bash
# One-time server preparation. Run ONCE as root on a fresh Ubuntu 24.04 / Debian 12 server:
#   bash bootstrap.sh
# Read it first; it changes the firewall and installs packages. It does not touch SSH settings
# (disabling password logins is a manual step in docs/30-deployment-runbook.md, after you test your key).
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "run as root" >&2; exit 1; fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y upgrade
# docker.io and the compose plugin come from the distribution's own repository.
apt-get -y install ca-certificates curl git ufw unattended-upgrades docker.io docker-compose-v2

# Firewall: inbound SSH only. Nothing else is published (compose.prod.yaml has no ports).
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

# Security updates install themselves.
dpkg-reconfigure -f noninteractive unattended-upgrades

# An unprivileged account owns the deployment; it is the only one in the docker group.
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
cat >/etc/docker/daemon.json <<'JSON'
{"log-driver": "json-file", "log-opts": {"max-size": "10m", "max-file": "5"}}
JSON
systemctl enable --now docker
systemctl restart docker

echo "bootstrap done. Next: log in as atl and follow docs/30-deployment-runbook.md, section 3."
