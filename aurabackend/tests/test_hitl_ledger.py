"""Subsystem C completion — the HITL human sign-off chains into the durable
ledger too, so the AS-1215 preparer→reviewer trail is exam-provable end to end.
A review record inherits the audited subject + preparer from the original audit
(looked up by its cert hash) and adds the reviewer + decision."""
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


async def _seed_audit():
    return await L.append_audit(
        tenant_id="bankA", kind="fairness_audit_completed", subject_id="model-1",
        subject_type="model", preparer_id="ada@bank.test", cert_hash="a" * 64,
        input_fingerprint="f" * 64, payload={"verdict": "disparate_impact"})


@pytest.mark.asyncio
async def test_record_for_cert_finds_the_audit(ledger_env):
    await _seed_audit()
    rec = await L.record_for_cert("bankA", "a" * 64)
    assert rec is not None and rec.subject_id == "model-1" and rec.preparer_id == "ada@bank.test"
    assert await L.record_for_cert("bankA", "z" * 64) is None      # unknown cert
    assert await L.record_for_cert("bankB", "a" * 64) is None      # wrong tenant


@pytest.mark.asyncio
async def test_review_chains_and_inherits_subject(ledger_env):
    await _seed_audit()
    from counterfactual_service.main import _append_review_to_ledger
    await _append_review_to_ledger(
        tenant_id="bankA", audit_cert_hash="a" * 64,
        review_stored={"record_hash": "b" * 64}, reviewer_id="rob@bank.test", approved=True)

    hist = await L.subject_history("bankA", "model-1")
    assert len(hist) == 2                              # audit + its review, same subject
    review = hist[1]
    assert review.kind == "human_review"
    assert review.reviewer_id == "rob@bank.test"
    assert review.preparer_id == "ada@bank.test"       # inherited from the audit
    assert review.cert_hash == "b" * 64
    assert review.payload["audit_cert_hash"] == "a" * 64
    assert review.decided_at
    assert (await L.verify_chain("bankA"))["ok"] is True


@pytest.mark.asyncio
async def test_review_for_unledgered_audit_is_skipped(ledger_env):
    # audit not in this tenant's ledger (e.g. predates it) → don't fabricate an orphan review
    from counterfactual_service.main import _append_review_to_ledger
    await _append_review_to_ledger(
        tenant_id="bankA", audit_cert_hash="a" * 64,
        review_stored={"record_hash": "b" * 64}, reviewer_id="rob", approved=False)
    assert await L.verify_chain("bankA") == {"tenant_id": "bankA", "count": 0, "failures": [], "ok": True}
