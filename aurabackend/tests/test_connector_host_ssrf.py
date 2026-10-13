"""BUG-367: Postgres/MySQL connector hosts were not SSRF-filtered, and an empty host became
the gateway's own localhost, so the connector routes reached the gateway's internal network
(and worked as a port scanner)."""
import pytest

from tests.test_connections_row_ceiling import _isolated_uploads, client  # noqa: F401  (fixtures)

V1 = "/api/v1"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "10.0.0.12", "169.254.169.254", "100.64.0.1", "::1", ""])
@pytest.mark.parametrize("db", ["postgresql", "mysql"])
def test_ad_hoc_connector_routes_refuse_internal_hosts(client, host, db):  # noqa: F811
    r = client.post(f"{V1}/connectors/{db}/test", json={"host": host, "port": 5432, "database": "x"})
    assert r.status_code == 400, r.text
    assert "public address" in r.text


def test_saving_a_connection_to_an_internal_host_is_refused(client):  # noqa: F811
    r = client.post(f"{V1}/connections", json={"name": "x", "type": "postgresql", "host": "10.0.0.12", "port": 5432})
    assert r.status_code == 400, r.text


def test_private_hosts_can_be_allowed_by_the_operator(client, monkeypatch):  # noqa: F811
    monkeypatch.setenv("AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS", "true")
    r = client.post(f"{V1}/connectors/postgresql/test", json={"host": "127.0.0.1", "port": 1, "database": "x"})
    assert r.status_code != 400 or "public address" not in r.text


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.12", ""])
def test_execute_query_refuses_internal_database_hosts(client, host):  # noqa: F811
    # BUG-377: POST /execute/query built its own connector and skipped the BUG-367 check.
    r = client.post(f"{V1}/execute/query", json={
        "query": "SELECT 1", "connector_type": "postgresql",
        "connector_config": {"host": host, "port": 5432, "database": "x", "username": "u", "password": "p"},
    })
    assert r.status_code == 400, r.text
    assert "public address" in r.text


# ── BUG-404: pipeline engine and streaming database sink ────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.12", None])
async def test_pipeline_database_source_and_sink_refuse_internal_hosts(host):
    # The engine opens both through shared.connector_policy.open_connector; a refused
    # host must stop it before any connector is built.
    from connectors import ConnectorConfig, SourceType
    from shared.connector_policy import open_connector

    def _never(cfg):
        raise AssertionError("a connector was built for a refused host")

    config = ConnectorConfig(source_type=SourceType.POSTGRESQL, name="pipeline_source", host=host)
    with pytest.raises(ValueError, match="public address"):
        await open_connector("postgresql", config, files_root=None, build=_never)


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [
    {"connector": "postgresql", "host": "10.0.0.12"},
    {"connector": "postgresql"},
    {"connector": "postgresql", "dsn": "postgresql://u:p@127.0.0.1:5432/db"},
])
async def test_streaming_database_sink_refuses_internal_hosts(config, monkeypatch):
    import sys
    import types

    from pipeline.streaming.sinks.database_sink import DatabaseSink

    connected = []

    async def _connect(*a, **k):
        connected.append((a, k))
        raise AssertionError("connected to an internal host")

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=_connect))
    sink = DatabaseSink({**config, "table": "t"})
    with pytest.raises(ValueError, match="public address"):
        await sink.start()
    assert connected == []


@pytest.mark.asyncio
async def test_private_database_hosts_can_be_allowed_by_the_operator(monkeypatch):
    from shared.ssrf import is_allowed_db_host

    assert await is_allowed_db_host("127.0.0.1") is False
    monkeypatch.setenv("AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS", "true")
    assert await is_allowed_db_host("127.0.0.1") is True
