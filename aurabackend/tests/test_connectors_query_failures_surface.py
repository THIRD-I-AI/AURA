"""BUG-439: a dead database or a failed statement must not come back as 200 with no rows."""
from __future__ import annotations

from fastapi.testclient import TestClient

import connectors.main as connectors_main


class _Connector:
    def __init__(self, connects: bool):
        self.connects = connects
        self.disconnected = False

    async def connect(self):
        return self.connects

    async def disconnect(self):
        self.disconnected = True

    async def execute_query(self, query, limit=1000, raise_errors=False):
        if raise_errors:
            raise RuntimeError('relation "nope" does not exist')
        return []


def _post(monkeypatch, connector):
    monkeypatch.setenv("DB_HOST", "db.example.com")
    monkeypatch.setattr(connectors_main, "_create_connector", lambda kind, cfg: connector)
    return TestClient(connectors_main.app).post(
        "/connections/default/query", json={"connection_id": "default", "query": "SELECT * FROM nope"})


def test_a_failed_connection_is_a_502(monkeypatch):
    r = _post(monkeypatch, _Connector(connects=False))
    assert r.status_code == 502, r.text


def test_a_failed_statement_is_an_error_and_the_connection_is_released(monkeypatch):
    connector = _Connector(connects=True)
    r = _post(monkeypatch, connector)
    assert r.status_code == 500, r.text
    assert connector.disconnected
