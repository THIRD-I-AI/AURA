"""Audit round 6, UASR area: BUG-463..467.

463: UASR timestamp columns took aware datetimes into naive columns (asyncpg rejects them).
464: approval-chain votes were serialised only by a per-process lock.
465: the gateway's uasr:metrics poller sent no credentials and broadcast per-source stats.
466: the semantic gateway's per-source history, versions and source map were unbounded.
467: each UASR replica kept its own shim registry, so approve/rollback reached one replica.

Uses an isolated metadata DB like tests/test_uasr_approval_identity_and_race.py."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql.asyncpg import dialect as asyncpg_dialect
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
from uasr import models as uasr_models  # noqa: E402
from uasr.db import get_session_factory, init_uasr_db  # noqa: E402
from uasr.models import BatchPayload, DriftEvent, RecoveryRecord, RecoveryStatus  # noqa: E402

SHIM = "def transform(rows):\n    return rows  # deployed on a peer replica\n"


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_uasr_r6_{uuid.uuid4().hex[:8]}.db")
    _metadata_db.DATABASE_URL = f"sqlite+aiosqlite:///{tmp_path}"
    _metadata_db._engine = None
    _metadata_db._session_factory = None
    yield
    leaked = _metadata_db._engine  # BUG-008: dispose before restoring, or pytest hangs at exit
    if leaked is not None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(leaked.dispose())
        finally:
            loop.close()
    _metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory = original
    try:
        os.remove(tmp_path)
    except OSError:
        pass


def _request(user: dict | None) -> Request:
    req = Request({"type": "http", "method": "GET", "headers": [], "query_string": b"", "path": "/x"})
    if user is not None:
        req.state.user = user
    return req


# ── BUG-463 ──────────────────────────────────────────────────────────

def _uasr_datetime_columns():
    for mapper in uasr_models.Base.registry.mappers:
        cls = mapper.class_
        if cls.__module__ != uasr_models.__name__:
            continue
        for col in mapper.columns:
            if isinstance(col.type, DateTime) or isinstance(getattr(col.type, "impl", None), DateTime):
                yield f"{cls.__name__}.{col.key}", col


def test_bug_463_aware_datetimes_reach_asyncpg_as_naive_utc():
    """What asyncpg would be handed for a TIMESTAMP WITHOUT TIME ZONE parameter.

    asyncpg raises on an aware value for a naive column; SQLAlchemy's DateTime passes
    the value through untouched, so the column type itself must strip the tzinfo."""
    pg = asyncpg_dialect()
    cols = list(_uasr_datetime_columns())
    assert {"DriftEvent.created_at", "RecoveryRecord.created_at",
            "RecoveryRecord.completed_at", "RecoveryRecord.decided_at"} <= {n for n, _ in cols}

    aware = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    for name, col in cols:
        assert getattr(col.type, "timezone", False) is False, name  # still a naive column: no migration
        proc = col.type._cached_bind_processor(pg)
        bound = proc(aware) if proc else aware
        assert bound.tzinfo is None, f"{name} would send an aware datetime to asyncpg"
        assert bound == aware.replace(tzinfo=None), name


@pytest.mark.asyncio
async def test_bug_463_naive_values_still_round_trip_on_sqlite():
    await init_uasr_db()
    rec_id = f"r6_{uuid.uuid4().hex[:8]}"
    async with get_session_factory()() as db:
        db.add(DriftEvent(id=rec_id, source_id="s463", drift_type="schema"))
        await db.commit()
        ev = await db.get(DriftEvent, rec_id)
        assert ev.created_at.tzinfo is None


# ── BUG-464 ──────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def chain_env(tmp_path, monkeypatch):
    from shared import audit_ledger as L
    monkeypatch.setenv("AURA_LEDGER_DATABASE_URL",
                       f"sqlite+aiosqlite:///{tmp_path / f'l_{uuid.uuid4().hex}.db'}")
    L._engine = None
    L._session_factory = None
    L._schema_initialized = False
    L._tenant_locks.clear()
    await L.init_database()
    yield L
    await L.close_database()


@pytest.mark.asyncio
async def test_bug_464_a_concurrent_approve_on_another_worker_cannot_overwrite_a_reject(
        chain_env, monkeypatch):
    from shared import approval_chains as ac

    req = await ac.create_request(tenant_id="bankA", action_kind="healing_deploy",
                                  subject_id="rec-464", required_approvals=1, created_by="ops-ana")

    # Each gateway worker has its own asyncio lock, so the per-tenant lock does not
    # serialise these two votes.
    monkeypatch.setattr(chain_env, "_lock_for", lambda _tenant: asyncio.Lock())

    c_has_read, b_done = asyncio.Event(), asyncio.Event()
    real_get_row = ac._get_row
    reads = {"C": 0}

    async def interleaved_get_row(session, request_id, tenant_id):
        row = await real_get_row(session, request_id, tenant_id)
        name = asyncio.current_task().get_name()
        if name == "C":
            reads["C"] += 1
            if reads["C"] == 1:          # C read the pending row; now B votes in full
                c_has_read.set()
                await b_done.wait()
        elif name == "B":
            await c_has_read.wait()
        return row

    monkeypatch.setattr(ac, "_get_row", interleaved_get_row)

    async def vote_b():
        try:
            return await ac.decide(request_id=req["id"], tenant_id="bankA",
                                   approver="rev-bob", approve=False)
        finally:
            b_done.set()

    b = asyncio.create_task(vote_b(), name="B")
    c = asyncio.create_task(ac.decide(request_id=req["id"], tenant_id="bankA",
                                      approver="rev-carol", approve=True), name="C")
    b_result, c_result = await asyncio.gather(b, c, return_exceptions=True)

    assert b_result["status"] == "rejected"
    assert isinstance(c_result, ValueError) and "resolved" in str(c_result), c_result
    final = await ac.get_request(req["id"], "bankA")
    assert final["status"] == "rejected"
    assert [d["approver"] for d in final["decisions"]] == ["rev-bob"]


@pytest.mark.asyncio
async def test_bug_464_two_concurrent_approvals_both_count(chain_env, monkeypatch):
    from shared import approval_chains as ac

    req = await ac.create_request(tenant_id="bankA", action_kind="healing_deploy",
                                  subject_id="rec-464b", required_approvals=2, created_by="ops-ana")
    monkeypatch.setattr(chain_env, "_lock_for", lambda _tenant: asyncio.Lock())

    both_read = asyncio.Barrier(2)
    real_get_row = ac._get_row
    first_read: set = set()

    async def interleaved_get_row(session, request_id, tenant_id):
        row = await real_get_row(session, request_id, tenant_id)
        name = asyncio.current_task().get_name()
        if name in ("B", "C") and name not in first_read:
            first_read.add(name)
            await both_read.wait()
        return row

    monkeypatch.setattr(ac, "_get_row", interleaved_get_row)
    await asyncio.gather(
        asyncio.create_task(ac.decide(request_id=req["id"], tenant_id="bankA",
                                      approver="rev-bob", approve=True), name="B"),
        asyncio.create_task(ac.decide(request_id=req["id"], tenant_id="bankA",
                                      approver="rev-carol", approve=True), name="C"),
    )
    final = await ac.get_request(req["id"], "bankA")
    assert final["status"] == "approved"
    assert sorted(d["approver"] for d in final["decisions"]) == ["rev-bob", "rev-carol"]


# ── BUG-465 ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bug_465_metrics_poller_authenticates_and_publishes_only_aggregates(monkeypatch):
    import httpx
    from fastapi import FastAPI

    import api_gateway.main as gw
    from shared.middleware import JWTAuthMiddleware
    from shared.streaming_manager import streaming_manager
    from uasr import service as uasr_service

    # The real UASR metrics handler behind the real JWT gate, as in prod.
    uasr_app = FastAPI()
    uasr_app.add_middleware(JWTAuthMiddleware)
    uasr_app.get("/uasr/metrics")(uasr_service.get_metrics)
    from uasr.metrics import RecoveryEvent
    from uasr.models import DriftSeverity, DriftType
    uasr_service._tracker.record(RecoveryEvent(
        source_id="tenantA::payments", drift_type=DriftType.SCHEMA, severity=DriftSeverity.HIGH,
        status=RecoveryStatus.DEPLOYED, latency_seconds=1.0))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(gw.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.ASGITransport(app=uasr_app), **kw))

    stop = asyncio.Event()
    published = []

    async def capture(event):
        published.append(event)
        stop.set()

    monkeypatch.setattr(streaming_manager, "publish", capture)
    await asyncio.wait_for(gw._uasr_metrics_poller(stop), timeout=10)

    assert published, "the poller published nothing: UASR rejected its unauthenticated call"
    payload = published[0].payload
    assert "hu_score" in payload
    assert "per_source" not in payload, "untenanted event leaked every tenant's per-source stats"


# ── BUG-466 ──────────────────────────────────────────────────────────

def _batch(source: str, i: int) -> BatchPayload:
    return BatchPayload(source_id=source, batch_id=f"b{i}", columns=["v"], rows=[{"v": i}])


def test_bug_466_similarity_history_and_reference_versions_are_bounded():
    from uasr.semantic_gateway import SemanticGateway

    gw = SemanticGateway()
    for i in range(120):
        gw.check(_batch("one", i))
    assert len(gw._sim_history["one"]) <= 50

    for i in range(40):
        gw.register_baseline(_batch("one", 1000 + i))
    versions = gw.reference_versions("one")
    assert len(versions) <= 10
    assert versions[-1]["active"] is True


def test_bug_466_distinct_source_ids_cannot_grow_memory_without_limit(monkeypatch):
    from uasr import semantic_gateway as sg

    monkeypatch.setattr(sg, "_MAX_SOURCES", 5, raising=False)
    gw = sg.SemanticGateway()
    for i in range(40):
        gw.check(_batch(f"x{i}", i))
        gw.check(_batch(f"x{i}", i + 1))
    assert len(gw.matrix._store) <= 5
    assert len(gw._sim_history) <= 5
    # A read for an unknown source must not allocate an entry either.
    assert gw.matrix.active_embedding("never-seen") is None
    assert "never-seen" not in gw.matrix._store


# ── BUG-467 ──────────────────────────────────────────────────────────

async def _deployed_on_peer(source: str, status: RecoveryStatus = RecoveryStatus.DEPLOYED) -> str:
    rec_id = f"r467_{uuid.uuid4().hex[:8]}"
    async with get_session_factory()() as db:
        db.add(DriftEvent(id=rec_id + "_drift", source_id=source, drift_type="schema"))
        db.add(RecoveryRecord(id=rec_id, drift_event_id=rec_id + "_drift", source_id=source,
                              status=status.value, shim_code=SHIM))
        await db.commit()
    return rec_id


@pytest.mark.asyncio
async def test_bug_467_a_shim_approved_on_a_peer_replica_is_applied_here(monkeypatch):
    from uasr import service

    await init_uasr_db()
    monkeypatch.setattr(service, "_SHARED_SHIM_REGISTRY", True, raising=False)
    name = f"src_{uuid.uuid4().hex[:6]}"
    source = f"acme::{name}"
    assert service._loop.get_deployed_shims(source) == []   # this replica never saw it

    await _deployed_on_peer(source)                          # approved on replica A
    out = await service.list_shims(name, _request({"sub": "u1", "org_id": "acme"}))
    assert out["deployed_shims"] == 1


@pytest.mark.asyncio
async def test_bug_467_a_rollback_on_a_peer_replica_stops_this_replica_applying_the_shim(monkeypatch):
    from uasr import service

    await init_uasr_db()
    monkeypatch.setattr(service, "_SHARED_SHIM_REGISTRY", True, raising=False)
    name = f"src_{uuid.uuid4().hex[:6]}"
    source = f"acme::{name}"
    service._loop.hydrate_deployed_shims({source: [SHIM]})   # loaded at this replica's startup
    await _deployed_on_peer(source, RecoveryStatus.ROLLED_BACK)  # replica A rolled it back

    out = await service.list_shims(name, _request({"sub": "u1", "org_id": "acme"}))
    assert out["deployed_shims"] == 0
    assert service._loop.get_deployed_shims(source) == []
