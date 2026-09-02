#!/usr/bin/env bash
#
# AURA — atomically bring the box to the latest main + latest images.
#
# WHY THIS EXISTS: a redeploy here is really two independent steps — (1) git
# pull/checkout this repo (docker-compose.yml, Caddyfile, env-var wiring for
# new features) and (2) `docker compose pull && up -d` (new application
# images). Confirmed live 2026-09-02 (docs/BUG_REGISTRY.md BUG-015): on
# 2026-08-31 someone did step 2's `.env` half (AURA_TAG pinned -> latest) but
# never ran step 1, so the box's docker-compose.yml stayed frozen 15 days
# behind main even after the tag fix — new services, new env vars (e.g.
# BUG-013's UASR_APPROVAL_TIMEOUT_SECONDS) silently couldn't exist because the
# checked-out compose file had never heard of them. There is no CI/CD
# auto-deploy (`.github/workflows/cd.yml` only builds+pushes images to GHCR,
# never touches this box) and no working cron/systemd-timer redeploy either —
# this script exists so a human redeploy is one atomic action instead of a
# two-step sequence that's easy to do half of.
#
# USAGE (from anywhere; cd's into the repo root itself):
#   ./redeploy.sh              # deploy origin/main
#   ./redeploy.sh v0.1.5       # deploy a specific tag/branch/commit instead
#
# Exits non-zero and leaves the box UNCHANGED (git checkout only moves after
# a clean fetch; containers only recreate after a successful image pull) if
# any step fails — never leaves a half-applied redeploy the way the Aug 31
# incident did.
set -euo pipefail

REF="${1:-origin/main}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_DIR="$REPO_ROOT/deploy/aws-free-tier"

echo "==> Fetching..."
cd "$REPO_ROOT"
sudo git fetch origin

echo "==> Checking out $REF..."
sudo git checkout "$REF"
DEPLOYED_SHA="$(sudo git rev-parse HEAD)"

echo "==> Pulling images..."
cd "$COMPOSE_DIR"
sudo docker compose pull

echo "==> Recreating containers..."
sudo docker compose up -d

echo "==> Waiting for the gateway to report healthy..."
for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null http://localhost/health 2>/dev/null; then
        echo "==> Redeployed. HEAD is now $DEPLOYED_SHA"
        exit 0
    fi
    sleep 2
done

echo "!! Gateway did not report healthy within 60s after redeploy." >&2
echo "!! HEAD is $DEPLOYED_SHA — check 'docker compose logs' before assuming this deploy is good." >&2
exit 1
