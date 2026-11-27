"""BUG-189: a pipeline DuckDB source's ``query`` ran raw on the run's own connection,
before the engine locks that connection down (BUG-276). Any signed-up tenant could POST
/pipeline/execute with a query that read any file the gateway can read -- its
environment (JWT signing secret, credential key), the audit signing key, every tenant's
uploads and state databases -- listed directories, ATTACHed another tenant's database or
wrote files, and got the rows back in ``run.preview_data``.

The query now runs on a connection of its own that holds only the tenant's .duckdb file,
attached READ_ONLY, and is locked before the query runs; the rows move into the run as
Arrow. Each test below plants a secret file outside every tenant directory, and another
tenant's database, and shows a way of reaching them is refused -- also with the keyword
guard switched off, because the locked connection is the control that has to hold --
while a SELECT / JOIN / aggregate over the tenant's own file, and a constant SELECT with
no database, still return the right rows.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import duckdb
import pytest

from pipeline import engine as engine_module
from pipeline.engine import PipelineEngine
from pipeline.models import (
    JoinSource,
    Pipeline,
    PipelineSink,
    PipelineSource,
    PipelineStatus,
    ProcessingStep,
    SinkType,
    SourceType,
    StepType,
)

SECRET = "AURA_TEST_SECRET_DO_NOT_LEAK_189"
ATTACKER = "bug189-attacker"
VICTIM = "bug189-victim"


def _posix(path) -> str:
    return str(path).replace("\\", "/")


def _duckdb_file(path, statements) -> bytes:
    con = duckdb.connect(str(path))
    try:
        for sql in statements:
            con.execute(sql)
    finally:
        con.close()
    return path.read_bytes()


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """The attacker's own .duckdb upload, another tenant's database, and a secret file
    outside every tenant's directory."""
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import get_storage_backend, reset_storage_backend

    reset_storage_backend()
    backend = get_storage_backend()

    keys = tmp_path / "keys"
    keys.mkdir()
    secret = keys / "secrets.csv"
    secret.write_text(f"token\n{SECRET}\n", encoding="utf-8")

    backend.write(ATTACKER, "warehouse.duckdb", _duckdb_file(tmp_path / "w.duckdb", [
        "CREATE TABLE sales (id INTEGER, region VARCHAR, amount INTEGER)",
        "INSERT INTO sales VALUES (1, 'N', 10), (2, 'S', 20), (3, 'N', 5)",
        "CREATE TABLE regions (region VARCHAR, name VARCHAR)",
        "INSERT INTO regions VALUES ('N', 'North'), ('S', 'South')",
        # The tenant builds this file itself, so a view in it can name any server file.
        f"CREATE VIEW leak AS SELECT * FROM read_text('{_posix(secret)}')",
    ]))
    backend.write(VICTIM, "victim.duckdb", _duckdb_file(tmp_path / "v.duckdb", [
        "CREATE TABLE payroll (name VARCHAR, salary INTEGER)",
        f"INSERT INTO payroll VALUES ('{SECRET}', 999)",
    ]))
    return SimpleNamespace(
        secret=_posix(secret),
        keys=_posix(keys),
        out=_posix(tmp_path / "written_by_a_query.csv"),
        warehouse=backend.duckdb_uri(ATTACKER, "warehouse.duckdb"),
        victim_db=backend.duckdb_uri(VICTIM, "victim.duckdb"),
    )


async def _run(query=None, *, table=None, database="warehouse.duckdb", steps=()):
    pipeline = Pipeline(
        name="bug189",
        source=PipelineSource(
            type=SourceType.DUCKDB, query=query, table=table,
            connection={"database": database} if database else None,
        ),
        steps=list(steps),
        sink=PipelineSink(type=SinkType.PREVIEW),
    )
    return await PipelineEngine().execute(pipeline, preview_only=True, tenant=ATTACKER)


def _assert_refused(run, world):
    assert run.status == PipelineStatus.FAILED, run.preview_data
    shown = str(run.model_dump())
    assert SECRET not in shown
    assert "secrets.csv" not in shown  # what glob would list
    # The error names no server path, not even the one the query typed.
    assert world.keys not in shown and world.keys.replace("/", "\\") not in shown


def _without_guard(monkeypatch):
    """The keyword guard is defence in depth; the locked connection has to hold alone."""
    monkeypatch.setattr(engine_module, "_validate_expression", lambda expression: None)


FILE_READS = {
    "read_text": "SELECT * FROM read_text('{secret}')",
    "read_csv_auto": "SELECT * FROM read_csv_auto('{secret}')",
    "read_blob": "SELECT * FROM read_blob('{secret}')",
    "glob": "SELECT * FROM glob('{keys}/*')",
    "replacement_scan": "SELECT * FROM '{secret}'",
    "replacement_scan_double_quoted": 'SELECT * FROM "{secret}"',
    "view_in_the_tenants_own_file": "SELECT * FROM ext_source_data.leak",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", ["guarded", "lock_only"])
@pytest.mark.parametrize("name", list(FILE_READS))
async def test_a_query_source_cannot_read_server_files(world, monkeypatch, name, guard):
    if guard == "lock_only":
        _without_guard(monkeypatch)

    run = await _run(FILE_READS[name].format(secret=world.secret, keys=world.keys))

    _assert_refused(run, world)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [
    "SELECT * FROM read_text('{secret}')",
    "SELECT * FROM read_csv_auto('{secret}')",
    "SELECT * FROM '{secret}'",
])
async def test_with_no_database_a_file_read_fails_with_a_clear_error(world, query):
    run = await _run(query.format(secret=world.secret), database=None)

    _assert_refused(run, world)
    assert "DuckDB source query refused" in run.error


@pytest.mark.asyncio
async def test_a_query_source_cannot_attach_another_tenants_database(world):
    run = await _run(
        f"SELECT 1 AS x; ATTACH '{world.victim_db}' AS v (READ_ONLY); "
        "CREATE OR REPLACE TABLE source_data AS SELECT * FROM v.payroll"
    )

    _assert_refused(run, world)


@pytest.mark.asyncio
async def test_a_query_source_cannot_write_a_file(world):
    run = await _run(f"SELECT 1 AS x; COPY (SELECT '{SECRET}' AS s) TO '{world.out}'")

    _assert_refused(run, world)
    assert not os.path.exists(world.out)


@pytest.mark.asyncio
@pytest.mark.parametrize("setting", ["SET lock_configuration=false", "RESET lock_configuration"])
async def test_a_setting_inside_the_query_cannot_switch_file_access_back_on(world, setting):
    """DuckDB refuses ``SET enable_external_access=true`` on any running database (it
    failed on the old connection too), so the way back in is to unlock the configuration
    first. Both statements ran on the old connection; the connection test below shows
    the source's own connection refuses them and the plain SET."""
    run = await _run(
        f"SELECT 1 AS x; {setting}; "
        f"CREATE OR REPLACE TABLE source_data AS SELECT * FROM read_text('{world.secret}')"
    )

    _assert_refused(run, world)


@pytest.mark.asyncio
async def test_a_table_source_cannot_read_server_files_through_a_view_in_the_upload(world):
    run = await _run(table="leak")

    _assert_refused(run, world)


@pytest.mark.asyncio
async def test_a_join_query_source_cannot_read_server_files(world):
    """A JOIN step's second source loads through the same loader as the first."""
    run = await _run("SELECT 1 AS k", database=None, steps=[ProcessingStep(
        id="j", type=StepType.JOIN, config={"left_key": "k", "right_key": "k"},
        join_source=JoinSource(type=SourceType.DUCKDB,
                               query=f"SELECT 1 AS k, * FROM read_text('{world.secret}')"),
    )])

    _assert_refused(run, world)


def test_the_source_connection_refuses_files_attach_copy_and_settings(world):
    con = engine_module._locked_source_connection(world.warehouse, "ext_source_data")
    try:
        assert con.execute("SELECT SUM(amount) FROM ext_source_data.sales").fetchone()[0] == 35
        for sql in [
            f"SELECT * FROM read_text('{world.secret}')",
            f"SELECT * FROM read_csv_auto('{world.secret}')",
            f"SELECT * FROM read_blob('{world.secret}')",
            f"SELECT * FROM glob('{world.keys}/*')",
            f"SELECT * FROM '{world.secret}'",
            f'SELECT * FROM "{world.secret}"',
            "SELECT * FROM ext_source_data.leak",
            f"ATTACH '{world.victim_db}' AS v (READ_ONLY)",
            f"COPY (SELECT 1) TO '{world.out}'",
            "SET enable_external_access=true",
            "RESET enable_external_access",
            "SET lock_configuration=false",
            "RESET lock_configuration",
            "LOAD httpfs",
            "DELETE FROM ext_source_data.sales",
        ]:
            with pytest.raises(duckdb.Error):
                con.execute(sql).fetchall()
        assert not os.path.exists(world.out)
    finally:
        con.close()


# ── The feature stays: SQL over the tenant's own file, or over no file at all ──

@pytest.mark.asyncio
@pytest.mark.parametrize("terminator", ["", ";"])
async def test_a_select_join_aggregate_over_the_tenants_own_file_still_works(world, terminator):
    run = await _run(
        "SELECT s.region, r.name, SUM(s.amount) AS total, COUNT(*) AS n "
        "FROM ext_source_data.sales s JOIN ext_source_data.regions r USING (region) "
        f"GROUP BY s.region, r.name ORDER BY s.region{terminator}"
    )

    assert run.status == PipelineStatus.SUCCESS, run.error
    assert run.rows_read == 2
    assert run.columns_in == ["region", "name", "total", "n"]
    assert run.preview_data == [
        {"region": "N", "name": "North", "total": 15, "n": 2},
        {"region": "S", "name": "South", "total": 20, "n": 1},
    ]


@pytest.mark.asyncio
async def test_a_constant_select_with_no_database_still_works(world):
    run = await _run("SELECT 1 AS a, 'x' AS b UNION ALL SELECT 2, 'y' ORDER BY a", database=None)

    assert run.status == PipelineStatus.SUCCESS, run.error
    assert run.preview_data == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]


@pytest.mark.asyncio
async def test_a_table_source_on_the_tenants_own_file_still_works(world):
    run = await _run(table="sales")

    assert run.status == PipelineStatus.SUCCESS, run.error
    assert sorted(r["amount"] for r in run.preview_data) == [5, 10, 20]


@pytest.mark.asyncio
async def test_a_join_query_source_over_its_own_file_still_works(world):
    run = await _run(table="sales", steps=[ProcessingStep(
        id="j", type=StepType.JOIN, config={"left_key": "region", "right_key": "region"},
        join_source=JoinSource(type=SourceType.DUCKDB, connection={"database": "warehouse.duckdb"},
                               query="SELECT region, name FROM ext_join_src_j.regions"),
    )])

    assert run.status == PipelineStatus.SUCCESS, run.error
    assert sorted((r["id"], r["name"]) for r in run.preview_data) == [(1, "North"), (2, "South"), (3, "North")]


# ── Through the router, with a tenant's token ──────────────────────────────────

def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api_gateway.routers.pipelines import router
    from shared.middleware import JWTAuthMiddleware

    app = FastAPI()
    app.add_middleware(JWTAuthMiddleware)
    app.include_router(router, prefix="/api/v1")
    # No `with`: the ASGI lifespan is never driven (.claude/rules/backend.md).
    return TestClient(app)


def _execute(client, query, headers):
    return client.post("/api/v1/pipeline/execute", headers=headers, json={
        "pipeline": {
            "name": "bug189",
            "source": {"type": "duckdb", "connection": {"database": "warehouse.duckdb"}, "query": query},
            "sink": {"type": "preview"},
        },
        "preview_only": True,
    })


def test_the_router_refuses_a_file_read_and_still_returns_the_tenants_own_rows(world):
    from shared.auth import create_access_token

    client = _client()
    token = {"Authorization": f"Bearer {create_access_token({'sub': 'u-189', 'org_id': ATTACKER})}"}

    assert _execute(client, "SELECT 1 AS a", {}).status_code == 401

    bad = _execute(client, f"SELECT * FROM read_text('{world.secret}')", token)
    assert bad.status_code == 200, bad.text
    assert bad.json()["run"]["status"] == "failed", bad.text
    assert SECRET not in bad.text and world.keys not in bad.text

    good = _execute(client, "SELECT region, SUM(amount) AS total FROM ext_source_data.sales "
                            "GROUP BY region ORDER BY region", token)
    assert good.status_code == 200, good.text
    run = good.json()["run"]
    assert run["status"] == "success", run
    assert run["preview_data"] == [{"region": "N", "total": 15}, {"region": "S", "total": 20}]
