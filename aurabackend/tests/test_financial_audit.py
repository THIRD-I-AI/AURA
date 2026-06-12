"""S34a — signed financial audit: report assembly, signing, verify, egress."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from counterfactual_service import financial_report as fr


def test_dataset_fingerprint_deterministic_and_sensitive():
    a = fr.dataset_fingerprint([{"amount": 1}], [], [], [])
    b = fr.dataset_fingerprint([{"amount": 1}], [], [], [])
    c = fr.dataset_fingerprint([{"amount": 2}], [], [], [])
    assert a == b and a != c
    assert len(a) == 64  # sha256 hex


def test_build_completion_document_shape():
    findings = [{"pcaob_standard": "AS 2305", "risk_level": "High", "description": "x",
                 "evidence_payload": {}, "requires_human_review": True}]
    doc = fr.build_completion_document("tenant-1", findings, "fp" * 16, 50000.0)
    assert doc["document_type"] == "EngagementCompletionDocument"
    assert doc["pcaob_standard"] == "AS 1215"
    assert doc["tenant_id"] == "tenant-1"
    assert doc["dataset_fingerprint"] == "fp" * 16
    assert doc["materiality_threshold"] == 50000.0
    assert doc["n_findings"] == 1
    assert doc["risk_counts"] == {"High": 1}
    assert doc["performed_by"]["agent"] == "FinancialAuditorAgent"
    assert "generated_at" in doc


def _store(monkeypatch):
    store = {}
    monkeypatch.setattr(fr.persistence, "write_artifact", lambda h, p: store.__setitem__(h, p) or h)
    monkeypatch.setattr(fr.persistence, "read_artifact", lambda h: store.get(h))
    monkeypatch.setattr(fr, "audit_event", lambda *a, **k: None)
    return store


def _doc():
    return fr.build_completion_document("t1", [{"pcaob_standard": "AS 2305", "risk_level": "High",
        "description": "x", "evidence_payload": {"amount": 1}, "requires_human_review": True}],
        "f" * 64, 50000.0)


def test_sign_and_verify_roundtrip(monkeypatch):
    _store(monkeypatch)
    stored = fr.sign_and_persist(_doc())
    assert stored["signature_status"] == "signed"
    assert len(stored["record_hash"]) == 64
    v = fr.verify_report(stored["record_hash"])
    assert v["verified"] is True


def test_verify_detects_tampering(monkeypatch):
    store = _store(monkeypatch)
    stored = fr.sign_and_persist(_doc())
    store[stored["record_hash"]]["findings"][0]["evidence_payload"]["amount"] = 999  # tamper
    assert fr.verify_report(stored["record_hash"])["verified"] is False


def test_verify_missing_returns_false(monkeypatch):
    _store(monkeypatch)
    assert fr.verify_report("0" * 64)["verified"] is False


def test_revoked_key_yields_unsigned(monkeypatch):
    _store(monkeypatch)
    monkeypatch.setattr(fr.cryptography, "is_revoked", lambda kid=None: True)
    stored = fr.sign_and_persist(_doc())
    assert stored["signature_status"] == "unsigned"


import asyncio


def test_run_full_audit_spans_standards(monkeypatch):
    import agents.specialists.financial_auditor as fa
    monkeypatch.setattr(fa, "audit_event", lambda *a, **k: None)
    agent = fa.FinancialAuditorAgent(tenant_id="t1")
    ledger = [{"internal_id": "L1", "account_code": "4000", "amount": 250000.0}]   # AS 2305 variance
    pos = [{"po_number": "PO-1"}]
    invoices = [{"invoice_number": "INV-9", "po_number": "PO-MISSING",
                 "employee_name": "Ada Lovelace"}]                                  # AS 2201 unmatched
    jes = [{"internal_id": "J1", "amount": 5000.0, "account_code": "6000", "vendor_id": "V1"},
           {"internal_id": "J2", "amount": 5000.0, "account_code": "6000", "vendor_id": "V1"}]  # AS 2401 dup+round
    result = asyncio.run(agent.run_full_audit(ledger, pos, invoices, jes))
    stds = {f.pcaob_standard for f in result["findings"]}
    assert {"AS 2305", "AS 2201", "AS 2401"}.issubset(stds)
    assert result["materiality_threshold"] == 7500.0


def test_run_full_audit_clean_batch_no_findings(monkeypatch):
    import agents.specialists.financial_auditor as fa
    monkeypatch.setattr(fa, "audit_event", lambda *a, **k: None)
    agent = fa.FinancialAuditorAgent(tenant_id="t1")
    result = asyncio.run(agent.run_full_audit(
        [{"internal_id": "L", "account_code": "4000", "amount": 100.0}],
        [{"po_number": "PO-1"}], [{"invoice_number": "I", "po_number": "PO-1"}],
        [{"internal_id": "J", "amount": 123.45, "account_code": "6000", "vendor_id": "V"}]))
    assert result["findings"] == []


def test_client_view_redacts_evidence_but_artifact_keeps_raw(monkeypatch):
    _store(monkeypatch)
    doc = fr.build_completion_document("t1", [{"pcaob_standard": "AS 2201", "risk_level": "Medium",
        "description": "unmatched", "evidence_payload": {"invoice": {"employee_name": "Ada", "po_number": "x"}},
        "requires_human_review": True}], "f" * 64, 50000.0)
    stored = fr.sign_and_persist(doc)
    view = fr.client_view(stored)
    # Egress view is redacted ...
    assert view["findings"][0]["evidence_payload"]["invoice"]["employee_name"] == "[REDACTED]"
    # ... but the signed artifact retains raw evidence AND still verifies.
    assert stored["findings"][0]["evidence_payload"]["invoice"]["employee_name"] == "Ada"
    assert fr.verify_report(stored["record_hash"])["verified"] is True


def test_financial_audit_endpoint_e2e(monkeypatch):
    _store(monkeypatch)
    import counterfactual_service.main as m
    req = m.FinancialAuditRequest(
        tenant_id="t1",
        ledger=[{"internal_id": "L1", "account_code": "4000", "amount": 250000.0}],
        purchase_orders=[{"po_number": "PO-1"}],
        invoices=[{"invoice_number": "INV-9", "po_number": "PO-MISSING", "employee_name": "Ada"}],
        journal_entries=[{"internal_id": "J1", "amount": 5000.0, "account_code": "6000", "vendor_id": "V1"}],
    )
    report = asyncio.run(m.financial_audit(req))
    assert report["signature_status"] == "signed"
    assert report["verify_url"].endswith(report["record_hash"])
    assert "Ada" not in str(report["findings"])           # egress redaction applied
    v = asyncio.run(m.financial_audit_verify(report["record_hash"]))
    assert v["verified"] is True
