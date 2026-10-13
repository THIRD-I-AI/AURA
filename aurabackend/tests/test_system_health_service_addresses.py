"""BUG-429: system health probes the configured service addresses, not localhost."""
from __future__ import annotations

import api_gateway.main as gateway


def test_configured_services_are_probed_where_the_gateway_is_told_they_are(monkeypatch):
    for env in gateway._SERVICE_URL_ENV.values():
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("AURA_SANDBOX_URL", "http://execution_sandbox:8003/")
    monkeypatch.setenv("AURA_CODEGEN_URL", "http://code_generation:8001")

    services = gateway._configured_services()

    assert services["execution_sandbox"] == "http://execution_sandbox:8003/health"
    assert services["code_generation"] == "http://code_generation:8001/health"


def test_services_this_deployment_does_not_configure_are_left_out(monkeypatch):
    for env in gateway._SERVICE_URL_ENV.values():
        monkeypatch.delenv(env, raising=False)

    assert set(gateway._configured_services()) == {"uasr"}
