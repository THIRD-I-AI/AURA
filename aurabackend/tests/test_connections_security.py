"""Connections: tenant isolation and credential handling.

Three separate defects are pinned here, because all three shipped together:

  1. The store was a module-level dict with NO tenant filter, so
     GET /connections returned every organisation's connections — host, port,
     database, username — to any caller. That is a live cross-tenant
     disclosure, independent of whether the feature worked.
  2. The password supplied at creation was read off the request and then
     never stored, so no saved connection could ever authenticate.
  3. Connections lived only in memory, so they vanished on restart.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway import persistence

_PASSWORD = "s3cr3t-pw"


@pytest.fixture(autouse=True)
def _fresh_engine():
    """Rebind the async engine per test — pytest-asyncio gives each test its
    own event loop while the module-level engine stays bound to the first, so
    the second async test in a session deadlocks. Same reset as
    tests/test_dashboards_persistence.py."""
    persistence._engine = None
    persistence._session_factory = None
    persistence._schema_initialized = False
    yield


def _record(ws: str, name: str = "prod replica") -> dict:
    now = datetime.now(timezone.utc)
    return {
        # Unique per run: the store is durable, so a fixed id would collide
        # with its own previous execution.
        "id": f"conn_{uuid.uuid4().hex[:12]}",
        "workspace_id": ws,
        "name": name,
        "type": "postgresql",
        "host": "db.internal",
        "port": 5432,
        "database": "analytics",
        "username": "svc_reader",
        "ssl": True,
        "created_at": now.isoformat(),
        "created_ts": now.timestamp(),
        "updated_at": now.isoformat(),
    }


@pytest.mark.asyncio
async def test_password_is_stored_but_never_plaintext_or_on_the_wire():
    saved = await persistence.insert_connection(_record("ws-secret"), _PASSWORD)

    # The wire dict must not carry the secret in any form.
    assert "password" not in saved
    assert "password_encrypted" not in saved
    assert _PASSWORD not in str(saved)

    # At rest it is a Fernet token, not the plaintext.
    from sqlalchemy import select
    async with persistence.session_scope() as s:
        stored = (await s.execute(
            select(persistence.ConnectionRow.password_encrypted)
            .where(persistence.ConnectionRow.id == saved["id"]),
        )).scalar_one()
    assert stored and _PASSWORD not in stored

    # And it round-trips, so the connection can actually authenticate — the
    # password used to be discarded entirely, which is why nothing worked.
    assert await persistence.get_connection_secret(saved["id"], "ws-secret") == _PASSWORD


@pytest.mark.asyncio
async def test_connections_do_not_leak_across_tenants():
    mine = await persistence.insert_connection(_record("org-a"), _PASSWORD)

    # The listing leak: org-b must not see org-a's connection at all.
    assert await persistence.list_connections("org-b") == []
    # Nor resolve it by id — None becomes a 404 in the router, never a 403,
    # so the response cannot confirm the connection exists.
    assert await persistence.get_connection(mine["id"], "org-b") is None
    # Nor extract its credential.
    assert await persistence.get_connection_secret(mine["id"], "org-b") is None
    # Nor delete it.
    assert await persistence.delete_connection(mine["id"], "org-b") is False

    # The owner is unaffected.
    still = await persistence.get_connection(mine["id"], "org-a")
    assert still is not None and still["host"] == "db.internal"


@pytest.mark.asyncio
async def test_connection_without_password_is_allowed():
    """Some connectors authenticate by other means (e.g. BigQuery service
    accounts), so a missing password must not be an error — but it must also
    not produce a bogus empty-string credential."""
    saved = await persistence.insert_connection(_record("ws-nopw"), None)
    assert await persistence.get_connection_secret(saved["id"], "ws-nopw") is None


@pytest.mark.asyncio
async def test_test_status_is_recorded_for_success_and_failure():
    saved = await persistence.insert_connection(_record("ws-status"), _PASSWORD)
    assert saved["is_active"] is False

    ok = await persistence.update_connection_status(
        saved["id"], "ws-status", is_active=True,
        last_tested="2026-08-05T00:00:00", table_count=7,
    )
    assert ok is not None and ok["is_active"] is True and ok["table_count"] == 7

    # A later failure must clear the flag rather than leave a stale "active"
    # badge from an earlier success.
    bad = await persistence.update_connection_status(
        saved["id"], "ws-status", is_active=False,
        last_tested="2026-08-05T01:00:00",
    )
    assert bad is not None and bad["is_active"] is False


@pytest.mark.asyncio
async def test_decrypting_with_a_changed_key_fails_loudly():
    """A wrong key must raise, not return a plausible-but-wrong credential —
    that would surface as a confusing auth failure against the customer's own
    database instead of pointing at key rotation."""
    from cryptography.fernet import Fernet

    from shared import credentials

    saved = await persistence.insert_connection(_record("ws-rotate"), _PASSWORD)

    credentials.reset_cache()
    credentials._fernet = Fernet(Fernet.generate_key())
    try:
        with pytest.raises(credentials.CredentialEncryptionError):
            await persistence.get_connection_secret(saved["id"], "ws-rotate")
    finally:
        credentials.reset_cache()
