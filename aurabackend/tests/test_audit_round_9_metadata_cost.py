"""Audit round 9 — metadata_store admin seed and Cost panel regressions.

BUG-513: the AURA_ADMIN_EMAIL seed clashed with the lower(email) unique index
(crash-looped startup) and its passwordless 'admin' row locked the address out
of /auth/register and /auth/token.
BUG-515: GET /llm-stats returned the process-global token counter (every
tenant's usage) to any signed-in user.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import create_engine, text

from tests.test_auth import V1, password_client  # noqa: F401  (pytest fixture)


def _make_db(tmp_path):
    from metadata_store.models import Base
    db_path = tmp_path / "seed.db"
    eng = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email_lower ON users (lower(email))"))
    return eng, db_path


# ── BUG-513 ─────────────────────────────────────────────────────────────

def test_bug513_admin_seed_does_not_crash_when_email_already_registered(tmp_path):
    """Case A: ops registered first; the seed must not raise IntegrityError
    (which aborted the lifespan) nor create a second row for the address."""
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from metadata_store.main import _seed_admin_user
    from metadata_store.repository import MetadataRepository

    eng, db_path = _make_db(tmp_path)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, name, email, password_hash, role, org_id, created_at, updated_at) "
            "VALUES (:id, 'Ops', 'ops@corp.com', 'h', 'user', :org, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
            {"id": str(uuid.uuid4()), "org": str(uuid.uuid4())})

    async def run():
        aeng = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            factory = async_sessionmaker(aeng, expire_on_commit=False)
            # The pre-fix lifespan made exactly this call; it raised and aborted startup.
            async with factory() as session:
                with pytest.raises(IntegrityError):
                    await MetadataRepository(session).upsert_user(
                        "admin", name="AURA Admin", email="OPS@corp.com")
            async with factory() as session:
                await _seed_admin_user(session, "OPS@corp.com")
        finally:
            await aeng.dispose()

    asyncio.run(run())

    with eng.connect() as conn:
        rows = conn.execute(text("SELECT id FROM users WHERE lower(email) = 'ops@corp.com'")).all()
    eng.dispose()
    assert len(rows) == 1 and rows[0][0] != "admin"


def test_bug513_seeded_admin_email_can_register_and_log_in(password_client):  # noqa: F811
    """Case B: the seed ran first (id='admin', no password, no org); the
    operator must still be able to register that address and sign in."""
    from metadata_store import db as db_mod

    sync_url = db_mod.DATABASE_URL.replace("sqlite+aiosqlite", "sqlite")
    eng = create_engine(sync_url)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, name, email, role, created_at, updated_at) "
            "VALUES ('admin', 'AURA Admin', 'ops@corp.com', 'user', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
    eng.dispose()

    reg = {"email": "ops@corp.com", "password": "Sup3r-secret-pw!", "name": "Ops"}
    resp = password_client.post(f"{V1}/auth/register", json=reg)
    assert resp.status_code == 201, resp.text
    assert resp.json()["sub"] != "admin"

    tok = password_client.post(f"{V1}/auth/token",
                               json={"email": reg["email"], "password": reg["password"]})
    assert tok.status_code == 200, tok.text

    # A real (credentialed) account still conflicts as before.
    again = password_client.post(f"{V1}/auth/register", json=reg)
    assert again.status_code == 409


# ── BUG-515 ─────────────────────────────────────────────────────────────

@pytest.fixture()
def llm_stats_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api_gateway.routers.queries import router
    from shared.middleware import JWTAuthMiddleware

    app = FastAPI()
    app.add_middleware(JWTAuthMiddleware)
    app.include_router(router, prefix=V1)
    return TestClient(app)


def _bearer(role: str) -> dict:
    from shared.auth import create_access_token
    tok = create_access_token({"sub": f"u-{role}", "role": role, "org_id": f"org-{role}"})
    return {"Authorization": f"Bearer {tok}"}


def test_bug515_llm_stats_hidden_from_non_admin_tenant(llm_stats_client):
    from shared.observability import LLM_TOKENS
    model = f"org-b-model-{uuid.uuid4().hex[:8]}"
    LLM_TOKENS.labels(provider="openai", model=model, kind="prompt").inc(500)

    resp = llm_stats_client.get(f"{V1}/llm-stats", headers=_bearer("user"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["rows"] == []
    assert body["totals"]["prompt"] == 0
    assert model not in resp.text


def test_bug515_llm_stats_still_visible_to_admin(llm_stats_client):
    from shared.observability import LLM_TOKENS
    model = f"platform-model-{uuid.uuid4().hex[:8]}"
    LLM_TOKENS.labels(provider="openai", model=model, kind="prompt").inc(7)

    resp = llm_stats_client.get(f"{V1}/llm-stats", headers=_bearer("admin"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert any(r["model"] == model and r["tokens"] == 7 for r in body["rows"])
