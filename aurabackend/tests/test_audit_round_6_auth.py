"""Regression tests for BUG-453..455, found in audit round 6 of the auth area."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from tests.test_auth import V1, password_client  # noqa: F401  (pytest fixture)

# ── BUG-453 ──────────────────────────────────────────────────────────────


def test_oidc_login_is_rate_limited():
    from shared.middleware import RateLimitMiddleware
    from shared.rate_limit import InMemoryBackend

    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware, requests_per_window=3, window_seconds=60,
        backend=InMemoryBackend(), trust_forwarded_for=False,
    )

    @app.get("/api/v1/auth/oidc/login")
    def oidc_login_stub():
        return {"ok": True}

    client = TestClient(app)
    codes = [client.get("/api/v1/auth/oidc/login").status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert 429 in codes[3:], codes


def test_liveness_probes_stay_exempt_from_rate_limiting():
    from shared.middleware import _RATE_LIMIT_EXEMPT_PATHS

    assert "/health" in _RATE_LIMIT_EXEMPT_PATHS
    assert "/api/v1/auth/oidc/login" not in _RATE_LIMIT_EXEMPT_PATHS


def test_oidc_state_store_is_bounded(monkeypatch):
    from shared import oidc

    monkeypatch.setattr(oidc, "_state_store", {})
    monkeypatch.setattr(oidc, "MAX_PENDING_STATES", 50)
    states = [oidc.new_state()[0] for _ in range(200)]

    assert len(oidc._state_store) == 50
    # The newest login attempts are the ones kept.
    assert oidc.pop_state(states[-1]) is not None
    assert oidc.pop_state(states[0]) is None


def test_expired_oidc_states_are_still_swept(monkeypatch):
    from shared import oidc

    monkeypatch.setattr(oidc, "_state_store", {})
    old = oidc.new_state()[0]
    verifier, _ = oidc._state_store[old]
    oidc._state_store[old] = (verifier, 0.0)
    fresh = oidc.new_state()[0]

    assert old not in oidc._state_store
    assert oidc.pop_state(fresh) is not None


# ── BUG-454 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("login_email", ["alice@example.com", "ALICE@EXAMPLE.COM", "Alice@Example.com"])
def test_password_login_matches_the_email_case_insensitively(password_client, login_email):  # noqa: F811
    reg = {"name": "Alice", "email": "Alice@Example.com", "password": "correct horse battery"}
    assert password_client.post(f"{V1}/auth/register", json=reg).status_code == 201

    resp = password_client.post(
        f"{V1}/auth/token", json={"email": login_email, "password": reg["password"]},
    )
    assert resp.status_code == 200, resp.text


def test_password_login_still_rejects_a_wrong_password(password_client):  # noqa: F811
    reg = {"name": "Bob", "email": "Bob@Example.com", "password": "correct horse battery"}
    assert password_client.post(f"{V1}/auth/register", json=reg).status_code == 201

    resp = password_client.post(
        f"{V1}/auth/token", json={"email": "bob@example.com", "password": "wrong"},
    )
    assert resp.status_code == 401


# ── BUG-455 ──────────────────────────────────────────────────────────────


class _Body(BaseModel):
    email: str | None = None


def _pydantic_app(max_bytes: int) -> TestClient:
    from shared.middleware import RequestBodyLimitMiddleware

    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=max_bytes)

    @app.post("/auth/token")
    async def token(body: _Body):
        return {"email": body.email}

    return TestClient(app)


def test_a_chunked_oversized_body_on_a_pydantic_route_is_413():
    def chunks():
        for _ in range(10):
            yield b"x" * 500

    r = _pydantic_app(1024).post(
        "/auth/token", content=chunks(), headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413, r.text
    assert "exceeds" in r.json()["detail"]


def test_a_small_body_on_a_pydantic_route_is_untouched():
    r = _pydantic_app(1024).post("/auth/token", json={"email": "a@b.c"})
    assert r.status_code == 200 and r.json() == {"email": "a@b.c"}
