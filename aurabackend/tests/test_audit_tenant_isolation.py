"""Security-review fix — the audit ledger's tenant MUST come from the VERIFIED
token, never a client-supplied body value (which a token holder could forge to
pollute another org's audit trail — exactly what require_tenant warns against).
Also fixes the fail-open drift: a legacy token (no org_id) falls back to its
subject, not "default", so its audits + reviews land in the same tenant."""
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
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path / "art"))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path / "aud"))
    L._engine = None
    L._session_factory = None
    L._schema_initialized = False
    L._tenant_locks.clear()
    await L.init_database()
    yield
    await L.close_database()


def test_ledger_tenant_derivation_matches_require_tenant():
    from counterfactual_service.main import _ledger_tenant
    assert _ledger_tenant({"org_id": "bankA", "sub": "x"}) == "bankA"
    assert _ledger_tenant({"sub": "x"}) == "x"        # legacy: sub, NOT "default" (the fail-open bug)
    assert _ledger_tenant(None) == "default"          # anonymous / open call


@pytest.mark.asyncio
async def test_financial_audit_ledger_tenant_from_token_not_body(ledger_env):
    from counterfactual_service.main import FinancialAuditRequest, financial_audit
    req = FinancialAuditRequest(
        tenant_id="attacker-controlled", subject_id="m1", preparer_id="ada",
        ledger=[{"account": "cash", "amount": 100}],
        journal_entries=[{"amount": 100, "id": 1}, {"amount": 200, "id": 2}])
    await financial_audit(req, user={"org_id": "bankA", "sub": "ada@bank.test"})

    assert len(await L.subject_history("bankA", "m1")) == 1        # recorded under the TOKEN tenant
    assert await L.subject_history("attacker-controlled", "m1") == []  # body tenant ignored


@pytest.mark.asyncio
async def test_review_uses_token_tenant_and_isolates(ledger_env):
    # seed an audit in bankA's ledger
    await L.append_audit(tenant_id="bankA", kind="financial_audit_completed", subject_id="m1",
                         subject_type="model", preparer_id="ada", cert_hash="a" * 64,
                         input_fingerprint="f" * 64, payload={})
    from counterfactual_service.main import _append_review_to_ledger
    # a reviewer whose token tenant is bankB CANNOT chain to bankA's audit
    await _append_review_to_ledger(tenant_id="bankB", audit_cert_hash="a" * 64,
                                   review_stored={"record_hash": "b" * 64}, reviewer_id="rob",
                                   approved=True)
    assert await L.subject_history("bankB", "m1") == []           # no cross-tenant chaining
    assert len(await L.subject_history("bankA", "m1")) == 1       # bankA's audit untouched
