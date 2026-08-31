"""
Regression coverage for the gateway's UASR proxy facade.

Found live (2026-08-31, docs/superpowers/specs/2026-08-31-uasr-live-
validation-and-benchmark.md): only 7 of UASR's ~20 HTTP endpoints were
proxied through the gateway (api_gateway/routers/pipelines.py's `_uasr`
helper) -- /uasr/heal, the endpoint that actually returns healed rows, had
no gateway route at all. Nothing caught this because every other UASR test
either calls uasr.service's functions directly or hits the gateway routes
that already existed.

UASR is a separately-deployed service (proxied via HTTP, not in-process
like counterfactual_service -- see test_gateway_facade_coverage.py for that
service's version of this same guard), so this diffs uasr.service's real
route set against the gateway's proxied set, the same pattern.

Two routes are DELIBERATELY excluded, not missed: `/uasr/mapek/status` and
`/uasr/mapek/resume` control the background Kafka worker process directly.
Whether an external caller should be able to pause/resume that over the
public API is a real product/security decision, not an oversight -- left
un-proxied on purpose until that decision is made.
"""
from __future__ import annotations

import os
import sys

from fastapi.routing import APIRoute

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

V1 = "/api/v1"

# Non-API routes uasr.service.app carries (infra + the operator dashboard
# HTML) that were never meant to be proxied — comparing them would flag
# these as "missing" forever, the same reasoning as
# test_gateway_facade_coverage.py's _EXCLUDED_SERVICE_PATHS.
_EXCLUDED_UASR_PATHS = {
    "/health", "/metrics", "/docs", "/redoc", "/openapi.json",
    "/", "/dashboard",
}

# Deliberately not proxied — see module docstring.
_EXCLUDED_UASR_CONTROL_PLANE_PATHS = {
    "/uasr/mapek/status", "/uasr/mapek/resume",
}


def _uasr_service_routes():
    from uasr.service import app as uasr_app
    out = []
    for route in uasr_app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path in _EXCLUDED_UASR_PATHS or route.path in _EXCLUDED_UASR_CONTROL_PLANE_PATHS:
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


def test_every_uasr_route_has_a_gateway_facade():
    """UASR path segments map 1:1 onto the gateway (unlike counterfactual_
    service, which strips a prefix) -- /uasr/heal on the service is
    /api/v1/uasr/heal on the gateway."""
    gateway_routes = _gateway_route_set()
    missing = [
        (method, path, f"{V1}{path}")
        for method, path in _uasr_service_routes()
        if (method, f"{V1}{path}") not in gateway_routes
    ]
    assert not missing, f"UASR routes with no gateway facade: {missing}"
