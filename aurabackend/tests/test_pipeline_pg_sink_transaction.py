"""BUG-191: the Postgres pipeline sink dropped the destination table, then re-created and filled it
with no transaction, so a failure part-way left the table missing or half-loaded.

Tier B: needs a real Postgres (AURA_PG_TEST_DSN), which the CI 'Scheduler (Postgres)' lane provides.
"""
from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse

import duckdb
import pytest

from pipeline.engine import PipelineEngine
from pipeline.models import PipelineSink, SinkType

DSN = os.getenv("AURA_PG_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="needs Postgres (set AURA_PG_TEST_DSN=postgresql+asyncpg://...)")


def _sink_connection() -> dict:
    u = urlparse(DSN.replace("postgresql+asyncpg", "postgresql"))
    return {"host": u.hostname, "port": u.port or 5432, "username": u.username,
            "password": u.password or "", "database": u.path.lstrip("/")}


async def _pg_rows(table: str):
    import asyncpg

    c = _sink_connection()
    conn = await asyncpg.connect(host=c["host"], port=c["port"], user=c["username"],
                                 password=c["password"], database=c["database"])
    try:
        return await conn.fetch(f'SELECT * FROM "{table}" ORDER BY 1')
    finally:
        await conn.close()


async def _drop(table: str):
    import asyncpg

    c = _sink_connection()
    conn = await asyncpg.connect(host=c["host"], port=c["port"], user=c["username"],
                                 password=c["password"], database=c["database"])
    try:
        await conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    finally:
        await conn.close()


def _run(final_sql: str, table: str):
    conn = duckdb.connect(":memory:")
    conn.execute(f"CREATE TABLE final_t AS {final_sql}")
    sink = PipelineSink(type=SinkType.POSTGRESQL, table=table, if_exists="replace", connection=_sink_connection())
    run = type("R", (), {"run_id": "r", "rows_written": 0, "output_table": None, "output_file": None})()
    return PipelineEngine()._write_pg_sink(conn, "final_t", sink, run)


@pytest.mark.asyncio
async def test_a_failed_replace_leaves_the_previous_table_intact():
    table = f"aura_bug191_{uuid.uuid4().hex[:8]}"
    try:
        await _run("SELECT 1 AS id, 'kept' AS note", table)
        assert [tuple(r) for r in await _pg_rows(table)] == [(1, "kept")]

        # BLOB maps to TEXT in the sink's type map, and asyncpg refuses bytes for a TEXT parameter,
        # so this replace fails AFTER the DROP and CREATE have run.
        with pytest.raises(Exception):
            await _run("SELECT 2 AS id, '\xAA'::BLOB AS note", table)

        assert [tuple(r) for r in await _pg_rows(table)] == [(1, "kept")], "the original table must survive"
    finally:
        await _drop(table)


@pytest.mark.asyncio
async def test_a_successful_replace_still_replaces():
    table = f"aura_bug191_{uuid.uuid4().hex[:8]}"
    try:
        await _run("SELECT 1 AS id", table)
        await _run("SELECT 5 AS id UNION ALL SELECT 6", table)
        assert [tuple(r) for r in await _pg_rows(table)] == [(5,), (6,)]
    finally:
        await _drop(table)
