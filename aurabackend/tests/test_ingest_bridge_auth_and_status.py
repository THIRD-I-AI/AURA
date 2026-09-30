"""BUG-229: the connector -> UASR ingest bridge must forward the caller's Authorization
and must not count a batch as ingested when UASR answers with an HTTP error."""
import httpx
import pytest

from tests.test_connections_row_ceiling import (  # noqa: F401  (fixtures; _isolated_uploads is autouse)
    _isolated_uploads,
    client,
    duckdb_source,
)

V1 = "/api/v1"


def _fake_uasr(monkeypatch, ingest_status=200):
    seen = {"client_headers": None, "calls": []}

    class _Resp:
        def __init__(self, code):
            self.status_code = code

        def json(self):
            return {"drift_detected": False} if self.status_code < 400 else {"detail": "Unauthorized"}

    class _FakeClient:
        def __init__(self, *a, headers=None, **k):
            seen["client_headers"] = dict(headers or {})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **k):
            seen["calls"].append(url)
            return _Resp(ingest_status if url.endswith("/uasr/ingest") else 200)

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    return seen


def _ingest(client, source, auth=None):  # noqa: F811
    headers = {"Authorization": auth} if auth else {}
    return client.post(f"{V1}/connectors/duckdb/ingest", headers=headers, json={
        "connector_config": {"database": source}, "table_name": "customers"})


def test_caller_authorization_is_forwarded_to_uasr(client, duckdb_source, monkeypatch):  # noqa: F811
    seen = _fake_uasr(monkeypatch)
    r = _ingest(client, duckdb_source, auth="Bearer caller-token")
    assert r.status_code == 200, r.text
    assert seen["client_headers"].get("Authorization") == "Bearer caller-token"


def test_uasr_http_error_is_a_502_not_a_successful_ingest(client, duckdb_source, monkeypatch):  # noqa: F811
    _fake_uasr(monkeypatch, ingest_status=401)
    r = _ingest(client, duckdb_source, auth="Bearer caller-token")
    assert r.status_code == 502, r.text
    assert "HTTP 401" in r.json()["detail"]


def test_healthy_uasr_still_ingests(client, duckdb_source, monkeypatch):  # noqa: F811
    _fake_uasr(monkeypatch)
    r = _ingest(client, duckdb_source)
    assert r.status_code == 200 and r.json()["total_rows"] == 3, r.text
