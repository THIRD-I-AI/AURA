"""BUG-423: the system health report is reachable under /api/v1, where the proxy routes."""
from __future__ import annotations


def test_system_health_is_registered_under_the_api_prefix():
    from api_gateway.main import app

    paths = {r.path for r in app.routes}
    assert "/api/v1/system/health" in paths
    assert "/system/health" in paths
