#!/bin/bash
# EC2 user-data — runs once, as root, on first boot of an Amazon Linux 2023
# instance. Passed via: aws ec2 run-instances --user-data file://bootstrap.sh
#
# Idempotent by design: cloud-init normally runs this once, but operators
# re-run it by hand after a failed boot, and a script that appends a second
# swapfile or a duplicate fstab line on the second run is a trap. Every step
# below checks before it acts.
set -euxo pipefail

# ── Swap ────────────────────────────────────────────────────────────────────
# 2 GB of swap on a 1 GB instance. This is not belt-and-braces: importing
# pandas + numpy + duckdb + langgraph briefly spikes well past steady-state
# RSS, and without swap the gateway is OOM-killed mid-import and restart-loops
# forever with no obvious error. Swap converts a hard failure into a slow
# first boot, which is the right trade at zero users.
if [ ! -f /swapfile ]; then
  dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
# Default vm.swappiness=60 swaps too eagerly and makes the box feel dead.
# 10 keeps swap as an overflow buffer rather than a routine tier.
sysctl -w vm.swappiness=10
grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf

# ── Docker + compose v2 ─────────────────────────────────────────────────────
dnf install -y docker git
systemctl enable --now docker
usermod -aG docker ec2-user

if [ ! -x /usr/libexec/docker/cli-plugins/docker-compose ]; then
  mkdir -p /usr/libexec/docker/cli-plugins
  curl -fsSL \
    "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
    -o /usr/libexec/docker/cli-plugins/docker-compose
  chmod +x /usr/libexec/docker/cli-plugins/docker-compose
fi

# ── Source ──────────────────────────────────────────────────────────────────
# Only the deploy assets are needed at runtime — images come from GHCR, the
# instance never builds. The clone is for the compose file and Caddyfile.
if [ ! -d /opt/aura ]; then
  git clone --depth 1 https://github.com/THIRD-I-AI/AURA.git /opt/aura
fi
chown -R ec2-user:ec2-user /opt/aura

# Deliberately NOT starting the stack here: .env does not exist yet and every
# production guard in shared/config.py would (correctly) refuse to boot. The
# operator fills .env, then runs `docker compose up -d`. See README.md step 3.
echo "bootstrap complete — now: cd /opt/aura/deploy/aws-free-tier && cp .env.example .env"
