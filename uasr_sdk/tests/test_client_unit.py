"""
Unit tests for the UASR client against a small mock ASGI app.

These are fast and dependency-free (no backend import): they verify the
client marshals requests correctly, parses responses into typed models,
and raises the right exceptions on error responses.
"""
from __future__ import annotations

import json

import httpx
import pytest

from uasr_client import (
    AsyncUASRClient,
    UASRAPIError,
    UASRClient,
)


# ─────────────────────────────────────────────────────────────────────
# A minimal fake UASR server as a raw ASGI/httpx handler
# ─────────────────────────────────────────────────────────────────────
def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    body = json.loads(request.content) if request.content else {}

    if path == "/uasr/deployment":
        return httpx.Response(200, json={
            "state_backend": "memory", "repair_backend": "none",
            "mapek_enabled": False, "recovery_mode": "auto", "node_id": "test",
            "state_store_class": "InMemoryStateStore", "repair_backend_class": None,
        })
    if path == "/uasr/baseline":
        return httpx.Response(200, json={
            "status": "registered", "source_id": body["source_id"],
            "reference_version": "abc123", "row_count": len(body["rows"]),
            "columns": body["columns"],
        })
    if path == "/uasr/ingest":
        # simulate drift when any row has amount > 1000
        drifted = any(r.get("amount", 0) > 1000 for r in body.get("rows", []))
        if drifted:
            return httpx.Response(200, json={
                "status": "drift", "drift_detected": True, "batch_id": body["batch_id"],
                "drift_type": "statistical", "severity": "critical",
                "drift_event_id": "evt1", "recovery_id": "rec1",
                "shim_deployed": True, "post_kl": 0.01, "latency_seconds": 0.03,
                "gate": {"allowed": False, "similarity": -0.1, "threshold": 0.75,
                         "source_id": body["source_id"], "batch_id": body["batch_id"], "reason": "rejected"},
            })
        return httpx.Response(200, json={
            "status": "clean", "drift_detected": False, "batch_id": body["batch_id"],
            "gate": {"allowed": True, "similarity": 1.0, "threshold": 0.75,
                     "source_id": body["source_id"], "batch_id": body["batch_id"], "reason": ""},
        })
    if path == "/uasr/sources":
        return httpx.Response(200, json={"sources": [
            {"source_id": "orders", "has_active_baseline": True, "deployed_shims": 1},
        ], "count": 1})
    if path == "/uasr/metrics":
        return httpx.Response(200, json={"hu": 0.42, "resolution": 0.9, "events": 3})
    if path.endswith("/approve"):
        return httpx.Response(200, json={"status": "approved", "recovery_id": path.split("/")[3]})
    if path == "/uasr/nonexistent":
        return httpx.Response(404, json={"detail": "not found"})
    return httpx.Response(404, json={"detail": f"no route {path}"})


@pytest.fixture
def client() -> UASRClient:
    transport = httpx.MockTransport(_handler)
    c = UASRClient("http://test", transport=transport)
    yield c
    c.close()


# ─────────────────────────────────────────────────────────────────────
# Sync tests
# ─────────────────────────────────────────────────────────────────────
def test_deployment(client: UASRClient) -> None:
    info = client.deployment()
    assert info.state_backend == "memory"
    assert info.state_store_class == "InMemoryStateStore"
    assert info.mapek_enabled is False


def test_register_baseline_infers_columns(client: UASRClient) -> None:
    rows = [{"amount": 1.0}, {"amount": 2.0}]
    res = client.register_baseline("orders", rows)
    assert res.status == "registered"
    assert res.row_count == 2
    assert res.columns == ["amount"]
    assert res.reference_version == "abc123"


def test_ingest_clean(client: UASRClient) -> None:
    res = client.ingest("orders", [{"amount": 5.0}], batch_id="b1")
    assert res.drift_detected is False
    assert res.status == "clean"
    assert res.gate.allowed is True


def test_ingest_drift(client: UASRClient) -> None:
    res = client.ingest("orders", [{"amount": 9999.0}], batch_id="b2")
    assert res.drift_detected is True
    assert res.severity == "critical"
    assert res.shim_deployed is True
    assert res.gate.allowed is False
    assert res.post_kl == pytest.approx(0.01)


def test_sources(client: UASRClient) -> None:
    srcs = client.sources()
    assert len(srcs) == 1
    assert srcs[0].source_id == "orders"
    assert srcs[0].deployed_shims == 1


def test_metrics_permissive_extra_fields(client: UASRClient) -> None:
    m = client.metrics()
    d = m.model_dump()
    assert d["hu"] == 0.42
    assert d["events"] == 3


def test_approve(client: UASRClient) -> None:
    out = client.approve("rec1", approver="alice", note="ok")
    assert out["status"] == "approved"


def test_api_error_raised(client: UASRClient) -> None:
    with pytest.raises(UASRAPIError) as ei:
        client._request("GET", "/uasr/nonexistent")
    assert ei.value.status_code == 404
    assert "not found" in str(ei.value.detail)


# ─────────────────────────────────────────────────────────────────────
# Async parity
# ─────────────────────────────────────────────────────────────────────
async def test_async_ingest_drift() -> None:
    transport = httpx.MockTransport(_handler)
    async with AsyncUASRClient("http://test", transport=transport) as c:
        res = await c.ingest("orders", [{"amount": 9999.0}], batch_id="b2")
        assert res.drift_detected is True
        assert res.severity == "critical"


async def test_async_baseline_and_sources() -> None:
    transport = httpx.MockTransport(_handler)
    async with AsyncUASRClient("http://test", transport=transport) as c:
        res = await c.register_baseline("orders", [{"amount": 1.0}])
        assert res.row_count == 1
        srcs = await c.sources()
        assert srcs[0].source_id == "orders"
