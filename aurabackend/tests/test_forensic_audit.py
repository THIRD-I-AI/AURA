"""S39 — forensic depth for the PCAOB financial auditor.

Covers the techniques added on top of the S34/S38 core:
  * AS 2401 — Benford's-Law first-digit MAD test (gated on sample size)
  * AS 2401 — period-end cutoff / window-dressing detection
  * AS 2201 — three-way match (PO/invoice/goods-receipt) + authorization
              controls (segregation of duties, approval authority)
  * AS 2305 — expectation-based analytics from prior-period history

Every new test asserts the technique fires when it should AND that it stays
silent on the minimal inputs the pre-S39 tests use (backward compatibility).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.specialists.financial_auditor as fa


def _agent(monkeypatch):
    monkeypatch.setattr(fa, "audit_event", lambda *a, **k: None)
    return fa.FinancialAuditorAgent(tenant_id="t1")


def _benford_conforming(n_base: int = 1000):
    """Population whose first-digit frequencies follow Benford's Law.
    Distinct, non-round, single-vendor-unique amounts so the duplicate
    and round-dollar rules stay silent and only Benford is exercised."""
    entries = []
    idx = 0
    for d in range(1, 10):
        k = round(fa.BENFORD_EXPECTED[d] * n_base)
        for i in range(k):
            entries.append({"internal_id": f"B{idx}", "amount": d * 1000 + i + 0.37,
                            "account_code": "6000", "vendor_id": f"V{idx}"})
            idx += 1
    return entries


# ── AS 2401 — Benford's Law ────────────────────────────────────────

def test_benford_conforming_population_is_clean(monkeypatch):
    agent = _agent(monkeypatch)
    jes = _benford_conforming()
    findings = asyncio.run(agent.execute_as2401_fraud_detection(jes))
    benford = [f for f in findings if f.evidence_payload.get("test") == "benford_first_digit"]
    assert benford == []


def test_benford_nonconforming_population_fires(monkeypatch):
    agent = _agent(monkeypatch)
    # 60 distinct, non-round entries all leading with digit 1 — a gross
    # deviation from Benford (expected first-digit-1 freq is only ~30%).
    jes = [{"internal_id": f"J{i}", "amount": 1000 + i + 0.37,
            "account_code": "6000", "vendor_id": f"V{i}"} for i in range(60)]
    findings = asyncio.run(agent.execute_as2401_fraud_detection(jes))
    benford = [f for f in findings if f.evidence_payload.get("test") == "benford_first_digit"]
    assert len(benford) == 1
    assert benford[0].evidence_payload["n"] == 60
    assert benford[0].evidence_payload["mad"] > fa.BENFORD_MAD_NONCONFORMITY


def test_benford_gated_below_min_sample(monkeypatch):
    agent = _agent(monkeypatch)
    # Same gross skew but only 10 entries — below MIN_BENFORD_SAMPLE, so
    # the statistic is not computed into a finding.
    jes = [{"internal_id": f"J{i}", "amount": 1000 + i + 0.37,
            "account_code": "6000", "vendor_id": f"V{i}"} for i in range(10)]
    findings = asyncio.run(agent.execute_as2401_fraud_detection(jes))
    assert [f for f in findings if f.evidence_payload.get("test") == "benford_first_digit"] == []


# ── AS 2401 — period-end cutoff ────────────────────────────────────

def test_cutoff_flags_entry_near_period_end(monkeypatch):
    agent = _agent(monkeypatch)
    jes = [{"internal_id": "J1", "amount": 512.50, "posting_date": "2025-12-30"}]
    findings = asyncio.run(agent.execute_as2401_fraud_detection(jes, period_end="2025-12-31"))
    cutoff = [f for f in findings if f.evidence_payload.get("test") == "period_end_cutoff"]
    assert len(cutoff) == 1
    assert cutoff[0].evidence_payload["days_before_period_end"] == 1


def test_cutoff_silent_without_period_end(monkeypatch):
    agent = _agent(monkeypatch)
    jes = [{"internal_id": "J1", "amount": 512.50, "posting_date": "2025-12-30"}]
    findings = asyncio.run(agent.execute_as2401_fraud_detection(jes))
    assert [f for f in findings if f.evidence_payload.get("test") == "period_end_cutoff"] == []


def test_cutoff_silent_for_entry_far_from_period_end(monkeypatch):
    agent = _agent(monkeypatch)
    jes = [{"internal_id": "J1", "amount": 512.50, "posting_date": "2025-06-01"}]
    findings = asyncio.run(agent.execute_as2401_fraud_detection(jes, period_end="2025-12-31"))
    assert [f for f in findings if f.evidence_payload.get("test") == "period_end_cutoff"] == []


# ── AS 2201 — three-way match ──────────────────────────────────────

def test_three_way_match_flags_invoice_without_goods_receipt(monkeypatch):
    agent = _agent(monkeypatch)
    pos = [{"po_number": "PO-1"}]
    invoices = [{"invoice_number": "INV-1", "po_number": "PO-1"}]
    grs = [{"po_number": "PO-2"}]  # a receipt, but not for this invoice's PO
    findings = asyncio.run(agent.execute_as2201_internal_controls(pos, invoices, grs))
    three_way = [f for f in findings if f.evidence_payload.get("control") == "three_way_match"]
    assert len(three_way) == 1


def test_three_way_match_clean_when_receipt_present(monkeypatch):
    agent = _agent(monkeypatch)
    pos = [{"po_number": "PO-1"}]
    invoices = [{"invoice_number": "INV-1", "po_number": "PO-1"}]
    grs = [{"po_number": "PO-1"}]
    findings = asyncio.run(agent.execute_as2201_internal_controls(pos, invoices, grs))
    assert findings == []


def test_three_way_not_enforced_without_goods_receipts(monkeypatch):
    agent = _agent(monkeypatch)
    # No goods_receipts arg => only two-way match; a matched-PO invoice is clean.
    pos = [{"po_number": "PO-1"}]
    invoices = [{"invoice_number": "INV-1", "po_number": "PO-1"}]
    findings = asyncio.run(agent.execute_as2201_internal_controls(pos, invoices))
    assert findings == []


# ── AS 2201 — authorization controls ───────────────────────────────

def test_segregation_of_duties_violation(monkeypatch):
    agent = _agent(monkeypatch)
    pos = [{"po_number": "PO-1"}]
    invoices = [{"invoice_number": "INV-1", "po_number": "PO-1",
                 "entered_by": "alice", "approved_by": "alice"}]
    findings = asyncio.run(agent.execute_as2201_internal_controls(pos, invoices))
    sod = [f for f in findings if f.evidence_payload.get("control") == "segregation_of_duties"]
    assert len(sod) == 1 and sod[0].evidence_payload["person"] == "alice"


def test_segregation_clean_when_preparer_differs(monkeypatch):
    agent = _agent(monkeypatch)
    pos = [{"po_number": "PO-1"}]
    invoices = [{"invoice_number": "INV-1", "po_number": "PO-1",
                 "entered_by": "alice", "approved_by": "bob"}]
    findings = asyncio.run(agent.execute_as2201_internal_controls(pos, invoices))
    assert [f for f in findings if f.evidence_payload.get("control") == "segregation_of_duties"] == []


def test_approval_authority_exceeded(monkeypatch):
    agent = _agent(monkeypatch)
    pos = [{"po_number": "PO-1"}]
    invoices = [{"invoice_number": "INV-1", "po_number": "PO-1",
                 "approved_by": "bob", "amount": 50_000, "approval_limit": 10_000}]
    findings = asyncio.run(agent.execute_as2201_internal_controls(pos, invoices))
    auth = [f for f in findings if f.evidence_payload.get("control") == "approval_authority"]
    assert len(auth) == 1 and auth[0].evidence_payload["amount"] == 50_000


# ── AS 2305 — expectation-based analytics ──────────────────────────

def test_expectation_deviation_flags_below_absolute_threshold(monkeypatch):
    agent = _agent(monkeypatch)
    # Entry of 1,000 is far below the 7,500 absolute threshold, but the
    # account historically averaged 100,000 — a material unexpected drop.
    ledger = [{"internal_id": "L1", "account_code": "4000", "amount": 1_000.0}]
    history = [{"account_code": "4000", "amount": 100_000.0},
               {"account_code": "4000", "amount": 100_000.0}]
    findings = asyncio.run(agent.execute_as2305_analytical_procedures(ledger, 7_500.0, history))
    dev = [f for f in findings if f.evidence_payload.get("test") == "expectation_deviation"]
    assert len(dev) == 1 and dev[0].evidence_payload["expected"] == 100_000.0


def test_expectation_in_line_is_clean(monkeypatch):
    agent = _agent(monkeypatch)
    ledger = [{"internal_id": "L1", "account_code": "4000", "amount": 1_100.0}]
    history = [{"account_code": "4000", "amount": 1_000.0}]
    findings = asyncio.run(agent.execute_as2305_analytical_procedures(ledger, 7_500.0, history))
    assert findings == []


def test_as2305_absolute_threshold_still_fires_without_history(monkeypatch):
    agent = _agent(monkeypatch)
    ledger = [{"internal_id": "L1", "account_code": "4000", "amount": 60_000.0}]
    findings = asyncio.run(agent.execute_as2305_analytical_procedures(ledger, 7_500.0))
    absolute = [f for f in findings if f.evidence_payload.get("test") == "absolute_materiality"]
    assert len(absolute) == 1


# ── run_full_audit threading ───────────────────────────────────────

def test_run_full_audit_threads_goods_receipts_and_period_end(monkeypatch):
    agent = _agent(monkeypatch)
    ledger = [{"internal_id": "L1", "account_code": "4000", "amount": 100.0}]
    pos = [{"po_number": "PO-1"}]
    invoices = [{"invoice_number": "INV-1", "po_number": "PO-1"}]  # matched PO, no GR
    jes = [{"internal_id": "J1", "amount": 222.22, "posting_date": "2025-12-31"}]
    result = asyncio.run(agent.run_full_audit(
        ledger, pos, invoices, jes, goods_receipts=[{"po_number": "PO-2"}], period_end="2025-12-31"))
    controls = {f.evidence_payload.get("control") for f in result["findings"]
                if f.pcaob_standard == "AS 2201"}
    tests = {f.evidence_payload.get("test") for f in result["findings"]
             if f.pcaob_standard == "AS 2401"}
    assert "three_way_match" in controls
    assert "period_end_cutoff" in tests


# ── S40 — one-click demo dataset trips every technique ─────────────

def test_forensic_demo_dataset_trips_every_technique(monkeypatch):
    from counterfactual_service.forensic_demo import forensic_demo_dataset
    agent = _agent(monkeypatch)
    d = forensic_demo_dataset()
    result = asyncio.run(agent.run_full_audit(
        d["ledger"], d["purchase_orders"], d["invoices"], d["journal_entries"],
        historical_reports=d["historical_reports"], goods_receipts=d["goods_receipts"],
        period_end=d["period_end"]))
    findings = result["findings"]

    as2305 = {f.evidence_payload.get("test") for f in findings if f.pcaob_standard == "AS 2305"}
    assert {"absolute_materiality", "expectation_deviation"} <= as2305

    as2201 = {f.evidence_payload.get("control") for f in findings if f.pcaob_standard == "AS 2201"}
    assert {"two_way_match", "three_way_match", "segregation_of_duties", "approval_authority"} <= as2201

    as2401 = [f for f in findings if f.pcaob_standard == "AS 2401"]
    descs = " ".join(f.description.lower() for f in as2401)
    as2401_tests = {f.evidence_payload.get("test") for f in as2401}
    assert "duplicate" in descs and "round-dollar" in descs
    assert {"benford_first_digit", "period_end_cutoff"} <= as2401_tests
