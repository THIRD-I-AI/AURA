"""Regression test for BUG-025b (docs/BUG_REGISTRY.md).

execute_as2305_analytical_procedures and execute_as2401_fraud_detection read
raw `entry.get("amount", 0)` / `je.get("amount", 0)` and compared it
directly, unlike the rest of this file's `_money()` helper (built to swallow
non-numeric amounts). A single bad row from arbitrary uploaded ledger data
crashed the entire audit batch with a TypeError instead of being treated as
zero, the same way `_money()` already handles it everywhere else.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.specialists.financial_auditor as fa


def _agent(monkeypatch):
    monkeypatch.setattr(fa, "audit_event", lambda *a, **k: None)
    return fa.FinancialAuditorAgent(tenant_id="t1")


def test_as2305_non_numeric_amount_does_not_crash_batch(monkeypatch):
    agent = _agent(monkeypatch)
    ledger = [
        {"internal_id": "L1", "account_code": "A1", "amount": "not-a-number"},
        {"internal_id": "L2", "account_code": "A1", "amount": 200_000.0},
    ]
    findings = asyncio.run(
        agent.execute_as2305_analytical_procedures(ledger, materiality_threshold=100_000.0)
    )
    # The bad row is treated as zero (like _money() everywhere else) and
    # doesn't crash the batch; the genuinely large entry still gets flagged.
    assert {f.evidence_payload["entry_id"] for f in findings} == {"L2"}


def test_as2401_non_numeric_amount_does_not_crash_batch(monkeypatch):
    agent = _agent(monkeypatch)
    journal_entries = [
        {"internal_id": "J1", "account_code": "A1", "vendor_id": "V1", "amount": None},
        {"internal_id": "J2", "account_code": "A1", "vendor_id": "V1", "amount": "garbage"},
        {"internal_id": "J3", "account_code": "A2", "vendor_id": "V2", "amount": 5000.0},
    ]
    # Must not raise despite the non-numeric/missing amounts.
    findings = asyncio.run(agent.execute_as2401_fraud_detection(journal_entries))
    round_dollar = [f for f in findings if f.evidence_payload.get("je_id") == "J3"]
    assert round_dollar  # the one genuinely round-dollar entry is still flagged


def test_run_full_audit_survives_bad_row_in_full_batch(monkeypatch):
    agent = _agent(monkeypatch)
    ledger = [
        {"internal_id": "L1", "account_code": "A1", "amount": float("nan")},
        {"internal_id": "L2", "account_code": "A1", "amount": 150_000.0},
    ]
    result = asyncio.run(agent.run_full_audit(ledger, [], [], []))
    assert result["findings"]  # completed without raising
