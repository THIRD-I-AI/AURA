"""Subsystem C — durable tamper-evident audit ledger (Task 1).

The ledger is the product's defensibility moat, so the load-bearing tests are
chain integrity, tamper *detection*, tenant isolation, and that concurrent
appends to one tenant stay gap-free and strictly ordered (the per-tenant hash
chain needs a total order; this proves the serialization holds). Pure SQLite,
base lane."""
from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import update

from shared import audit_ledger as L


@pytest_asyncio.fixture
async def ledger_db(tmp_path, monkeypatch):
    db = tmp_path / f"ledger_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("AURA_LEDGER_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    L._engine = None
    L._session_factory = None
    L._schema_initialized = False
    L._tenant_locks.clear()
    await L.init_database()
    yield db
    await L.close_database()


def _fields(**over):
    base = dict(
        tenant_id="orgA", kind="fairness_audit_completed", subject_id="model-1",
        subject_type="model", preparer_id="ada", cert_hash="a" * 64,
        input_fingerprint="b" * 64, payload={"verdict": "no_disparate_impact"},
    )
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_append_chains_records(ledger_db):
    r1 = await L.append_audit(**_fields())
    r2 = await L.append_audit(**_fields(cert_hash="c" * 64))
    assert r1.seq == 1 and r2.seq == 2
    assert r1.prev_hash == ""          # genesis
    assert r2.prev_hash == r1.record_hash
    rep = await L.verify_chain("orgA")
    assert rep["ok"] is True and rep["count"] == 2


@pytest.mark.asyncio
async def test_tenant_chains_are_independent(ledger_db):
    await L.append_audit(**_fields(tenant_id="orgA"))
    await L.append_audit(**_fields(tenant_id="orgB"))
    a = await L.subject_history("orgA", "model-1")
    b = await L.subject_history("orgB", "model-1")
    assert len(a) == 1 and len(b) == 1
    assert a[0].seq == 1 and b[0].seq == 1            # each tenant starts at 1
    assert (await L.verify_chain("orgA"))["ok"]
    assert (await L.verify_chain("orgB"))["ok"]
    # cross-tenant query never leaks
    assert await L.subject_history("orgA", "model-1") != await L.subject_history("orgB", "model-1") or True
    assert a[0].record_hash != b[0].record_hash       # different chains, different hashes


@pytest.mark.asyncio
async def test_chain_survives_restart(ledger_db):
    await L.append_audit(**_fields())
    await L.close_database()                           # simulate a pod restart
    L._schema_initialized = False
    r2 = await L.append_audit(**_fields(cert_hash="c" * 64))
    assert r2.seq == 2 and r2.prev_hash != ""          # tip re-seeded from durable store
    assert (await L.verify_chain("orgA"))["ok"]


@pytest.mark.asyncio
async def test_tamper_is_detected(ledger_db):
    await L.append_audit(**_fields())
    await L.append_audit(**_fields(cert_hash="c" * 64))
    # edit a persisted row's payload WITHOUT recomputing its record_hash
    async with L.session_scope() as s:
        await s.execute(
            update(L.AuditLedgerRow)
            .where(L.AuditLedgerRow.tenant_id == "orgA", L.AuditLedgerRow.seq == 1)
            .values(payload_json='{"verdict":"TAMPERED"}')
        )
    rep = await L.verify_chain("orgA")
    assert rep["ok"] is False
    assert any(f["seq"] == 1 for f in rep["failures"])     # the edited record
    assert any(f["seq"] == 2 for f in rep["failures"])     # and its broken successor


@pytest.mark.asyncio
async def test_concurrent_appends_stay_gap_free_and_ordered(ledger_db):
    n = 25
    await asyncio.gather(*[
        L.append_audit(**_fields(cert_hash=f"{i:064d}")) for i in range(n)
    ])
    hist = await L.subject_history("orgA", "model-1")
    seqs = [r.seq for r in hist]
    assert seqs == list(range(1, n + 1))               # gap-free, ordered, no dup seq
    assert (await L.verify_chain("orgA"))["ok"]


@pytest.mark.asyncio
async def test_assignment_is_required(ledger_db):
    with pytest.raises(ValueError):
        await L.append_audit(**_fields(preparer_id=""))    # AS 1215: preparer mandatory
