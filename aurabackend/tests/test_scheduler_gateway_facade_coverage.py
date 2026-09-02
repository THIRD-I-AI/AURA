"""
Regression coverage for the gateway's scheduler_service proxy facade.

Found via research survey: scheduler_service (S20a/S20b/S20.2 -- Postgres
LISTEN/NOTIFY wake, pg_advisory_lock leader election, a fully built and
tested distributed cron/interval job scheduler) had NO gateway proxy routes
at all. The only reference to port 8004 anywhere in api_gateway/ was the
/system/health probe -- the entire subsystem was unreachable from the
product surface despite being functional.

Same pattern as test_uasr_gateway_facade_coverage.py: diff the underlying
service's real route set against what the gateway proxies. Unlike UASR,
scheduler_service's own paths (/jobs, /executions, /admin/cleanup) carry no
service-name prefix, so the gateway facade maps each path onto
/api/v1/scheduler<path> rather than a bare 1:1 copy.
"""
from __future__ import annotations

import os
import sys

from fastapi.routing import APIRoute

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

V1 = "/api/v1"

# Root health-check endpoint -- not meant to be proxied, same reasoning as
# UASR's "/" exclusion in test_uasr_gateway_facade_coverage.py. The gateway
# already probes scheduler health separately via /system/health.
_EXCLUDED_SCHEDULER_PATHS = {"/", "/health", "/metrics", "/docs", "/redoc", "/openapi.json"}


def _scheduler_service_routes():
    from scheduler_service.main import scheduler_app

    out = []
    for route in scheduler_app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path in _EXCLUDED_SCHEDULER_PATHS:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            out.append((method, route.path))
    return out


def _gateway_route_set():
    from api_gateway.main import app as gw_app

    out = set()
    for route in gw_app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            out.add((method, route.path))
    return out


def test_every_scheduler_route_has_a_gateway_facade():
    """Every real scheduler_service endpoint maps onto
    /api/v1/scheduler<service_path> on the gateway."""
    gateway_routes = _gateway_route_set()
    missing = [
        (method, path, f"{V1}/scheduler{path}")
        for method, path in _scheduler_service_routes()
        if (method, f"{V1}/scheduler{path}") not in gateway_routes
    ]
    assert not missing, f"scheduler_service routes with no gateway facade: {missing}"
