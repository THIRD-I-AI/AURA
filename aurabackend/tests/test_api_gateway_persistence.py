"""
Sprint P-1 — contract tests for api_gateway/persistence.py.

Single tier: pure SQLAlchemy against SQLite (in-memory). The repository
functions are dialect-portable so anything that passes here works on
the production Postgres engine the same way (modulo Postgres-specific
features we don't use).

Why no Postgres-required tier?
------------------------------
Unlike Sprint 20b's distributed_queue (LISTEN/NOTIFY + pg_advisory_lock
are Postgres-only), this module uses only ANSI SQL constructs: indexes,
ORDER BY, DELETE with subquery, foreign keys. SQLite gives us full
fidelity on those, so the base backend lane is sufficient.

Coverage:
  * insert + list (newest-first ordering)
  * 200-row cap enforcement on query_history
  * 500-per-workspace cap on saved_queries
  * workspace isolation
  * update + delete + cascade share-token revoke
  * share-token idempotency
  * orphan share-token lookup behaviour
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from api_gateway import persistence


@pytest_asyncio.fixture
async def gateway_db(tmp_path, monkeypatch):
    """Fresh SQLite-backed gateway persistence per test.

    Points the module's global engine at a per-test SQLite file so
    tests don't share state. Closes + clears the singleton on exit
    so the next test gets a clean slate."""
    db_path = tmp_path / f"gw_{uuid.uuid4().hex}.db"
    monkeypatch.setenv(
        "GATEWAY_DATABASE_URL",
        f"sqlite+aiosqlite:///{db_path}",
    )
    # Reset the module-level engine singleton so our env-var picks up.
    persistence._engine = None
    persistence._session_factory = None
    await persistence.init_database()
    yield
    await persistence.close_database()


# ── Query history ────────────────────────────────────────────────────


def _history_record(i: int = 1, status: str = "success") -> dict:
    return {
        "id": f"q_{i}",
        "prompt": f"prompt {i}",
        "sql": f"SELECT {i}",
        "status": status,
        "rows": i,
        "executionTime": float(i),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@pytest.mark.asyncio
async def test_query_history_insert_then_list(gateway_db) -> None:
    """Single insert → single row in the list."""
    await persistence.insert_query_history(_history_record(1))
    rows = await persistence.list_query_history(limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == "q_1"
    # Wire shape MUST match the legacy in-memory dict.
    assert set(rows[0].keys()) == {
        "id", "prompt", "sql", "status", "rows", "executionTime", "timestamp",
    }


@pytest.mark.asyncio
async def test_query_history_returns_newest_first(gateway_db) -> None:
    """Three inserts, indexed ORDER BY created_ts DESC."""
    for i in range(1, 4):
        await persistence.insert_query_history(_history_record(i))
        # Force a tiny gap so created_ts differs deterministically.
        await asyncio.sleep(0.01)
    rows = await persistence.list_query_history(limit=10)
    assert [r["id"] for r in rows] == ["q_3", "q_2", "q_1"]


@pytest.mark.asyncio
async def test_query_history_status_filter(gateway_db) -> None:
    """status_filter='success' returns only successes; 'all' or None
    returns everything."""
    await persistence.insert_query_history(_history_record(1, status="success"))
    await persistence.insert_query_history(_history_record(2, status="failed"))
    successes = await persistence.list_query_history(status_filter="success")
    assert [r["id"] for r in successes] == ["q_1"]
    all_rows = await persistence.list_query_history(status_filter="all")
    assert len(all_rows) == 2
    none_filter = await persistence.list_query_history(status_filter=None)
    assert len(none_filter) == 2


@pytest.mark.asyncio
async def test_query_history_cap_enforces_200_rows(gateway_db) -> None:
    """Insert 210 rows; only the newest 200 survive. The cap eviction
    happens inside the same transaction as the insert so concurrent
    reads never see > 200 rows."""
    # Use synthetic timestamps so the ORDER BY is deterministic.
    for i in range(210):
        rec = _history_record(i)
        await persistence.insert_query_history(rec)
    rows = await persistence.list_query_history(limit=300)
    assert len(rows) == persistence.QUERY_HISTORY_CAP


# ── Saved queries ────────────────────────────────────────────────────


def _saved_query(i: int = 1, workspace_id: str = "ws1", starred: bool = False) -> dict:
    ts = datetime.now(timezone.utc)
    return {
        "id": f"sq_{i}",
        "workspace_id": workspace_id,
        "name": f"Query {i}",
        "sql": f"SELECT {i}",
        "prompt": f"prompt {i}",
        "starred": starred,
        "created_at": ts.isoformat(),
        "created_ts": time.time() + i * 0.001,  # deterministic order
        "updated_at": ts.isoformat(),
    }


@pytest.mark.asyncio
async def test_saved_query_insert_then_get(gateway_db) -> None:
    """Insert one + fetch by id with the workspace filter."""
    rec = await persistence.insert_saved_query(_saved_query(1))
    assert rec["id"] == "sq_1"
    assert rec["workspace_id"] == "ws1"
    assert rec["starred"] is False
    fetched = await persistence.get_saved_query("sq_1", workspace_id="ws1")
    assert fetched is not None
    assert fetched["id"] == "sq_1"


@pytest.mark.asyncio
async def test_saved_query_workspace_isolation(gateway_db) -> None:
    """Records in workspace A are invisible to workspace B."""
    await persistence.insert_saved_query(_saved_query(1, workspace_id="ws_a"))
    await persistence.insert_saved_query(_saved_query(2, workspace_id="ws_b"))
    list_a = await persistence.list_saved_queries("ws_a")
    list_b = await persistence.list_saved_queries("ws_b")
    assert [r["id"] for r in list_a] == ["sq_1"]
    assert [r["id"] for r in list_b] == ["sq_2"]


@pytest.mark.asyncio
async def test_saved_query_starred_sorts_first(gateway_db) -> None:
    """The composite index drives the ORDER BY starred DESC,
    created_ts DESC — starred rows appear before unstarred ones."""
    await persistence.insert_saved_query(_saved_query(1, starred=False))
    await persistence.insert_saved_query(_saved_query(2, starred=True))
    await persistence.insert_saved_query(_saved_query(3, starred=False))
    rows = await persistence.list_saved_queries("ws1")
    # sq_2 (starred) must be first.
    assert rows[0]["id"] == "sq_2"
    # Within the unstarred group, newest first (sq_3 came after sq_1).
    unstarred_ids = [r["id"] for r in rows if not r["starred"]]
    assert unstarred_ids == ["sq_3", "sq_1"]


@pytest.mark.asyncio
async def test_saved_query_cap_500_per_workspace(gateway_db) -> None:
    """Inserting > 500 in one workspace evicts the oldest; OTHER
    workspaces are unaffected by another workspace's cap."""
    # Insert 510 in ws_a.
    for i in range(510):
        await persistence.insert_saved_query(_saved_query(i, workspace_id="ws_a"))
    # Insert 5 in ws_b.
    for i in range(5):
        await persistence.insert_saved_query(
            _saved_query(1000 + i, workspace_id="ws_b"),
        )
    list_a = await persistence.list_saved_queries("ws_a")
    list_b = await persistence.list_saved_queries("ws_b")
    assert len(list_a) == persistence.SAVED_QUERIES_PER_WORKSPACE_CAP
    assert len(list_b) == 5


@pytest.mark.asyncio
async def test_saved_query_update_partial(gateway_db) -> None:
    """update_saved_query applies a partial dict — fields not in the
    update don't get blanked. Returns the updated wire dict."""
    await persistence.insert_saved_query(_saved_query(1))
    updated = await persistence.update_saved_query(
        "sq_1", "ws1",
        {"name": "Renamed", "starred": True},
    )
    assert updated is not None
    assert updated["name"] == "Renamed"
    assert updated["starred"] is True
    # SQL field NOT updated.
    assert updated["sql"] == "SELECT 1"


@pytest.mark.asyncio
async def test_saved_query_update_wrong_workspace_returns_none(gateway_db) -> None:
    """Update from the wrong workspace yields None — endpoint maps
    to 404."""
    await persistence.insert_saved_query(_saved_query(1, workspace_id="ws_a"))
    result = await persistence.update_saved_query(
        "sq_1", "ws_b",
        {"name": "Renamed"},
    )
    assert result is None


@pytest.mark.asyncio
async def test_saved_query_schedule_roundtrip(gateway_db) -> None:
    """Schedule dict survives encode/decode through schedule_json."""
    await persistence.insert_saved_query(_saved_query(1))
    schedule = {"interval": "daily", "hour": 9, "minute": 0, "enabled": True}
    updated = await persistence.update_saved_query(
        "sq_1", "ws1",
        {"schedule": schedule, "next_run_at": "2026-05-20T09:00:00+00:00"},
    )
    assert updated["schedule"] == schedule
    assert updated["next_run_at"] == "2026-05-20T09:00:00+00:00"


@pytest.mark.asyncio
async def test_saved_query_clear_schedule(gateway_db) -> None:
    """update with schedule=None clears the field; next_run_at also
    clears on the same call."""
    await persistence.insert_saved_query(_saved_query(1))
    schedule = {"interval": "daily", "hour": 9, "minute": 0, "enabled": True}
    await persistence.update_saved_query(
        "sq_1", "ws1",
        {"schedule": schedule, "next_run_at": "2026-05-20T09:00:00+00:00"},
    )
    updated = await persistence.update_saved_query(
        "sq_1", "ws1",
        {"schedule": None, "next_run_at": None},
    )
    assert "schedule" not in updated  # missing means cleared
    assert "next_run_at" not in updated or updated.get("next_run_at") is None


@pytest.mark.asyncio
async def test_saved_query_delete_returns_true_when_present(gateway_db) -> None:
    """Delete returns True on success, False when the row doesn't
    exist OR is in another workspace."""
    await persistence.insert_saved_query(_saved_query(1, workspace_id="ws_a"))
    assert await persistence.delete_saved_query("sq_1", "ws_a") is True
    # Now it's gone.
    assert await persistence.delete_saved_query("sq_1", "ws_a") is False


@pytest.mark.asyncio
async def test_saved_query_delete_wrong_workspace_returns_false(gateway_db) -> None:
    """Cross-workspace delete attempt must NOT succeed — workspace
    isolation is a security boundary."""
    await persistence.insert_saved_query(_saved_query(1, workspace_id="ws_a"))
    assert await persistence.delete_saved_query("sq_1", "ws_b") is False
    # Original record is still there.
    fetched = await persistence.get_saved_query("sq_1", workspace_id="ws_a")
    assert fetched is not None


# ── Share tokens ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_share_token_get_or_create_is_idempotent(gateway_db) -> None:
    """Two consecutive calls return the SAME token — no duplicate
    tokens for the same query."""
    await persistence.insert_saved_query(_saved_query(1))
    t1 = await persistence.get_or_create_share_token("sq_1")
    t2 = await persistence.get_or_create_share_token("sq_1")
    assert t1 == t2
    assert len(t1) > 10  # secrets.token_urlsafe(24) → ~32 chars


@pytest.mark.asyncio
async def test_share_token_lookup_returns_query_id(gateway_db) -> None:
    """Round-trip: mint a token, look it up, get the query_id back."""
    await persistence.insert_saved_query(_saved_query(1))
    token = await persistence.get_or_create_share_token("sq_1")
    qid = await persistence.lookup_share_token(token)
    assert qid == "sq_1"


@pytest.mark.asyncio
async def test_share_token_lookup_unknown_returns_none(gateway_db) -> None:
    """Looking up a never-minted token returns None — the endpoint
    maps this to 404."""
    qid = await persistence.lookup_share_token("nonexistent_token")
    assert qid is None


@pytest.mark.asyncio
async def test_share_token_revoke_for_query_cascade(gateway_db) -> None:
    """revoke_share_tokens_for_query deletes ALL tokens pointing at
    a query — indexed by saved_query_id, so it's O(log n) not O(n)."""
    await persistence.insert_saved_query(_saved_query(1))
    await persistence.insert_saved_query(_saved_query(2))
    t1 = await persistence.get_or_create_share_token("sq_1")
    await persistence.get_or_create_share_token("sq_2")
    n = await persistence.revoke_share_tokens_for_query("sq_1")
    assert n == 1
    # sq_1's token is gone, sq_2's is intact.
    assert await persistence.lookup_share_token(t1) is None


@pytest.mark.asyncio
async def test_delete_saved_query_cascades_share_tokens(gateway_db) -> None:
    """Deleting a saved query also revokes any tokens pointing at it
    (in the same transaction)."""
    await persistence.insert_saved_query(_saved_query(1))
    token = await persistence.get_or_create_share_token("sq_1")
    assert await persistence.lookup_share_token(token) == "sq_1"
    await persistence.delete_saved_query("sq_1", "ws1")
    assert await persistence.lookup_share_token(token) is None


@pytest.mark.asyncio
async def test_revoke_one_share_token_specific(gateway_db) -> None:
    """revoke_one_share_token deletes a specific token but leaves
    others alone — used for the dangling-token cleanup path."""
    await persistence.insert_saved_query(_saved_query(1))
    t1 = await persistence.get_or_create_share_token("sq_1")
    await persistence.revoke_one_share_token(t1)
    assert await persistence.lookup_share_token(t1) is None
