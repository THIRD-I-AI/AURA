"""UASR tenant scoping (BUG-262, BUG-263, BUG-264, BUG-270; closes the BUG-072 gap).

UASR trusted whatever source id a caller sent and filtered nothing, so one tenant
could read, heal, approve and roll back another tenant's sources. Every handler now
namespaces writes into the caller's tenant and limits reads and decisions to it.

Uses the same isolated metadata DB as tests/test_uasr_approval_reaper.py."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid

import pytest
from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
from uasr import service  # noqa: E402
from uasr.db import get_session_factory, init_uasr_db  # noqa: E402
from uasr.models import DriftEvent, RecoveryRecord, RecoveryStatus  # noqa: E402
from uasr.tenancy import owns_source, same_tenant, scoped_source, source_tenant  # noqa: E402

SHIM = "def transform(rows):\n    return rows\n"


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_uasr_tenancy_{uuid.uuid4().hex[:8]}.db")
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


@pytest.fixture(autouse=True)
async def _tables():
    await init_uasr_db()
    yield


def _as(tenant: str | None, role: str = "member") -> Request:
    req = Request({"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/x"})
    if tenant is not None:
        req.state.user = {"org_id": tenant, "sub": f"user@{tenant}", "role": role}
    return req


async def _recovery(source: str, status: RecoveryStatus = RecoveryStatus.PENDING_APPROVAL) -> str:
    rec_id = f"ts_{uuid.uuid4().hex[:8]}"
    async with get_session_factory()() as db:
        db.add(DriftEvent(id=rec_id + "_drift", source_id=source, drift_type="schema"))
        db.add(RecoveryRecord(id=rec_id, drift_event_id=rec_id + "_drift", source_id=source,
                              status=status.value, shim_code=SHIM))
        await db.commit()
    return rec_id


async def _status(rec_id: str) -> str:
    async with get_session_factory()() as db:
        return (await db.get(RecoveryRecord, rec_id)).status


# ── the rule itself ────────────────────────────────────────────────────────

def test_source_ids_are_namespaced_not_trusted():
    assert scoped_source("acme", "orders") == "acme::orders"
    assert scoped_source("acme", "acme::orders") == "acme::orders"          # idempotent
    assert scoped_source("acme", "acme::folder::orders") == "acme::folder::orders"
    # naming another tenant's source lands inside the caller's own namespace
    assert scoped_source("evil", "acme::orders") == "evil::acme::orders"
    assert scoped_source(None, "orders") == "orders"                        # no identity: unscoped

    assert owns_source("acme", "acme::orders") and not owns_source("acme", "acme2::orders")
    assert not owns_source("acme", "orders") and not owns_source("acme", None)
    assert owns_source(None, "anything")

    assert source_tenant("acme::orders") == "acme" and source_tenant("orders") is None
    assert same_tenant("acme::a", "acme::b") and not same_tenant("acme::a", "evil::b")
    assert same_tenant("a", "b") and not same_tenant("a", "acme::b")


# ── writes ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rollback_of_another_tenants_source_cannot_reach_it():
    victim = f"acme::orders_{uuid.uuid4().hex[:6]}"
    service._loop.hydrate_deployed_shims({victim: [SHIM]})

    async with get_session_factory()() as db:
        with pytest.raises(HTTPException) as exc:
            await service.rollback_shim(service.RollbackRequest(source_id=victim), _as("evil"), db=db)

    assert exc.value.status_code == 404
    assert service._loop.get_deployed_shims(victim) == [SHIM]


@pytest.mark.asyncio
async def test_shims_listing_is_namespaced():
    victim = f"acme::orders_{uuid.uuid4().hex[:6]}"
    service._loop.hydrate_deployed_shims({victim: [SHIM]})

    mine = await service.list_shims(victim, _as("acme"))
    theirs = await service.list_shims(victim, _as("evil"))

    assert mine["deployed_shims"] == 1
    assert theirs["deployed_shims"] == 0 and theirs["source_id"] == f"evil::{victim}"


# ── reads ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lists_show_only_the_callers_tenant():
    mine = await _recovery(f"acme::orders_{uuid.uuid4().hex[:6]}")
    theirs = await _recovery(f"evil::orders_{uuid.uuid4().hex[:6]}")

    async with get_session_factory()() as db:
        pending = await service.pending_approvals(_as("acme"), limit=500, db=db)
        sources = await service.list_sources(_as("acme"), db=db)
        drift = await service.drift_status(_as("acme"), source_id=None, limit=500, db=db)
        everyone = await service.pending_approvals(_as(None), limit=500, db=db)

    ids = {r["id"] for r in pending["pending"]}
    assert mine in ids and theirs not in ids
    assert all(s["source_id"].startswith("acme::") for s in sources["sources"])
    assert all(e["source_id"].startswith("acme::") for e in drift["events"])
    assert {mine, theirs} <= {r["id"] for r in everyone["pending"]}  # unauthenticated: unscoped


@pytest.mark.asyncio
async def test_another_tenants_recovery_looks_like_a_missing_one():
    theirs = await _recovery(f"acme::orders_{uuid.uuid4().hex[:6]}")

    async with get_session_factory()() as db:
        with pytest.raises(HTTPException) as exc:
            await service.recovery_detail(theirs, _as("evil"), db=db)
        assert exc.value.status_code == 404
        assert (await service.recovery_detail(theirs, _as("acme"), db=db))["recovery"]["id"] == theirs


# ── decisions ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_tenant_cannot_approve_or_reject_another_tenants_recovery():
    source = f"acme::orders_{uuid.uuid4().hex[:6]}"
    held = await _recovery(source)

    async with get_session_factory()() as db:
        with pytest.raises(HTTPException) as exc:
            await service.approve_recovery(held, service.ApprovalRequest(approver="x"), _as("evil"), db=db)
        assert exc.value.status_code == 404
    async with get_session_factory()() as db:
        with pytest.raises(HTTPException) as exc:
            await service.reject_recovery(
                held, service.RejectionRequest(approver="x", reason="no"), _as("evil"), db=db)
        assert exc.value.status_code == 404

    assert await _status(held) == RecoveryStatus.PENDING_APPROVAL.value
    assert service._loop.get_deployed_shims(source) == []

    async with get_session_factory()() as db:
        out = await service.approve_recovery(held, service.ApprovalRequest(approver="x"), _as("acme"), db=db)
    assert out["status"] == "approved" and service._loop.get_deployed_shims(source) == [SHIM]


# ── the shared worker ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resuming_the_shared_worker_needs_the_admin_role(monkeypatch):
    class _Worker:
        is_paused = True
        resumed = False

        def resume(self):
            self.resumed = True

    worker = _Worker()
    monkeypatch.setattr(service, "_mapek_worker", worker)

    with pytest.raises(HTTPException) as exc:
        await service.mapek_resume(_as("acme", role="member"))
    assert exc.value.status_code == 403 and not worker.resumed

    assert (await service.mapek_resume(_as("acme", role="admin")))["resumed"] is True


# ── cross-source borrowing (BUG-072) ──────────────────────────────────────

def test_a_shim_is_never_borrowed_from_another_tenant():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from uasr.metrics import HealingMetricTracker
    from uasr.models import DriftType

    tracker = HealingMetricTracker()

    def _event(source):
        return SimpleNamespace(source_id=source, drift_type=DriftType.SCHEMA, status=RecoveryStatus.DEPLOYED,
                               shim_code=SHIM, timestamp=datetime.now(timezone.utc))

    tracker._events = [_event("evil::orders")]
    assert tracker.find_recent_deployed_shim(DriftType.SCHEMA, "acme::orders", 3600) is None

    tracker._events = [_event("evil::orders"), _event("acme::invoices")]
    assert tracker.find_recent_deployed_shim(DriftType.SCHEMA, "acme::orders", 3600) == ("acme::invoices", SHIM)


# ── BUG-349: the platform Kafka worker's recoveries ─────────────────────────

@pytest.mark.asyncio
async def test_an_admin_can_see_and_decide_on_the_kafka_workers_recoveries():
    # The shared MAPE-K worker files under an un-namespaced source no tenant owns, so
    # with auth on these held recoveries were invisible and undecidable for everyone.
    source = f"kafka_{uuid.uuid4().hex[:6]}"
    held = await _recovery(source)

    async with get_session_factory()() as db:
        pending = await service.pending_approvals(_as("ops", role="admin"), db=db)
        assert held in {r["id"] for r in pending["pending"]}
        assert (await service.recovery_detail(held, _as("ops", role="admin"), db=db))["recovery"]["id"] == held
        out = await service.approve_recovery(
            held, service.ApprovalRequest(approver="ops"), _as("ops", role="admin"), db=db)
    assert out["status"] == "approved" and out["recovery"]["status"] == RecoveryStatus.DEPLOYED.value
    service._loop.rollback_last_shim(source)


@pytest.mark.asyncio
async def test_a_member_still_cannot_reach_platform_recoveries():
    held = await _recovery(f"kafka_{uuid.uuid4().hex[:6]}")

    async with get_session_factory()() as db:
        pending = await service.pending_approvals(_as("acme"), db=db)
        assert held not in {r["id"] for r in pending["pending"]}
        with pytest.raises(HTTPException) as exc:
            await service.approve_recovery(held, service.ApprovalRequest(approver="x"), _as("acme"), db=db)
    assert exc.value.status_code == 404
    assert await _status(held) == RecoveryStatus.PENDING_APPROVAL.value
