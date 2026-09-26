"""BUG-196: connections that run user / stored / LLM SQL could read and write local files.

Every test plants a secret file on disk and shows the SQL path cannot read it, while
ordinary queries over the loaded tables keep working.
"""
from __future__ import annotations

import asyncio

import duckdb
import pytest

from shared.duckdb_factory import lock_down_connection

SECRET = "TOP-SECRET-CONTENT"


@pytest.fixture()
def secret_file(tmp_path):
    f = tmp_path / "secret.txt"
    f.write_text(SECRET)
    return str(f).replace("\\", "/")


def _blocked(con, sql):
    with pytest.raises(duckdb.Error):
        con.execute(sql).fetchall()


def test_lock_down_blocks_file_network_and_config_changes_but_keeps_loaded_tables(secret_file, tmp_path):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t AS SELECT 1 AS a UNION ALL SELECT 2")
    lock_down_connection(con)

    assert con.execute("SELECT SUM(a) FROM t").fetchone()[0] == 3
    _blocked(con, f"SELECT * FROM read_text('{secret_file}')")
    _blocked(con, f"SELECT * FROM read_csv('{secret_file}')")
    _blocked(con, f"SELECT * FROM '{secret_file}'")
    _blocked(con, "SELECT * FROM glob('*')")
    _blocked(con, f"COPY (SELECT 1) TO '{tmp_path.as_posix()}/out.csv'")
    _blocked(con, f"ATTACH '{tmp_path.as_posix()}/x.db' AS x")
    _blocked(con, "SET enable_external_access=true")
    assert not (tmp_path / "out.csv").exists()


@pytest.fixture()
def tenant_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path / "uploads"))
    from shared.data_utils import schema_cache  # noqa: F401  (import for side effects only)
    from shared.storage import get_storage_backend, reset_storage_backend

    reset_storage_backend()
    get_storage_backend().write("default", "orders.csv", b"id,amount\n1,10\n2,20\n")
    yield
    reset_storage_backend()


def test_saved_query_runner_cannot_read_local_files(tenant_upload, secret_file):
    from api_gateway.routers.queries import _execute_saved_query_sql

    ok = asyncio.run(_execute_saved_query_sql("SELECT SUM(amount) AS s FROM orders", None))
    assert ok["success"] and ok["row_count"] == 1

    with pytest.raises(duckdb.Error):
        asyncio.run(_execute_saved_query_sql(f"SELECT * FROM read_text('{secret_file}')", None))


def test_dashboard_tile_cannot_read_local_files(tenant_upload, secret_file):
    from starlette.requests import Request

    from api_gateway.routers.dashboards import _run_tile

    req = Request({"type": "http", "method": "GET", "headers": [], "query_string": b"", "path": "/x"})
    tile = {"id": "t1", "saved_query_id": "q1", "title": "x", "chart_type": "table"}

    good = asyncio.run(_run_tile(tile, [{"id": "q1", "name": "n", "sql": "SELECT COUNT(*) AS c FROM orders"}], req))
    assert good.get("status") != "error" and good["row_count"] == 1

    bad = asyncio.run(_run_tile(tile, [{"id": "q1", "name": "n", "sql": f"SELECT * FROM read_text('{secret_file}')"}], req))
    assert SECRET not in str(bad), "the tile returned the contents of a local file"
    assert bad.get("status") == "error"


def test_mcp_duckdb_handle_is_locked_down(tmp_path, secret_file, monkeypatch):
    pytest.importorskip("mcp")
    from mcp_servers import aura_mcp_server as srv

    db = tmp_path / "m.duckdb"
    seed = duckdb.connect(str(db))
    seed.execute("CREATE TABLE t AS SELECT 1 AS a")
    seed.close()
    monkeypatch.setattr(srv, "DUCKDB_PATH", str(db))
    monkeypatch.setattr(srv, "_duck_con", None)

    con = srv._get_duckdb()
    assert con.execute("SELECT a FROM t").fetchone()[0] == 1
    _blocked(con, f"SELECT * FROM read_text('{secret_file}')")
    _blocked(con, "SET enable_external_access=true")
    srv._duck_con = None
    con.close()
