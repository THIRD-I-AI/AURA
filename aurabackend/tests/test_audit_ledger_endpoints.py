"""Subsystem C — Task 3+5: the live /audit/financial path binds all inputs,
records the durable ledger, and the verify surface works end-to-end.

Closes the security-review finding: the signed fingerprint must change when a
previously-unbound input (goods_receipts) is added — i.e. the attestation
actually binds every audited input. Calls the async endpoint functions
directly (no lifespan/TestClient overhead)."""
from __future__ import annotations

import os
import sys
import uuid

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import audit_ledger as L


@pytest_asyncio.fixture
async def ledger_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_LEDGER_DATABASE_URL",
                       f"sqlite+aiosqlite:///{tmp_path / f'l_{uuid.uuid4().hex}.db'}")
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path / "audit"))
    L._engine = None
    L._session_factory = None
    L._schema_initialized = False
    L._tenant_locks.clear()
    await L.init_database()
    yield
    await L.close_database()


def _payload(**over):
    base = dict(
        tenant_id="orgX", subject_id="model-1", preparer_id="ada@bank.test",
        ledger=[{"account": "cash", "amount": 100}],
        journal_entries=[{"amount": 100, "id": 1}, {"amount": 200, "id": 2}],
    )
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_endpoint_binds_all_inputs_and_records_durable_ledger(ledger_env):
    from counterfactual_service.main import (
        FinancialAuditRequest,
        audit_ledger_proof,
        audit_ledger_subject_history,
        audit_ledger_verify,
        financial_audit,
    )

    r_plain = await financial_audit(FinancialAuditRequest(**_payload()))
    r_gr = await financial_audit(FinancialAuditRequest(
        **_payload(goods_receipts=[{"po_number": "PO1", "qty": 5}])))

    # SECURITY FIX: adding a previously-unbound input changes the signed fingerprint
    assert r_plain["dataset_fingerprint"] != r_gr["dataset_fingerprint"]

    # both audits chained into the subject's durable history
    hist = await audit_ledger_subject_history("model-1", tenant_id="orgX")
    assert hist["count"] == 2
    assert hist["audits"][0]["preparer_id"] == "ada@bank.test"

    # the chain verifies, and a cert has an independently-checkable inclusion proof
    assert (await audit_ledger_verify(tenant_id="orgX"))["ok"] is True
    cert = hist["audits"][0]["cert_hash"]
    proof = await audit_ledger_proof(cert, tenant_id="orgX")
    assert proof["cert_hash"] == cert and proof["tree_size"] == 2


@pytest.mark.asyncio
async def test_proof_for_unknown_cert_404s(ledger_env):
    from fastapi import HTTPException

    from counterfactual_service.main import audit_ledger_proof
    with pytest.raises(HTTPException) as ei:
        await audit_ledger_proof("d" * 64, tenant_id="orgX")
    assert ei.value.status_code == 404
