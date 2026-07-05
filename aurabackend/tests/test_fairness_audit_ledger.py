"""Subsystem C completion — the CAUSAL FAIRNESS audit (the fair-lending
product's core, /counterfactual/audit) must chain its signed cert into the
durable tamper-evident ledger, exactly like the financial auditor does.

Tests the ledger-append helper directly with a synthetic audit result so it
doesn't need the heavy out-of-process causal fan-out."""
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
    L._engine = None
    L._session_factory = None
    L._schema_initialized = False
    L._tenant_locks.clear()
    await L.init_database()
    yield
    await L.close_database()


@pytest.mark.asyncio
async def test_fairness_audit_chains_to_ledger(ledger_env):
    from counterfactual_service.main import _append_fairness_audit_to_ledger
    result = {"audit_record_hash": "a" * 64, "dataset_fingerprint": "b" * 64,
              "signature_status": "signed"}
    payload = {"tenant_id": "bankA", "subject_id": "model-underwriting-v3",
               "subject_type": "model", "preparer_id": "ada@bank.test",
               "treatment": "race_proxy", "outcome": "approved"}
    await _append_fairness_audit_to_ledger(result, payload)

    hist = await L.subject_history("bankA", "model-underwriting-v3")
    assert len(hist) == 1
    rec = hist[0]
    assert rec.kind == "fairness_audit_completed"
    assert rec.cert_hash == "a" * 64
    assert rec.input_fingerprint == "b" * 64
    assert rec.preparer_id == "ada@bank.test"
    assert (await L.verify_chain("bankA"))["ok"] is True


@pytest.mark.asyncio
async def test_unsigned_fairness_audit_is_not_chained(ledger_env):
    from counterfactual_service.main import _append_fairness_audit_to_ledger
    # no audit_record_hash → there's no cert to chain; don't fabricate a record
    await _append_fairness_audit_to_ledger(
        {"signature_status": "unsigned"}, {"tenant_id": "bankA", "subject_id": "m1"})
    assert await L.subject_history("bankA", "m1") == []


@pytest.mark.asyncio
async def test_ledger_failure_never_raises(ledger_env, monkeypatch):
    # a ledger hiccup must not blow up the audit job
    import shared.audit_ledger as mod
    from counterfactual_service.main import _append_fairness_audit_to_ledger

    async def _boom(**kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(mod, "append_audit", _boom)
    # should swallow + log, not raise
    await _append_fairness_audit_to_ledger(
        {"audit_record_hash": "c" * 64, "dataset_fingerprint": "d" * 64},
        {"tenant_id": "bankA", "subject_id": "m1", "preparer_id": "x"})


def test_audit_request_accepts_identity():
    from counterfactual_service.main import AuditRequest
    r = AuditRequest(uploaded_file="f.csv", treatment="t", outcome="o",
                     tenant_id="bankA", subject_id="m1", subject_type="model",
                     preparer_id="ada")
    assert r.subject_id == "m1" and r.preparer_id == "ada" and r.subject_type == "model"
