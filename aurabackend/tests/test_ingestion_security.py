"""Sec-7 — ingestion auth (fail-closed) + perimeter PII tokenization."""
import os
import re
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.auth import create_access_token

TOKEN_RE = re.compile(r"^PII-[0-9a-f]{12}$")

BATCH = {
    "batch_id": "b1",
    "tenant_id": "t1",
    "entries": [{"internalId": "X1", "debit": 100.0, "tranDate": "2026-06-01",
                 "employee_name": "Ada Lovelace"}],
}


def _client(monkeypatch):
    import ingestion_service.main as im
    monkeypatch.setattr(im.kafka_producer, "producer", None)
    monkeypatch.setattr(im.kafka_producer, "start", _noop_start)
    return TestClient(im.app), im


async def _noop_start():
    return None


# ── Ingestion endpoints are fail-closed ──────────────────────────────


def test_ingest_without_token_is_401(monkeypatch):
    client, _ = _client(monkeypatch)
    with client:
        r = client.post("/api/v1/ingest/netsuite", json=BATCH)
    assert r.status_code == 401


def test_ingest_with_token_is_202(monkeypatch):
    client, _ = _client(monkeypatch)
    tok = create_access_token({"sub": "erp-connector", "role": "service"})
    with client:
        r = client.post("/api/v1/ingest/netsuite", json=BATCH,
                        headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 202


def test_erc_map_requires_token(monkeypatch):
    client, _ = _client(monkeypatch)
    with client:
        assert client.get("/api/v1/ingest/erc-map/E1?system=NetSuite").status_code == 401


# ── Perimeter masking tokenizes (keyed) instead of destroying evidence ─


def test_perimeter_tokenizes_with_key(monkeypatch):
    monkeypatch.setenv("AURA_PII_TOKEN_KEY", "perimeter-secret")
    client, _ = _client(monkeypatch)
    captured = []
    import ingestion_service.main as im
    orig = im.process_raw_batch_async

    async def spy(payload, system_origin):
        captured.append(payload)
    monkeypatch.setattr(im, "process_raw_batch_async", spy)

    tok = create_access_token({"sub": "erp-connector", "role": "service"})
    with client:
        r1 = client.post("/api/v1/ingest/netsuite", json=BATCH,
                         headers={"Authorization": f"Bearer {tok}"})
        r2 = client.post("/api/v1/ingest/netsuite", json=BATCH,
                         headers={"Authorization": f"Bearer {tok}"})
    assert r1.status_code == r2.status_code == 202
    assert orig is not None and len(captured) == 2
    n1 = captured[0].entries[0]["employee_name"]
    n2 = captured[1].entries[0]["employee_name"]
    assert TOKEN_RE.match(n1), f"expected PII token, got {n1!r}"
    assert n1 == n2          # deterministic — AS-2401 correlation survives


def test_perimeter_redacts_without_key(monkeypatch):
    monkeypatch.delenv("AURA_PII_TOKEN_KEY", raising=False)
    client, _ = _client(monkeypatch)
    captured = []
    import ingestion_service.main as im

    async def spy(payload, system_origin):
        captured.append(payload)
    monkeypatch.setattr(im, "process_raw_batch_async", spy)

    tok = create_access_token({"sub": "erp-connector", "role": "service"})
    with client:
        client.post("/api/v1/ingest/netsuite", json=BATCH,
                    headers={"Authorization": f"Bearer {tok}"})
    assert captured[0].entries[0]["employee_name"] == "[REDACTED]"
