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
