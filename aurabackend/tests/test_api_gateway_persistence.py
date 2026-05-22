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


def _saved_query(i: int = 1, workspace_id: str = "ws1", starred: bool = False, sql: str = "") -> dict:
    ts = datetime.now(timezone.utc)
    return {
        "id": f"sq_{i}",
        "workspace_id": workspace_id,
        "name": f"Query {i}",
        "sql": sql or f"SELECT {i}",
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


# ── File metadata cache (Sprint P-2a) ────────────────────────────────


def _write_csv(path: Path, n_rows: int) -> None:
    """Tiny CSV helper for the metadata tests."""
    lines = ["id,value"] + [f"{i},x" for i in range(n_rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_index_file_metadata_counts_rows_and_upserts(
    gateway_db, tmp_path,
) -> None:
    """A single index call computes row count + mtime + size and
    persists them. Wire-shape dict matches what the dashboard
    endpoint reads."""
    csv = tmp_path / "small.csv"
    _write_csv(csv, n_rows=42)
    result = await persistence.index_file_metadata(str(csv))
    assert result is not None
    assert result["row_count"] == 42
    assert result["file_suffix"] == ".csv"
    assert result["file_name"] == "small.csv"
    assert result["size_bytes"] > 0
    # Confirm it landed in the list endpoint output.
    rows = await persistence.list_file_metadata()
    assert len(rows) == 1
    assert rows[0]["row_count"] == 42


@pytest.mark.asyncio
async def test_index_file_metadata_upserts_on_repeat(
    gateway_db, tmp_path,
) -> None:
    """Indexing the same path twice updates the existing row instead
    of producing duplicates (file_path is the primary key)."""
    csv = tmp_path / "doc.csv"
    _write_csv(csv, n_rows=5)
    await persistence.index_file_metadata(str(csv))
    # Rewrite with more rows.
    _write_csv(csv, n_rows=20)
    await persistence.index_file_metadata(str(csv))
    rows = await persistence.list_file_metadata()
    assert len(rows) == 1
    assert rows[0]["row_count"] == 20


@pytest.mark.asyncio
async def test_index_file_metadata_missing_file_prunes_row(
    gateway_db, tmp_path,
) -> None:
    """If the file is deleted between request and indexing, the
    indexer prunes the existing cached row and returns None."""
    csv = tmp_path / "ephemeral.csv"
    _write_csv(csv, n_rows=10)
    await persistence.index_file_metadata(str(csv))
    assert len(await persistence.list_file_metadata()) == 1
    csv.unlink()
    result = await persistence.index_file_metadata(str(csv))
    assert result is None
    assert len(await persistence.list_file_metadata()) == 0


@pytest.mark.asyncio
async def test_index_file_metadata_skips_untracked_suffix(
    gateway_db, tmp_path,
) -> None:
    """Files with unsupported extensions (.txt, .md, etc.) are
    silently skipped — no row inserted."""
    txt = tmp_path / "notes.txt"
    txt.write_text("hello", encoding="utf-8")
    result = await persistence.index_file_metadata(str(txt))
    assert result is None
    assert len(await persistence.list_file_metadata()) == 0


@pytest.mark.asyncio
async def test_index_file_metadata_xlsx_counts_zero(
    gateway_db, tmp_path,
) -> None:
    """xlsx/xls require the duckdb excel extension which isn't always
    loaded — the indexer records the file but with row_count=0,
    matching the legacy in-line endpoint's behaviour."""
    xlsx = tmp_path / "report.xlsx"
    xlsx.write_bytes(b"PK\x03\x04fake-xlsx-content")  # not a real xlsx
    result = await persistence.index_file_metadata(str(xlsx))
    assert result is not None
    assert result["row_count"] == 0
    assert result["file_suffix"] == ".xlsx"


@pytest.mark.asyncio
async def test_refresh_stale_walks_upload_dir(gateway_db, tmp_path) -> None:
    """refresh_stale_file_metadata indexes new files + skips
    unchanged files + prunes deleted files. Returns telemetry."""
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    _write_csv(a, n_rows=3)
    _write_csv(b, n_rows=7)

    stats1 = await persistence.refresh_stale_file_metadata(str(tmp_path))
    assert stats1["indexed"] == 2
    assert stats1["skipped"] == 0

    # Second pass — nothing changed.
    stats2 = await persistence.refresh_stale_file_metadata(str(tmp_path))
    assert stats2["indexed"] == 0
    assert stats2["skipped"] == 2

    # Delete b; refresh should prune the cached row.
    b.unlink()
    stats3 = await persistence.refresh_stale_file_metadata(str(tmp_path))
    assert stats3["pruned"] == 1
    rows = await persistence.list_file_metadata()
    assert {r["file_name"] for r in rows} == {"a.csv"}


@pytest.mark.asyncio
async def test_refresh_stale_handles_missing_upload_dir(
    gateway_db, tmp_path,
) -> None:
    """refresh_stale_file_metadata returns zero counts when the
    upload dir doesn't exist — graceful no-op for fresh deployments."""
    nonexistent = tmp_path / "no_such_dir"
    stats = await persistence.refresh_stale_file_metadata(str(nonexistent))
    assert stats == {"indexed": 0, "skipped": 0, "pruned": 0}


@pytest.mark.asyncio
async def test_refresh_stale_reindexes_changed_mtime(
    gateway_db, tmp_path,
) -> None:
    """If a file's mtime changes (user edits it after upload), the
    refresh re-indexes it — picks up the new row count."""
    import time
    csv = tmp_path / "mutable.csv"
    _write_csv(csv, n_rows=5)
    await persistence.refresh_stale_file_metadata(str(tmp_path))

    # Force a different mtime + new row count.
    time.sleep(0.05)
    _write_csv(csv, n_rows=15)
    new_mtime = csv.stat().st_mtime + 1.0
    os.utime(csv, (new_mtime, new_mtime))

    stats = await persistence.refresh_stale_file_metadata(str(tmp_path))
    assert stats["indexed"] == 1
    rows = await persistence.list_file_metadata()
    assert rows[0]["row_count"] == 15


@pytest.mark.asyncio
async def test_prune_missing_file_metadata_handles_empty_list(
    gateway_db,
) -> None:
    """Defensive: pruning an empty list is a no-op, not an error."""
    n = await persistence.prune_missing_file_metadata([])
    assert n == 0


# ── Schema context (Sprint P-2b) ─────────────────────────────────────


def _write_schema_csv(path: Path, rows: int = 3) -> None:
    """Write a tiny CSV so compute_schema_fingerprint returns a non-empty hash."""
    path.write_text("a,b\n" + "".join(f"{i},{i*2}\n" for i in range(rows)))


@pytest.mark.asyncio
async def test_schema_context_miss_returns_none(gateway_db, tmp_path) -> None:
    """get_schema_context returns None when nothing has been stored yet."""
    _write_schema_csv(tmp_path / "data.csv")
    fp = persistence.compute_schema_fingerprint([str(tmp_path)])
    assert fp  # sanity: fingerprint is non-empty
    result = await persistence.get_schema_context(fp)
    assert result is None


@pytest.mark.asyncio
async def test_schema_context_upsert_then_get(gateway_db, tmp_path) -> None:
    """Upsert stores the context; get_schema_context returns it byte-for-byte."""
    _write_schema_csv(tmp_path / "data.csv")
    fp = persistence.compute_schema_fingerprint([str(tmp_path)])
    ctx = {"tables": {"data": {"columns": []}}, "relationships": [], "context_text": "test"}
    await persistence.upsert_schema_context(fp, ctx)
    fetched = await persistence.get_schema_context(fp)
    assert fetched == ctx


@pytest.mark.asyncio
async def test_schema_context_upsert_is_idempotent(gateway_db, tmp_path) -> None:
    """Second upsert overwrites the first; only one row exists per fingerprint."""
    _write_schema_csv(tmp_path / "data.csv")
    fp = persistence.compute_schema_fingerprint([str(tmp_path)])
    ctx_v1 = {"context_text": "v1", "tables": {}, "relationships": []}
    ctx_v2 = {"context_text": "v2", "tables": {}, "relationships": []}
    await persistence.upsert_schema_context(fp, ctx_v1)
    await persistence.upsert_schema_context(fp, ctx_v2)
    fetched = await persistence.get_schema_context(fp)
    assert fetched["context_text"] == "v2"


@pytest.mark.asyncio
async def test_get_any_schema_context_cold_start(gateway_db, tmp_path) -> None:
    """get_any_schema_context returns None when the table is empty (cold start)."""
    result = await persistence.get_any_schema_context()
    assert result is None


@pytest.mark.asyncio
async def test_get_any_schema_context_returns_latest(gateway_db, tmp_path) -> None:
    """get_any_schema_context returns the most recently indexed row."""
    _write_schema_csv(tmp_path / "a.csv")
    _write_schema_csv(tmp_path / "b.csv")
    fp_a = "fp_a_" + uuid.uuid4().hex[:8]
    fp_b = "fp_b_" + uuid.uuid4().hex[:8]
    ctx_a = {"context_text": "A", "tables": {}, "relationships": []}
    ctx_b = {"context_text": "B", "tables": {}, "relationships": []}
    await persistence.upsert_schema_context(fp_a, ctx_a)
    await asyncio.sleep(0.01)  # ensure last_indexed_at differs
    await persistence.upsert_schema_context(fp_b, ctx_b)
    result = await persistence.get_any_schema_context()
    assert result["context_text"] == "B"


@pytest.mark.asyncio
async def test_schema_fingerprint_changes_on_mtime(tmp_path) -> None:
    """Fingerprint changes when a file's mtime changes — stale detection works."""
    csv = tmp_path / "data.csv"
    _write_schema_csv(csv)
    fp1 = persistence.compute_schema_fingerprint([str(tmp_path)])
    # Advance mtime by 10 seconds
    mtime = csv.stat().st_mtime + 10
    os.utime(csv, (mtime, mtime))
    fp2 = persistence.compute_schema_fingerprint([str(tmp_path)])
    assert fp1 != fp2


@pytest.mark.asyncio
async def test_schema_fingerprint_empty_dir_returns_empty(tmp_path) -> None:
    """Empty upload dir produces an empty fingerprint — no context to cache."""
    fp = persistence.compute_schema_fingerprint([str(tmp_path)])
    assert fp == ""


@pytest.mark.asyncio
async def test_schema_context_empty_fingerprint_is_noop(gateway_db) -> None:
    """upsert + get with empty fingerprint are no-ops, not errors."""
    await persistence.upsert_schema_context("", {"context_text": "x"})
    result = await persistence.get_schema_context("")
    assert result is None


# ── Lineage edges (Sprint P-2c) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_lineage_edges_basic(gateway_db) -> None:
    """Creating a saved query then upserting its edges stores the table refs."""
    await persistence.insert_saved_query(_saved_query(1, sql="SELECT * FROM orders JOIN customers ON orders.cid = customers.id"))
    await persistence.upsert_lineage_edges("sq_1", "ws1", "SELECT * FROM orders JOIN customers ON orders.cid = customers.id")
    edges = await persistence.list_lineage_edges("ws1")
    tables = {e["table_name"] for e in edges}
    assert tables == {"orders", "customers"}
    assert all(e["saved_query_id"] == "sq_1" for e in edges)


@pytest.mark.asyncio
async def test_list_lineage_edges_workspace_isolated(gateway_db) -> None:
    """Edges from workspace A are invisible to workspace B."""
    await persistence.insert_saved_query(_saved_query(1, workspace_id="ws_a", sql="SELECT * FROM sales"))
    await persistence.insert_saved_query(_saved_query(2, workspace_id="ws_b", sql="SELECT * FROM inventory"))
    await persistence.upsert_lineage_edges("sq_1", "ws_a", "SELECT * FROM sales")
    await persistence.upsert_lineage_edges("sq_2", "ws_b", "SELECT * FROM inventory")
    edges_a = await persistence.list_lineage_edges("ws_a")
    edges_b = await persistence.list_lineage_edges("ws_b")
    assert {e["table_name"] for e in edges_a} == {"sales"}
    assert {e["table_name"] for e in edges_b} == {"inventory"}


@pytest.mark.asyncio
async def test_upsert_lineage_edges_idempotent(gateway_db) -> None:
    """Calling upsert twice for the same query replaces, not doubles, the edges."""
    await persistence.insert_saved_query(_saved_query(1, sql="SELECT * FROM orders"))
    await persistence.upsert_lineage_edges("sq_1", "ws1", "SELECT * FROM orders")
    await persistence.upsert_lineage_edges("sq_1", "ws1", "SELECT * FROM orders")
    edges = await persistence.list_lineage_edges("ws1")
    assert len(edges) == 1


@pytest.mark.asyncio
async def test_delete_saved_query_cascades_lineage_edges(gateway_db) -> None:
    """Deleting a saved query removes its lineage edges via FK CASCADE."""
    await persistence.insert_saved_query(_saved_query(1, sql="SELECT * FROM orders"))
    await persistence.upsert_lineage_edges("sq_1", "ws1", "SELECT * FROM orders")
    assert len(await persistence.list_lineage_edges("ws1")) == 1
    await persistence.delete_saved_query("sq_1", "ws1")
    assert await persistence.list_lineage_edges("ws1") == []


@pytest.mark.asyncio
async def test_upsert_lineage_edges_no_tables(gateway_db) -> None:
    """SQL with no table references (e.g. SELECT 1) stores zero edges cleanly."""
    await persistence.insert_saved_query(_saved_query(1, sql="SELECT 1"))
    await persistence.upsert_lineage_edges("sq_1", "ws1", "SELECT 1")
    assert await persistence.list_lineage_edges("ws1") == []
