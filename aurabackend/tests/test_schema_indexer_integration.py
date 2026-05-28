"""
Sprint S34 — Schema Indexer + MCP query-pattern integration tests.

Tier A (pure Python, no optional deps).

Covers the indexer→search round-trip:
  * _upsert_columns persists SchemaColumn rows correctly
  * Replace-by-(source_id, table_name) — re-uploads don't accumulate
  * MCP-style queries (column_name_lower LIKE :pat) retrieve the
    indexed rows by keyword
  * Per-table describe queries (source_id + table_name + ordinal order)
  * Multi-table sources are isolated

This crosses shared.schema_indexer ↔ metadata_store ↔ the SQL query
patterns the MCP server runs against schema_columns. The full DuckDB
introspection path (_introspect_via_duckdb) is excluded since it
requires DuckDB; we test the persistence + retrieval contract that
sits on top.
"""
from __future__ import annotations

import os
import sys

import pytest
from sqlalchemy import select, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metadata_store.db import Base
from metadata_store.models import SchemaColumn


@pytest.fixture
async def engine(monkeypatch):
    """In-memory async SQLite engine wired into metadata_store.db so
    schema_indexer._upsert_columns picks it up via get_session_factory()."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    import metadata_store.db as db_mod

    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)

    monkeypatch.setattr(db_mod, "_engine", eng)
    monkeypatch.setattr(db_mod, "_session_factory", factory)

    yield eng

    await eng.dispose()


def _rows(table: str, columns):
    """Build the row payload shape that _upsert_columns expects."""
    return [
        {
            "table_name": table,
            "column_name": col["name"],
            "data_type": col.get("type", "VARCHAR"),
            "is_nullable": col.get("nullable", True),
            "ordinal_position": idx + 1,
            "sample_values": col.get("samples", []),
        }
        for idx, col in enumerate(columns)
    ]


# ── Persistence ──────────────────────────────────────────────────

@pytest.mark.asyncio
class TestUpsertColumns:
    async def test_persists_one_row_per_column(self, engine):
        from shared.schema_indexer import _upsert_columns

        rows = _rows("sales", [
            {"name": "id", "type": "INTEGER", "nullable": False},
            {"name": "revenue", "type": "DOUBLE", "nullable": True, "samples": [100.0, 200.0]},
            {"name": "region", "type": "VARCHAR", "nullable": True},
        ])
        result = await _upsert_columns("src1", rows)
        assert result == {"tables": 1, "columns": 3}

    async def test_writes_lowercase_column_name(self, engine):
        from shared.schema_indexer import _upsert_columns

        rows = _rows("sales", [{"name": "RevenueTotal"}])
        await _upsert_columns("src1", rows)

        from sqlalchemy.ext.asyncio import AsyncSession

        from metadata_store.db import get_session_factory
        factory = get_session_factory()
        async with factory() as sess:
            sc = (await sess.execute(select(SchemaColumn))).scalar_one()
            assert sc.column_name == "RevenueTotal"
            assert sc.column_name_lower == "revenuetotal"


# ── Replace-by-(source_id, table_name) ────────────────────────────

@pytest.mark.asyncio
class TestIdempotentReupload:
    async def test_reupload_replaces_not_accumulates(self, engine):
        from shared.schema_indexer import _upsert_columns

        v1 = _rows("sales", [
            {"name": "id"}, {"name": "amount"}, {"name": "region"},
        ])
        await _upsert_columns("src1", v1)

        v2 = _rows("sales", [
            {"name": "id"}, {"name": "amount_usd"},
        ])
        result = await _upsert_columns("src1", v2)
        assert result == {"tables": 1, "columns": 2}

        from sqlalchemy.ext.asyncio import AsyncSession

        from metadata_store.db import get_session_factory
        factory = get_session_factory()
        async with factory() as sess:
            cols = (await sess.execute(
                select(SchemaColumn).where(SchemaColumn.source_id == "src1"),
            )).scalars().all()
            assert len(cols) == 2
            names = {c.column_name for c in cols}
            assert names == {"id", "amount_usd"}
            assert "region" not in names

    async def test_reupload_isolated_by_source_id(self, engine):
        from shared.schema_indexer import _upsert_columns

        await _upsert_columns("src1", _rows("sales", [{"name": "id"}, {"name": "amount"}]))
        await _upsert_columns("src2", _rows("sales", [{"name": "id"}, {"name": "revenue"}]))
        # Re-upload src1; src2 should be untouched.
        await _upsert_columns("src1", _rows("sales", [{"name": "id"}]))

        from metadata_store.db import get_session_factory
        factory = get_session_factory()
        async with factory() as sess:
            src1 = (await sess.execute(
                select(SchemaColumn).where(SchemaColumn.source_id == "src1"),
            )).scalars().all()
            src2 = (await sess.execute(
                select(SchemaColumn).where(SchemaColumn.source_id == "src2"),
            )).scalars().all()
            assert len(src1) == 1
            assert len(src2) == 2


# ── MCP-style retrieval queries ───────────────────────────────────

@pytest.mark.asyncio
class TestMCPQueryPatterns:
    async def test_keyword_search_via_lower_pattern(self, engine):
        """Mirror metadata_search_columns SQL — case-insensitive
        LIKE on column_name_lower."""
        from shared.schema_indexer import _upsert_columns

        await _upsert_columns("src1", _rows("sales", [
            {"name": "TotalRevenue"},
            {"name": "Region"},
            {"name": "CustomerID"},
        ]))

        from metadata_store.db import get_session_factory
        factory = get_session_factory()
        async with factory() as sess:
            pattern = "%revenue%"
            res = await sess.execute(
                text(
                    "SELECT source_id, table_name, column_name, data_type "
                    "FROM schema_columns "
                    "WHERE column_name_lower LIKE :pat "
                    "ORDER BY source_id, table_name, ordinal_position"
                ),
                {"pat": pattern},
            )
            rows = [dict(r._mapping) for r in res]
            assert len(rows) == 1
            assert rows[0]["column_name"] == "TotalRevenue"

    async def test_describe_table_query(self, engine):
        """Mirror metadata_describe_table — fetch all columns for a
        (source, table) tuple in ordinal order."""
        from shared.schema_indexer import _upsert_columns

        await _upsert_columns("src1", _rows("orders", [
            {"name": "order_id"},
            {"name": "customer_id"},
            {"name": "total"},
        ]))

        from metadata_store.db import get_session_factory
        factory = get_session_factory()
        async with factory() as sess:
            res = await sess.execute(
                text(
                    "SELECT column_name, ordinal_position "
                    "FROM schema_columns "
                    "WHERE source_id=:sid AND table_name=:tn "
                    "ORDER BY ordinal_position"
                ),
                {"sid": "src1", "tn": "orders"},
            )
            cols = [dict(r._mapping) for r in res]
            assert [c["column_name"] for c in cols] == [
                "order_id", "customer_id", "total",
            ]
            assert cols[0]["ordinal_position"] == 1


# ── Multi-table source ────────────────────────────────────────────

@pytest.mark.asyncio
class TestMultiTable:
    async def test_multiple_tables_under_one_source(self, engine):
        from shared.schema_indexer import _upsert_columns

        rows = (
            _rows("sales", [{"name": "id"}, {"name": "amount"}])
            + _rows("orders", [{"name": "order_id"}, {"name": "ship_date"}])
        )
        result = await _upsert_columns("src1", rows)
        assert result == {"tables": 2, "columns": 4}

        from metadata_store.db import get_session_factory
        factory = get_session_factory()
        async with factory() as sess:
            all_cols = (await sess.execute(
                select(SchemaColumn).where(SchemaColumn.source_id == "src1"),
            )).scalars().all()
            tables = {c.table_name for c in all_cols}
            assert tables == {"sales", "orders"}
