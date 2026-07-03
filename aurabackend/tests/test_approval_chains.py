"""Collaboration v1 — N-of-M signed approval chains (enterprise roadmap #2).

Multiple humans decide on one action (pipeline deploy, healing shim, audit
sign-off). Every decision is appended to the tamper-evident per-tenant audit
ledger; the chain completes only when N DISTINCT approvers approve, any
single reject fails closed, and the requester cannot approve their own
request (segregation of duties, AS-2201).
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import approval_chains as ac
from shared import audit_ledger as L


@pytest_asyncio.fixture
async def chain_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_LEDGER_DATABASE_URL",
                       f"sqlite+aiosqlite:///{tmp_path / f'l_{uuid.uuid4().hex}.db'}")
    L._engine = None
    L._session_factory = None
    L._schema_initialized = False
    L._tenant_locks.clear()
    await L.init_database()
    yield
    await L.close_database()


@pytest.mark.asyncio
async def test_two_of_n_chain_approves_and_ledger_chains_every_decision(chain_env):
    req = await ac.create_request(
        tenant_id="bankA", action_kind="healing_deploy", subject_id="rec-77",
        required_approvals=2, created_by="ops-ana")
    assert req["status"] == "pending"

    r1 = await ac.decide(request_id=req["id"], tenant_id="bankA", approver="rev-bob", approve=True)
    assert r1["status"] == "pending" and r1["approvals"] == 1

    r2 = await ac.decide(request_id=req["id"], tenant_id="bankA", approver="rev-carol", approve=True)
    assert r2["status"] == "approved" and r2["approvals"] == 2

    hist = await L.subject_history("bankA", "rec-77")
    kinds = [h.kind for h in hist]
    assert kinds.count("approval_decision") == 2       # every vote is tamper-evident
    assert "approval_requested" in kinds
    assert (await L.verify_chain("bankA"))["ok"] is True


@pytest.mark.asyncio
async def test_same_approver_cannot_vote_twice(chain_env):
    req = await ac.create_request(tenant_id="t", action_kind="pipeline_deploy",
                                  subject_id="p1", required_approvals=2, created_by="ana")
    await ac.decide(request_id=req["id"], tenant_id="t", approver="bob", approve=True)
    with pytest.raises(ValueError, match="already decided"):
        await ac.decide(request_id=req["id"], tenant_id="t", approver="bob", approve=True)


@pytest.mark.asyncio
async def test_requester_cannot_approve_own_request(chain_env):
    """Segregation of duties: the human who asks for the action cannot be
    one of the humans who authorizes it."""
    req = await ac.create_request(tenant_id="t", action_kind="audit_signoff",
                                  subject_id="c1", required_approvals=1, created_by="ana")
    with pytest.raises(ValueError, match="segregation"):
        await ac.decide(request_id=req["id"], tenant_id="t", approver="ana", approve=True)


@pytest.mark.asyncio
async def test_single_reject_fails_closed(chain_env):
    req = await ac.create_request(tenant_id="t", action_kind="healing_deploy",
                                  subject_id="r2", required_approvals=3, created_by="ana")
    await ac.decide(request_id=req["id"], tenant_id="t", approver="bob", approve=True)
    r = await ac.decide(request_id=req["id"], tenant_id="t", approver="carol", approve=False)
    assert r["status"] == "rejected"
    # resolved chains accept no further votes
    with pytest.raises(ValueError, match="resolved"):
        await ac.decide(request_id=req["id"], tenant_id="t", approver="dave", approve=True)


@pytest.mark.asyncio
async def test_tenant_isolation_on_decide(chain_env):
    req = await ac.create_request(tenant_id="bankA", action_kind="healing_deploy",
                                  subject_id="r3", required_approvals=1, created_by="ana")
    with pytest.raises(LookupError):
        await ac.decide(request_id=req["id"], tenant_id="bankB", approver="mallory", approve=True)


@pytest.mark.asyncio
async def test_pending_lists_only_open_requests_for_tenant(chain_env):
    a = await ac.create_request(tenant_id="t", action_kind="x", subject_id="s1",
                                required_approvals=1, created_by="ana")
    await ac.create_request(tenant_id="other", action_kind="x", subject_id="s2",
                            required_approvals=1, created_by="ana")
    await ac.decide(request_id=a["id"], tenant_id="t", approver="bob", approve=True)
    b = await ac.create_request(tenant_id="t", action_kind="y", subject_id="s3",
                                required_approvals=2, created_by="ana")
    open_reqs = await ac.pending("t")
    assert [r["id"] for r in open_reqs] == [b["id"]]
