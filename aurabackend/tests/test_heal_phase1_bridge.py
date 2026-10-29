"""Self-healing phase 1, connector ingest bridge: BUG-561, BUG-563.

The ingest route is called directly (no FastAPI lifespan) against a real file-backed
DuckDB table. Its UASR calls are routed in-process to the real UASR service: the
/uasr/baseline handler itself, and for /uasr/ingest the real DriftDetector the
handler runs (its recovery stage needs a database and an LLM and is not under test
here). The webhook side is the real WebhookDispatcher on the real event bus; only
the outbound HTTP to the subscriber's URL is captured by an httpx MockTransport.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import duckdb
import httpx
import pytest
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shared.webhook_dispatcher as wd  # noqa: E402
import uasr.service as service  # noqa: E402
from api_gateway.routers import connections  # noqa: E402
from connectors.base import ConnectorConfig, SourceType  # noqa: E402
from connectors.duckdb_connector import DuckDBConnector  # noqa: E402
from shared.streaming_manager import streaming_manager  # noqa: E402
from uasr.drift_detector import DriftDetector  # noqa: E402
from uasr.models import BatchPayload  # noqa: E402
from uasr.semantic_gateway import ReferenceContextMatrix, SemanticGateway  # noqa: E402
from uasr.tenancy import caller_tenant, scoped_source  # noqa: E402

_RealAsyncClient = httpx.AsyncClient
TENANT = "tenant-a"


def _request(tenant: str = TENANT) -> Request:
    req = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""})
    req.state.user = {"org_id": tenant, "sub": "user-1"}
    return req


class _Resp:
    def __init__(self, body):
        self.status_code = 200
        self._body = body

    def json(self):
        return self._body


@pytest.fixture()
def uasr(monkeypatch):
    """A fresh in-process UASR detector/gateway behind the bridge's httpx client."""
    monkeypatch.setattr(service, "_detector", DriftDetector())
    monkeypatch.setattr(service, "_gateway", SemanticGateway(matrix=ReferenceContextMatrix()))
    monkeypatch.setattr(service, "_mapek_worker", None)
    calls = []

    class _InProcessUASR:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **k):
            calls.append((url.rsplit("/uasr/", 1)[-1], json))
            if url.endswith("/uasr/baseline"):
                return _Resp(await service.register_baseline(service.BaselineRequest(**json), _request()))
            assert url.endswith("/uasr/ingest"), url
            batch = BatchPayload(
                source_id=scoped_source(caller_tenant(_request()), json["source_id"]),
                columns=json["columns"], rows=json["rows"], schema_snapshot=json["schema_snapshot"],
            )
            result = service._detector.detect(batch)
            return _Resp({
                "drift_detected": result.drift_detected,
                "drift_type": result.drift_type.value if result.drift_type else None,
                "severity": result.severity.value if result.severity else None,
            })

    monkeypatch.setattr(connections.httpx, "AsyncClient", _InProcessUASR)

    import metadata_store.repository as repo_mod

    async def _no_repo():
        if False:
            yield None

    monkeypatch.setattr(repo_mod, "get_repository", _no_repo)
    return calls


@pytest.fixture()
def orders_db(tmp_path, monkeypatch):
    db = tmp_path / "src.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE orders AS SELECT range AS id, range * 1.5 AS amount FROM range(10)")
    con.close()

    async def _make(conn_type, config, request):
        return DuckDBConnector(ConnectorConfig(
            source_type=SourceType.DUCKDB, name="t", connection_string=str(db),
        ))

    monkeypatch.setattr(connections, "_make_connector", _make)
    return db


def _rename_amount(db):
    con = duckdb.connect(str(db))
    con.execute("ALTER TABLE orders RENAME COLUMN amount TO total")
    con.close()


async def _ingest(**fields):
    req = connections.ConnectorIngestRequest(table_name="orders", **fields)
    return await connections.ingest_connector_data("duckdb", req, _request())


def _source_id():
    return f"{TENANT}::duckdb:orders"


# ── BUG-563 ──────────────────────────────────────────────────────────


def test_bug563_register_baseline_defaults_to_false():
    assert connections.ConnectorIngestRequest(table_name="orders").register_baseline is False


def test_bug563_first_run_still_seeds_a_baseline(uasr, orders_db):
    out = asyncio.run(_ingest())
    assert out["success"] is True and out["drift_events"] == 0
    assert service._detector.has_baseline(_source_id())


def test_bug563_rename_between_runs_is_reported_by_default(uasr, orders_db):
    """Monday registers the baseline; Tuesday's rename must be detected, not adopted."""
    monday = asyncio.run(_ingest())
    assert monday["drift_events"] == 0
    _rename_amount(orders_db)

    tuesday = asyncio.run(_ingest())

    # Old code: Tuesday's first chunk replaced the baseline, so this was 0.
    assert tuesday["drift_events"] == 1, tuesday
    assert tuesday["detail"][0]["result"]["drift_type"] == "schema"
    assert "amount" in service._detector.schema_baseline(_source_id())


def test_bug563_explicit_register_baseline_still_replaces_it(uasr, orders_db):
    asyncio.run(_ingest())
    _rename_amount(orders_db)

    out = asyncio.run(_ingest(register_baseline=True))

    assert out["drift_events"] == 0
    schema = service._detector.schema_baseline(_source_id())
    assert "total" in schema and "amount" not in schema


def test_bug563_only_if_absent_keeps_a_previously_adopted_schema(uasr):
    """A source whose first batch was ingested without a baseline has an adopted
    schema; seeding its distributions must not replace that schema."""
    sid = "src-adopted"
    service._detector.detect(BatchPayload(
        source_id=sid, columns=["id", "amount"],
        rows=[{"id": i, "amount": i * 1.5} for i in range(10)],
        schema_snapshot={"id": "BIGINT", "amount": "DOUBLE"},
    ))
    assert not service._detector.has_baseline(sid)

    renamed = {
        "source_id": sid, "columns": ["id", "total"],
        "rows": [{"id": i, "total": i * 1.5} for i in range(10)],
        "schema_snapshot": {"id": "BIGINT", "total": "DOUBLE"},
    }
    anon = Request({"type": "http", "headers": []})
    out = asyncio.run(service.register_baseline(service.BaselineRequest(**renamed, only_if_absent=True), anon))

    assert out["status"] == "registered"
    assert service._detector.has_baseline(sid)
    assert service._detector.schema_baseline(sid) == {"id": "BIGINT", "amount": "DOUBLE"}
    drift = service._detector.detect(BatchPayload(**renamed))
    assert drift.drift_detected and drift.drift_type.value == "schema"


def test_bug563_only_if_absent_leaves_an_existing_baseline_alone(uasr):
    sid = "src-existing"
    anon = Request({"type": "http", "headers": []})
    first = {"source_id": sid, "columns": ["v"], "rows": [{"v": float(i)} for i in range(30)]}
    asyncio.run(service.register_baseline(service.BaselineRequest(**first), anon))
    before = service._detector._store.peek(sid).baseline["v"].mean

    shifted = {"source_id": sid, "columns": ["v"], "rows": [{"v": float(i) + 1000} for i in range(30)]}
    out = asyncio.run(service.register_baseline(service.BaselineRequest(**shifted, only_if_absent=True), anon))

    assert out["status"] == "exists"
    assert service._detector._store.peek(sid).baseline["v"].mean == before


# ── BUG-561 ──────────────────────────────────────────────────────────


@pytest.fixture()
def dispatcher(tmp_path, monkeypatch):
    monkeypatch.setattr(wd, "_DATA_DIR", str(tmp_path / "webhooks"))
    monkeypatch.setattr(wd, "_STORE_PATH", str(tmp_path / "webhooks" / "subscriptions.json"))
    return wd.WebhookDispatcher()


async def _run_with_dispatcher(dispatcher, scenario):
    """Start the real dispatcher on the event bus, run ``scenario``, and return the
    webhook requests it sent (captured at the HTTP transport)."""
    sent = []

    def _capture(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    # start() builds its client from httpx.AsyncClient, which the uasr fixture routes
    # to UASR; give the dispatcher a real client whose transport captures deliveries.
    await dispatcher.start()
    dispatcher._client = _RealAsyncClient(transport=httpx.MockTransport(_capture))
    try:
        result = await scenario()
        for _ in range(200):
            if sent:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)  # let any further (wrong) deliveries land
    finally:
        await dispatcher.stop()
    return result, sent


def test_bug561_drift_fires_a_subscribed_uasr_drift_webhook(uasr, orders_db, dispatcher):
    mine = dispatcher.register(TENANT, "http://hooks.test/mine", ["uasr.drift"])
    dispatcher.register("tenant-b", "http://hooks.test/other", ["uasr.drift"])

    async def scenario():
        await _ingest(register_baseline=True)
        _rename_amount(orders_db)
        # register_baseline=False explicitly, so this test does not depend on BUG-563.
        return await _ingest(register_baseline=False)

    out, sent = asyncio.run(_run_with_dispatcher(dispatcher, scenario))

    assert out["drift_events"] == 1
    # Old code: nothing published a drift event, so no webhook was ever called.
    assert len(sent) == 1, [str(r.url) for r in sent]
    req = sent[0]
    assert str(req.url) == mine.url
    assert req.headers["X-AURA-Event"] == "uasr.drift"
    data = json.loads(req.content)["data"]
    assert data["drift_detected"] is True
    assert data["source_id"] == _source_id()
    assert data["drift_type"] == "schema"


def test_bug561_clean_ingest_publishes_no_drift_event(uasr, orders_db, dispatcher):
    dispatcher.register(TENANT, "http://hooks.test/mine", ["uasr.drift"])

    async def scenario():
        sub_id, queue = streaming_manager.subscribe("uasr:*")
        try:
            out = await _ingest()
            out2 = await _ingest()
        finally:
            streaming_manager.unsubscribe(sub_id)
        assert queue.empty()
        return out, out2

    (first, second), sent = asyncio.run(_run_with_dispatcher(dispatcher, scenario))
    assert first["drift_events"] == 0 and second["drift_events"] == 0
    assert sent == []
