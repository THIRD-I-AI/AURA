"""BUG-596: redeploy.sh waited for `curl -fsS http://localhost/health`, which Caddy answers
with a 308 redirect to https; curl -f treats a redirect as success, so every deploy reported
"Redeployed" whether or not the gateway came up. It now waits for the gateway container's own
Docker healthcheck, which calls /health on :8000 inside the container."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _health_wait() -> str:
    script = (ROOT / "deploy/aws-free-tier/redeploy.sh").read_text(encoding="utf-8")
    return script.split("Waiting for the gateway", 1)[1]


def test_redeploy_no_longer_trusts_the_redirected_localhost_probe():
    assert "curl -fsS -o /dev/null http://localhost/health" not in _health_wait()


def test_redeploy_waits_for_the_containers_own_healthcheck():
    wait = _health_wait()
    assert "docker inspect --format '{{.State.Health.Status}}'" in wait
    assert '"$status" = "healthy"' in wait
    compose = (ROOT / "deploy/aws-free-tier/docker-compose.yml").read_text(encoding="utf-8")
    gateway = compose.split("  api_gateway:", 1)[1].split("\n  uasr_service:", 1)[0]
    assert "healthcheck:" in gateway and "localhost:8000/health" in gateway
