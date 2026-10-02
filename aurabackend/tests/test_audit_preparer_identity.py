"""BUG-291: the AS 1215 preparer in the signed document and the ledger was whatever
``preparer_id`` the request body carried, so a caller could have an audit signed as
prepared by anyone."""
from __future__ import annotations

import asyncio

import pytest

from counterfactual_service import financial_report
from counterfactual_service import main as m

ALICE = {"sub": "alice", "org_id": "org-x", "role": "user"}


class _Captured(Exception):
    pass


def test_an_authenticated_caller_is_the_preparer_whatever_the_body_says():
    assert m._preparer(ALICE, "bob.cpa@firm.com") == "alice"
    assert m._preparer(ALICE, "system") == "alice"


def test_a_request_with_no_identity_keeps_the_body_value():
    assert m._preparer(None, "ada@bank.test") == "ada@bank.test"
    assert m._preparer(None, "") == "system"


def test_financial_audit_signs_the_callers_identity_not_the_bodys(monkeypatch):
    seen = {}

    def _capture(tenant, findings, fingerprint, threshold, **kwargs):
        seen.update(kwargs)
        raise _Captured()

    monkeypatch.setattr(financial_report, "build_completion_document", _capture)
    req = m.FinancialAuditRequest(tenant_id="ignored", preparer_id="bob.cpa@firm.com",
                                  journal_entries=[{"id": "je1", "amount": 10.0}])

    with pytest.raises(_Captured):
        asyncio.run(m.financial_audit(req, user=ALICE))

    assert seen["preparer_id"] == "alice"


def test_fairness_audit_payload_carries_the_callers_identity(monkeypatch):
    seen = {}

    async def _capture(job_id, payload):
        seen.update(payload)

    async def _exists(*a, **k):
        return True

    monkeypatch.setattr(m, "_run_audit_job_async", _capture)
    monkeypatch.setattr(m, "_upload_exists", lambda name, tenant: True)
    req = m.AuditRequest(uploaded_file="d.parquet", treatment="t", outcome="y",
                         confounders=[], preparer_id="bob.cpa@firm.com")

    async def call():
        out = await m.run_audit(req, user=ALICE)
        await asyncio.sleep(0)
        return out

    job = asyncio.run(call())
    m._jobs.pop(job["job_id"], None)

    assert seen["preparer_id"] == "alice"
    assert seen["tenant_id"] == "org-x"
