"""
Test: /connections/{connection_id}/query rejects an unregistered
connection_id instead of silently misrouting to the global env DB
(BUG-029 item 1).

_connection_store in aurabackend/connectors/main.py is never written
anywhere in this codebase (no registration endpoint exists yet), so
before this fix, ANY connection_id fell through to the single
env-configured DB with no error -- a caller asking for a specific
connection got a different one silently. Only the documented "default"
sentinel is meant to use the env fallback.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _client():
    from fastapi.testclient import TestClient

    from connectors.main import app
    return TestClient(app)


def test_unregistered_connection_id_is_rejected(monkeypatch):
    """A connection_id other than 'default' with nothing registered for it
    must 404, not silently run against the global env-configured DB."""
    monkeypatch.delenv("DB_HOST", raising=False)
    client = _client()
    r = client.post(
        "/connections/some-other-tenants-connection/query",
        json={"connection_id": "some-other-tenants-connection", "query": "SELECT 1"},
    )
    assert r.status_code == 404, r.text
    assert "not registered" in r.json()["detail"]


def test_default_connection_id_still_uses_env_fallback(monkeypatch):
    """The documented 'default' sentinel keeps its existing behavior: no
    DB_HOST configured -> 400 (not 404), same as before this fix."""
    monkeypatch.delenv("DB_HOST", raising=False)
    client = _client()
    r = client.post(
        "/connections/default/query",
        json={"connection_id": "default", "query": "SELECT 1"},
    )
    assert r.status_code == 400, r.text
    assert "no default DB configured" in r.json()["detail"]
