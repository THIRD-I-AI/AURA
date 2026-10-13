"""Every route reachable without a login is listed here, on purpose.

Closes the class behind BUG-005, 016/017, 037, 321 and 432: "public" used to be a
list in the middleware that nobody updated when a handler was written as
unauthenticated, and nothing noticed when a route became public by accident. A route
is now public because its handler says so (``@public_route``) or because it is in the
middleware's legacy list, and this test pins the complete set. Adding a public route
means adding it here, with a reviewer watching.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from shared.middleware import JWTAuthMiddleware, _is_public_path
from shared.public_routes import declared_public_routes, is_declared_public, public_route

V1 = "/api/v1"
CF = f"{V1}/counterfactual"

# (method, route path). Nothing else in the gateway may answer without a token.
EXPECTED_PUBLIC = {
    ("GET", "/health"),
    ("POST", f"{V1}/auth/token"),
    ("POST", f"{V1}/auth/register"),
    ("GET", f"{V1}/auth/oidc/status"),
    ("GET", f"{V1}/auth/oidc/login"),
    ("GET", f"{V1}/auth/oidc/callback"),
    ("POST", f"{V1}/auth/oidc/exchange"),
    ("POST", f"{V1}/hooks/fire/{{slug}}"),
    ("GET", f"{V1}/public/saved-queries/{{token}}"),
    ("GET", f"{CF}/jwks"),
    ("GET", f"{CF}/public-key"),
    ("GET", f"{CF}/demo/scenarios"),
    ("GET", f"{CF}/audit/sth"),
    ("GET", f"{CF}/audit/inclusion/{{record_hash}}"),
    ("GET", f"{CF}/artifacts/{{record_hash}}/verify"),
    ("GET", f"{CF}/audit/financial/verify/{{record_hash}}"),
}


def _sample(path: str) -> str:
    """A concrete URL for a route path: each {param} becomes a plain segment."""
    out = path
    while "{" in out:
        start, end = out.index("{"), out.index("}")
        out = out[:start] + "x" + out[end + 1:]
    return out


def _gateway_public_routes():
    from api_gateway.main import app

    public = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        url = _sample(route.path)
        for method in route.methods:
            if _is_public_path(url) or is_declared_public(app, method, url):
                public.add((method, route.path))
    return public


def test_the_set_of_routes_that_need_no_login_is_exactly_the_reviewed_one():
    public = _gateway_public_routes()
    assert public - EXPECTED_PUBLIC == set(), "a route became public without being reviewed here"
    assert EXPECTED_PUBLIC - public == set(), "a route meant to be public is behind the login gate"


def test_every_declared_public_route_states_what_guards_it():
    from api_gateway.main import app

    declared = declared_public_routes(app)
    assert declared, "expected at least one @public_route in the gateway"
    assert all(len(r.reason) > 20 for r in declared)


def test_a_public_route_does_not_open_its_neighbours():
    from api_gateway.main import app

    assert is_declared_public(app, "GET", f"{CF}/artifacts/abc123/verify")
    for method, url in [
        ("GET", f"{CF}/artifacts/abc123"),
        ("GET", f"{CF}/artifacts/abc123/report.pdf"),
        ("POST", f"{CF}/artifacts/abc123/verify"),
        ("GET", f"{V1}/saved-queries/abc123"),
        ("DELETE", f"{V1}/saved-queries/abc123/share"),
    ]:
        assert not is_declared_public(app, method, url), (method, url)


def test_a_reason_is_required():
    with pytest.raises(ValueError):
        public_route("   ")


def _mini_app() -> TestClient:
    app = FastAPI()
    app.add_middleware(JWTAuthMiddleware)

    @app.get("/open/{token}")
    @public_route("test route: gated by the token in the path, nothing else")
    async def open_route(token: str):
        return {"token": token}

    @app.get("/open/{token}/secret")
    async def secret(token: str):
        return {"secret": True}

    @app.post("/open/{token}")
    async def write(token: str):
        return {"written": True}

    return TestClient(app)


def test_the_login_gate_lets_a_declared_route_through_and_nothing_beside_it():
    client = _mini_app()
    assert client.get("/open/abc").status_code == 200
    assert client.get("/open/abc/secret").status_code == 401
    assert client.post("/open/abc").status_code == 401
