"""S38 — calibrated materiality: AS-2110 computes thresholds from the
population instead of the mock $50k constant; AS-2305 scans against
performance materiality instead of a disconnected hardcoded $100k."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.specialists.financial_auditor as fa


def _agent(monkeypatch):
    monkeypatch.setattr(fa, "audit_event", lambda *a, **k: None)
    return fa.FinancialAuditorAgent(tenant_id="t1")


def test_materiality_benchmark_one_pct_of_population(monkeypatch):
    agent = _agent(monkeypatch)
    # 10M absolute ledger value -> overall = 1% = 100k, performance = 75k.
    ledger = [{"internal_id": f"L{i}", "amount": 1_000_000.0} for i in range(10)]
    risk = asyncio.run(agent.execute_as2110_risk_assessment([], ledger=ledger))
    assert risk["overall_materiality"] == 100_000.0
    assert risk["materiality_threshold"] == 75_000.0      # operative (performance)
    assert risk["materiality_basis"] == 10_000_000.0


def test_materiality_floor_protects_small_batches(monkeypatch):
    agent = _agent(monkeypatch)
    # 1% of 250k = 2.5k -> floored at 10k overall, 7.5k performance.
    risk = asyncio.run(agent.execute_as2110_risk_assessment(
        [], ledger=[{"internal_id": "L1", "amount": 250_000.0}]))
    assert risk["overall_materiality"] == 10_000.0
    assert risk["materiality_threshold"] == 7_500.0


def test_materiality_fallback_without_financial_basis(monkeypatch):
    agent = _agent(monkeypatch)
    risk = asyncio.run(agent.execute_as2110_risk_assessment([], ledger=[]))
    assert risk["overall_materiality"] == 50_000.0        # documented default
    assert risk["materiality_threshold"] == 37_500.0
    assert risk["materiality_basis"] == 0.0


def test_credits_count_toward_basis(monkeypatch):
    agent = _agent(monkeypatch)
    # Absolute value: credits (negative amounts) are activity too.
    risk = asyncio.run(agent.execute_as2110_risk_assessment(
        [], ledger=[{"amount": 2_000_000.0}, {"amount": -2_000_000.0}]))
    assert risk["materiality_basis"] == 4_000_000.0
    assert risk["overall_materiality"] == 40_000.0


def test_as2305_scans_against_performance_materiality(monkeypatch):
    agent = _agent(monkeypatch)
    # Both entries are below the OLD hardcoded 100k but above performance
    # materiality (basis 80k -> floored overall 10k -> perf 7.5k): the old
    # disconnected threshold would have flagged NOTHING.
    ledger = [{"internal_id": "L1", "amount": 60_000.0},
              {"internal_id": "L2", "amount": 20_000.0}]
    result = asyncio.run(agent.run_full_audit(ledger, [], [], []))
    flagged = [f for f in result["findings"] if f.pcaob_standard == "AS 2305"]
    assert {f.evidence_payload["entry_id"] for f in flagged} == {"L1", "L2"}
    assert result["materiality_threshold"] == 7_500.0
    # The threshold an entry was judged against is part of the evidence.
    assert all(f.evidence_payload["materiality_threshold"] == 7_500.0 for f in flagged)


def test_entries_below_performance_materiality_stay_clean(monkeypatch):
    agent = _agent(monkeypatch)
    ledger = [{"internal_id": "L1", "amount": 5_000.0}]   # perf floor = 7.5k
    result = asyncio.run(agent.run_full_audit(ledger, [], [], []))
    assert [f for f in result["findings"] if f.pcaob_standard == "AS 2305"] == []
