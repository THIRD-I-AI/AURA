"""BUG-225: /execute/query must refuse "embedded" connectors (DuckDB, DuckDB-spatial, FAISS),
which run on server-local files at a caller-chosen path with no tenant check."""
import pytest
from fastapi.testclient import TestClient

URL = "/api/v1/execute/query"


@pytest.fixture()
def client():
    from api_gateway.main import app

    return TestClient(app)


@pytest.mark.parametrize("ctype", ["duckdb", "duckdb_spatial", "faiss"])
def test_embedded_connector_types_are_refused(client, ctype):
    resp = client.post(URL, json={"query": "SELECT 1", "connector_type": ctype,
                                  "connector_config": {"database": ":memory:"}})
    assert resp.status_code == 400, resp.text
    assert "server-local" in resp.json()["detail"]


def test_embedded_refusal_happens_before_any_connection_is_opened(client, monkeypatch):
    from connectors import duckdb_connector

    def boom(self):
        raise AssertionError("DuckDBConnector.connect must not be reached")

    monkeypatch.setattr(duckdb_connector.DuckDBConnector, "connect", boom)
    resp = client.post(URL, json={"query": "SELECT 1", "connector_type": "duckdb",
                                  "connector_config": {"database": ":memory:"}})
    assert resp.status_code == 400


def test_query_validation_failure_is_now_a_real_400_not_a_200(client):
    resp = client.post(URL, json={"query": "DROP TABLE t", "connector_type": "postgresql",
                                  "connector_config": {"host": "localhost"}})
    assert resp.status_code == 400, resp.text


def test_a_successful_query_is_reported_as_successful(client, monkeypatch):
    """The success-path response left out `error`, which the model required, so the
    handler's except turned every successful query into success=False."""
    monkeypatch.setenv("AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS", "true")  # local test host (BUG-377)
    from agents.base import AgentResult, AgentStatus
    from agents.specialists import analysis_agent
    from api_gateway.routers import queries

    class FakeConnector:  # an external database; none is available to the test lane
        async def connect(self):
            return True

        async def disconnect(self):
            return True

        async def execute_query(self, sql):
            return [{"region": "EU", "total": 10}, {"region": "US", "total": 7}]

    async def _no_llm(self, ctx):
        return AgentResult(status=AgentStatus.FAILED, error="no provider in tests")

    monkeypatch.setattr(queries, "build_connector", lambda ctype, cfg: FakeConnector())
    monkeypatch.setattr(analysis_agent.AnalysisAgent, "execute", _no_llm)

    resp = client.post(URL, json={"query": "SELECT region, total FROM sales", "connector_type": "mysql",
                                  "connector_config": {"host": "db.example", "database": "shop"}})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["rows"] == 2 and body["columns"] == ["region", "total"]
    assert body["error"] is None
