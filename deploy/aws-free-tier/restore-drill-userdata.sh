#!/bin/bash
# EC2 user-data for the restore-drill instance; restore-drill.sh passes it to run-instances.
# Runs once, as root, on first boot of an Amazon Linux 2023 instance.
#
# The dead-man switch is armed first, before anything can fail. The instance is launched with
# instance-initiated-shutdown-behavior=terminate, so this halt TERMINATES it 150 minutes after
# boot even if the operator's workstation died mid-drill and restore-drill.sh never reached its
# teardown. A drill takes 10-30 minutes, and the orchestrator gives up long before this fires.
shutdown -h +150 "aura restore drill dead-man switch"

# The toolchain the on-box drill needs. A failed step is flagged at once (drill-setup.failed)
# instead of surfacing minutes later as an unrelated docker error.
(
  set -eu
  dnf install -y docker git python3
  systemctl enable --now docker
  usermod -aG docker ec2-user
  # Pinned, unlike bootstrap.sh: the drill's isolation rests on how compose builds an
  # `internal: true` network, so a compose release must not change that unannounced.
  mkdir -p /usr/libexec/docker/cli-plugins
  curl -fsSL -o /usr/libexec/docker/cli-plugins/docker-compose \
    "https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-$(uname -m)"
  chmod +x /usr/libexec/docker/cli-plugins/docker-compose
  # Importing pandas + numpy + duckdb + langgraph spikes past the 2 GB of RAM a t3.small has.
  dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
) >>/var/log/drill-setup.log 2>&1 && touch /var/log/drill-setup.done || touch /var/log/drill-setup.failed
