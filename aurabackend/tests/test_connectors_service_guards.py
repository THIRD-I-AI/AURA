"""BUG-442: the connectors service applies the host rule and file confinement itself."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import connectors.main as connectors_main


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS", raising=False)
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path / "uploads"))
    (tmp_path / "uploads").mkdir()

    def _never(kind, cfg):
        raise AssertionError(f"a {kind} connector was built for a refused config")

    monkeypatch.setattr(connectors_main, "_create_connector", _never)
    return TestClient(connectors_main.app)


@pytest.mark.parametrize("route", ["/test", "/tables", "/introspect"])
@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "localhost", ""])
def test_an_internal_database_host_is_refused(client, route, host):
    r = client.post(route, json={"connector_type": "postgresql", "config": {"host": host, "username": "u"}})
    assert r.status_code == 400, r.text
    assert "public address" in r.json()["detail"]


@pytest.mark.parametrize("route", ["/test", "/tables", "/introspect"])
def test_a_duckdb_file_outside_the_uploads_is_refused(client, route, tmp_path):
    other = tmp_path / "elsewhere" / "warehouse.duckdb"
    r = client.post(route, json={"connector_type": "duckdb", "config": {"database": str(other)}})
    assert r.status_code == 400, r.text
    assert "uploads" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_public_host_and_an_upload_path_pass_through(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.setenv("AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS", "true")
    from connectors import ConnectorConfig, SourceType

    monkeypatch.setattr(connectors_main, "_create_connector", lambda kind, cfg: cfg)

    async def opened(kind, **fields):
        return await connectors_main._open_connector(
            kind, ConnectorConfig(source_type=SourceType(kind), name="t", **fields))

    assert (await opened("postgresql", host="db.internal")).host == "db.internal"
    assert (await opened("duckdb", database="mine.duckdb")).database == str((tmp_path / "mine.duckdb").resolve())
    assert (await opened("duckdb", database=":memory:")).database == ":memory:"
