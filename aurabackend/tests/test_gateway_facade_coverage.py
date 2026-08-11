"""Regression coverage for the gateway's counterfactual_service facade.

Two things this file guards:

  1. Route-set drift — the gateway hand-mounts counterfactual_service
     in-process and proxies each endpoint individually (see
     api_gateway/routers/counterfactual.py's module docstring); nothing else
     keeps that proxy set in sync as the service grows. A newly added
     service route with no gateway facade 404s in the real deployment while
     every other test (which calls the service functions directly, or the
     gateway routes that already existed) stays green — exactly how six
     routes (plus /jwks) went unreachable before this file existed.
     test_every_service_route_has_a_gateway_facade() diffs the two route
     sets so that can't happen silently again.

  2. The in-process-mount Depends() trap — the gateway calls the service's
     handlers as plain Python functions, so the service's OWN Depends()
     defaults are never resolved by FastAPI; every dependency has to be
     re-declared on the gateway route and forwarded explicitly. A missed one
     surfaces as a 500 (AttributeError on a raw Depends sentinel) or, worse,
     an auth check that silently never runs. The per-endpoint tests below
     assert "not 500" at minimum, with real auth-boundary + cross-tenant
     isolation assertions for the ledger routes that carry a tenant.
"""
from __future__ import annotations

import json
import os
import sys
import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.auth import create_access_token

V1 = "/api/v1"
_EXCLUDED_SERVICE_PATHS = {
    # Infra endpoints create_service() attaches independently to EVERY
    # service app (including the gateway's own), not proxied business
    # routes — comparing them would flag /metrics as "missing" forever.
    "/health", "/metrics", "/docs", "/redoc", "/openapi.json",
}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _user_token(sub: str, org: str) -> str:
    return create_access_token({"sub": sub, "org_id": org})


def _admin_token(sub: str = "admin-tester", org: str = "org-admin") -> str:
    return create_access_token({"sub": sub, "org_id": org, "role": "admin"})


# ── 1. Route-set drift guard ────────────────────────────────────────────

def _service_routes():
    from counterfactual_service.main import app as svc_app
    out = []
    for route in svc_app.routes:
        if not isinstance(route, APIRoute) or route.path in _EXCLUDED_SERVICE_PATHS:
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


def _expected_gateway_path(service_path: str) -> str:
    """Every service route — regardless of the service's own prefix — is
    hand-mounted under /api/v1/counterfactual/ (see counterfactual.py's
    module docstring). A leading '/counterfactual' segment on the service
    path is the one prefix that gets stripped before re-adding
    /api/v1/counterfactual, so it isn't doubled: service '/counterfactual/
    jobs' -> gateway '/api/v1/counterfactual/jobs'; service
    '/audit/financial' (no such prefix) -> gateway
    '/api/v1/counterfactual/audit/financial'."""
    stripped = (
        service_path[len("/counterfactual"):]
        if service_path.startswith("/counterfactual") else service_path
    )
    return f"{V1}/counterfactual{stripped}"


def test_every_service_route_has_a_gateway_facade():
    gateway_routes = _gateway_route_set()
    missing = [
        (method, service_path, _expected_gateway_path(service_path))
        for method, service_path in _service_routes()
        if (method, _expected_gateway_path(service_path)) not in gateway_routes
    ]
    assert not missing, f"service routes with no gateway facade: {missing}"


# ── 2. Per-endpoint reachability + Depends()-forwarding checks ─────────

@pytest.fixture()
def gw(tmp_path, monkeypatch):
    # Point the artifact store somewhere writable. Its default is
    # /var/log/aura/artifacts — correct in production (the Helm WORM PVC) but
    # absent and unwritable on a CI runner, where the store then reports
    # "error" instead of "not_found" for a hash that simply does not exist.
    # That is why this file passed on Windows and failed on Linux CI.
    # persistence.py resolves artifacts as <AURA_AUDIT_DIR>/../artifacts, so
    # this one variable covers both.
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path / "audit"))
    from api_gateway.main import app
    return TestClient(app)


def test_jwks_reachable_unauthenticated(gw):
    r = gw.get(f"{V1}/counterfactual/jwks")
    assert r.status_code == 200, r.text
    assert "keys" in r.json()


def test_admin_revoke_key_requires_admin_role(gw, tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_SIGNING_KEY_DIR", str(tmp_path / "keys"))

    # Anonymous refused.
    assert gw.post(f"{V1}/counterfactual/admin/revoke-key",
                   params={"kid": "k1"}).status_code == 401

    # Authenticated but not admin -> 403, not 500. This is the exact
    # Depends-forwarding trap: _require_admin must actually execute through
    # the gateway rather than arrive as an unresolved Depends sentinel,
    # which would either 500 or (worse) silently pass as truthy.
    non_admin = _bearer(_user_token("mallory", "org-x"))
    r = gw.post(f"{V1}/counterfactual/admin/revoke-key",
               params={"kid": "k1"}, headers=non_admin)
    assert r.status_code == 403, r.text

    # Admin succeeds.
    admin = _bearer(_admin_token())
    r = gw.post(f"{V1}/counterfactual/admin/revoke-key",
               params={"kid": "k1"}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["revoked_kid"] == "k1"


def test_replay_bulk_requires_auditor_role(gw):
    # Anonymous refused — replay/bulk is gated with _require_auditor at the
    # gateway even though the service function itself takes no user param
    # (see the comment above the route: existence-oracle + DoS guard).
    assert gw.post(f"{V1}/counterfactual/replay/bulk",
                   json={"hashes": ["a" * 64]}).status_code == 401

    non_auditor = _bearer(_user_token("mallory", "org-x"))
    r = gw.post(f"{V1}/counterfactual/replay/bulk",
               json={"hashes": ["a" * 64]}, headers=non_auditor)
    assert r.status_code == 403, r.text

    auditor = _bearer(create_access_token(
        {"sub": "auditor-tester", "org_id": "org-audit", "role": "auditor"}))
    r = gw.post(f"{V1}/counterfactual/replay/bulk",
               json={"hashes": ["a" * 64]}, headers=auditor)
    assert r.status_code == 200, r.text
    assert json.loads(r.text.strip().splitlines()[0])["status"] == "not_found"


def test_sth_reachable_unauthenticated(gw):
    r = gw.get(f"{V1}/counterfactual/audit/sth")
    # No audit records for "today" in a fresh test env -> 404 is the honest
    # answer here; the only thing under test is that the route resolves its
    # dependencies (no 500). See counterfactual.py's comment on why this
    # route is deliberately left tenant-agnostic (it's a single global RFC
    # 6962 log over shared.audit_log, a different store than the tenant-
    # scoped shared.audit_ledger the /audit/ledger/* routes use).
    assert r.status_code != 500, r.text


def test_inclusion_proof_reachable_unauthenticated(gw):
    r = gw.get(f"{V1}/counterfactual/audit/inclusion/{'a' * 64}")
    assert r.status_code != 500, r.text


# ── 3. Tenant-scoped ledger routes: auth required + cross-tenant isolation ─

@pytest.fixture()
def ledger_isolation_env(tmp_path, monkeypatch):
    """Point the ledger + artifact stores at a throwaway per-test directory
    and force a fresh async engine. The Merkle-proof/subject-history routes
    now require_tenant (see counterfactual.py) and read the same tenant-
    scoped AuditLedgerRow table as /audit/ledger/verify — this fixture lets
    the isolation test below prove the fix actually isolates tenants, not
    just that the route resolves."""
    from shared import audit_ledger as L
    monkeypatch.setenv(
        "AURA_LEDGER_DATABASE_URL",
        f"sqlite+aiosqlite:///{tmp_path / f'l_{uuid.uuid4().hex}.db'}",
    )
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path / "audit"))
    L._engine = None
    L._session_factory = None
    L._schema_initialized = False
    L._tenant_locks.clear()
    yield


def test_ledger_proof_and_subject_history_require_auth(gw, ledger_isolation_env):
    assert gw.get(f"{V1}/counterfactual/audit/ledger/proof/{'a' * 64}").status_code == 401
    assert gw.get(f"{V1}/counterfactual/audit/ledger/subject/model-1").status_code == 401


def test_ledger_proof_and_subject_history_isolate_tenants(ledger_isolation_env):
    """Tenant A's Merkle proof and subject history must be invisible to
    tenant B — mirrors test_e2e_auth_job_flow.py::
    test_another_org_cannot_poll_the_job. A cross-tenant read answers
    identically to a missing record (404 / empty history), never 403, so
    the response can't be used as an existence oracle for another org's
    audit trail.

    Uses `with TestClient(app) as client:` (not the bare `gw` fixture)
    because this test makes several sequential calls that all touch the
    async ledger DB engine: Starlette's TestClient only reuses one anyio
    portal (and therefore one event loop) across calls made INSIDE a
    `with` block — a bare `TestClient(app)` opens and tears down a fresh
    portal/loop for every individual call, and the second DB-touching call
    would then try to reuse the first call's already-closed aiosqlite
    engine and fail. Entering the context manager also happens to run the
    real gateway lifespan (api_gateway/main.py), including the newly-wired
    signing.validate_signing_config() call — if that broke dev startup,
    this test would fail before a single request is sent, which doubles as
    live evidence for Gap 2.
    """
    from api_gateway.main import app as gw_app

    owner = _bearer(_user_token("ada@bank.test", "org-a"))
    other = _bearer(_user_token("mallory", "org-b"))

    with TestClient(gw_app) as gw:
        seeded = gw.post(
            f"{V1}/counterfactual/audit/financial",
            json={
                "tenant_id": "attacker-supplied-ignored",
                "subject_id": "model-1",
                "preparer_id": "ada@bank.test",
                "ledger": [{"account": "cash", "amount": 100}],
                "journal_entries": [{"amount": 100, "id": 1}, {"amount": 200, "id": 2}],
            },
            headers=owner,
        )
        assert seeded.status_code == 200, seeded.text
        cert_hash = seeded.json()["record_hash"]

        # Owner (org-a) can read both.
        owner_proof = gw.get(f"{V1}/counterfactual/audit/ledger/proof/{cert_hash}", headers=owner)
        assert owner_proof.status_code == 200, owner_proof.text
        assert owner_proof.json()["cert_hash"] == cert_hash

        owner_hist = gw.get(f"{V1}/counterfactual/audit/ledger/subject/model-1", headers=owner)
        assert owner_hist.status_code == 200, owner_hist.text
        assert owner_hist.json()["count"] == 1

        # A different org (org-b) gets the "doesn't exist" answer, never org-a's data.
        intruder_proof = gw.get(f"{V1}/counterfactual/audit/ledger/proof/{cert_hash}", headers=other)
        assert intruder_proof.status_code == 404, intruder_proof.text
        assert cert_hash not in intruder_proof.text

        intruder_hist = gw.get(f"{V1}/counterfactual/audit/ledger/subject/model-1", headers=other)
        assert intruder_hist.status_code == 200, intruder_hist.text
        assert intruder_hist.json()["count"] == 0
