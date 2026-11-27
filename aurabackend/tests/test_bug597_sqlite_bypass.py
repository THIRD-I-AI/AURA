"""BUG-597: the BUG-189 lockdown (enable_external_access=false) could be bypassed with a
SQLite database uploaded under a DuckDB-source or connection name. DuckDB sniffs the file,
loads sqlite_scanner (auto-installing it if needed) before any lockdown, and sqlite_scan()
then reads any SQLite file on the box -- users, chats, the ledger -- through SQLite's own I/O,
which the lockdown does not cover. Found by adversarial review of PR #814."""
from __future__ import annotations

import sqlite3

import duckdb
import pytest

from connectors.base import ConnectorConfig
from connectors.base import SourceType as CSourceType
from connectors.duckdb_connector import DuckDBConnector
from pipeline.engine import PipelineEngine
from pipeline.models import Pipeline, PipelineSink, PipelineSource, PipelineStatus, SinkType, SourceType
from shared.duckdb_factory import new_connection, require_duckdb_file

SECRET = "AURA_BUG597_SECRET_ROW"


def _isolate_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import reset_storage_backend
    reset_storage_backend()


def _sqlite_file(path, value=SECRET):
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE users (email TEXT)")
    c.execute("INSERT INTO users VALUES (?)", (value,))
    c.commit()
    c.close()
    return path


def _duckdb_file(path):
    c = duckdb.connect(str(path))
    c.execute("CREATE TABLE regions AS SELECT * FROM (VALUES (1, 'North')) t(id, name)")
    c.close()
    return path


def test_only_a_duckdb_file_passes_the_header_check(tmp_path):
    require_duckdb_file(str(_duckdb_file(tmp_path / "ok.duckdb")))
    require_duckdb_file(str(tmp_path / "missing.duckdb"))  # nothing to open yet
    for bad in (_sqlite_file(tmp_path / "evil.csv"), tmp_path / "plain.csv"):
        if not bad.exists():
            bad.write_text("a,b\n1,2\n", encoding="utf-8")
        with pytest.raises(ValueError):
            require_duckdb_file(str(bad))


def test_factory_connections_never_fetch_or_load_extensions_on_their_own():
    con = new_connection()
    try:
        assert con.execute("SELECT current_setting('autoinstall_known_extensions')").fetchone()[0] is False
        assert con.execute("SELECT current_setting('autoload_known_extensions')").fetchone()[0] is False
    finally:
        con.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("disguise", ["evil.csv", "evil.duckdb"])
async def test_a_sqlite_file_cannot_be_a_duckdb_pipeline_source(tmp_path, monkeypatch, disguise):
    _isolate_storage(tmp_path, monkeypatch)
    from shared.storage import get_storage_backend
    other = _sqlite_file(tmp_path / "other_tenant_metadata.db")
    evil = _sqlite_file(tmp_path / "evil_src.db", value="decoy")
    get_storage_backend().write("tenant_a", disguise, evil.read_bytes())
    for source in (
        PipelineSource(type=SourceType.DUCKDB, connection={"database": disguise},
                       query=f"SELECT * FROM \"sqlite_scan\"('{other.as_posix()}', 'users')"),
        PipelineSource(type=SourceType.DUCKDB, connection={"database": disguise}, table="users"),
    ):
        run = await PipelineEngine().execute(
            Pipeline(name="bug597", source=source, sink=PipelineSink(type=SinkType.PREVIEW)),
            preview_only=True, tenant="tenant_a")
        assert run.status == PipelineStatus.FAILED
        dumped = str(run.model_dump())
        assert SECRET not in dumped and "decoy" not in dumped


@pytest.mark.asyncio
async def test_a_sqlite_file_cannot_be_a_duckdb_connection(tmp_path):
    evil = _sqlite_file(tmp_path / "evil.duckdb")
    conn = DuckDBConnector(ConnectorConfig(source_type=CSourceType.DUCKDB, name="x", connection_string=str(evil)))
    assert await conn.connect() is False

    good = DuckDBConnector(ConnectorConfig(source_type=CSourceType.DUCKDB, name="y",
                                           connection_string=str(_duckdb_file(tmp_path / "good.duckdb"))))
    assert await good.connect() is True


@pytest.mark.asyncio
async def test_a_real_duckdb_table_source_still_works(tmp_path, monkeypatch):
    _isolate_storage(tmp_path, monkeypatch)
    from shared.storage import get_storage_backend
    get_storage_backend().write("tenant_a", "warehouse.duckdb", _duckdb_file(tmp_path / "w.duckdb").read_bytes())
    run = await PipelineEngine().execute(
        Pipeline(name="bug597-ok", source=PipelineSource(type=SourceType.DUCKDB, table="regions",
                                                       connection={"database": "warehouse.duckdb"}),
                 sink=PipelineSink(type=SinkType.PREVIEW)),
        preview_only=True, tenant="tenant_a")
    assert run.status == PipelineStatus.SUCCESS, run.error
    assert run.preview_data == [{"id": 1, "name": "North"}]
