"""S34b — HITL exception queue: finding_id, pending view, signed decisions."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from counterfactual_service import financial_report as fr


def _findings():
    return [
        {"pcaob_standard": "AS 2305", "risk_level": "High", "description": "variance",
         "evidence_payload": {"entry_id": "L1", "amount": 250000.0},
         "requires_human_review": True},
        # Content-identical twin of the next finding — ids must still differ.
        {"pcaob_standard": "AS 2401", "risk_level": "High", "description": "dup",
         "evidence_payload": {"je_id": "J1"}, "requires_human_review": True},
        {"pcaob_standard": "AS 2401", "risk_level": "High", "description": "dup",
         "evidence_payload": {"je_id": "J1"}, "requires_human_review": True},
    ]


def _store(monkeypatch):
    store = {}
    monkeypatch.setattr(fr.persistence, "write_artifact", lambda h, p: store.__setitem__(h, p) or h)
    monkeypatch.setattr(fr.persistence, "read_artifact", lambda h: store.get(h))
    monkeypatch.setattr(fr, "audit_event", lambda *a, **k: None)
    return store


# ── Task 1: finding_id under the signature ────────────────────────────


def test_findings_get_deterministic_unique_finding_ids():
    doc1 = fr.build_completion_document("t1", _findings(), "f" * 64, 50000.0)
    doc2 = fr.build_completion_document("t1", _findings(), "f" * 64, 50000.0)
    ids1 = [f["finding_id"] for f in doc1["findings"]]
    ids2 = [f["finding_id"] for f in doc2["findings"]]
    assert ids1 == ids2                       # deterministic
    assert len(set(ids1)) == 3                # unique, even for identical content
    assert all(len(i) == 64 for i in ids1)    # sha256 hex


def test_finding_ids_are_signed(monkeypatch):
    store = _store(monkeypatch)
    doc = fr.build_completion_document("t1", _findings(), "f" * 64, 50000.0)
    stored = fr.sign_and_persist(doc)
    assert fr.verify_report(stored["record_hash"])["verified"] is True
    store[stored["record_hash"]]["findings"][0]["finding_id"] = "0" * 64  # tamper
    assert fr.verify_report(stored["record_hash"])["verified"] is False


# ── Task 3: queue module ─────────────────────────────────────────────

from counterfactual_service import exception_queue as eq  # noqa: E402


def _signed_report(monkeypatch, tmp_path):
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path))
    store = _store(monkeypatch)
    worm: list = []
    monkeypatch.setattr(eq, "audit_human_override",
                        lambda *a: worm.append(a))
    doc = fr.build_completion_document("t1", [
        {"pcaob_standard": "AS 2201", "risk_level": "Medium", "description": "unmatched",
         "evidence_payload": {"invoice": {"employee_name": "Ada", "po_number": "x"}},
         "requires_human_review": True},
        {"pcaob_standard": "AS 2305", "risk_level": "High", "description": "variance",
         "evidence_payload": {"entry_id": "L1"}, "requires_human_review": True},
        {"pcaob_standard": "AS 2110", "risk_level": "Low", "description": "info only",
         "evidence_payload": {}, "requires_human_review": False},
    ], "f" * 64, 50000.0)
    return store, worm, fr.sign_and_persist(doc)


def test_pending_lists_only_unreviewed_and_redacts(monkeypatch, tmp_path):
    _, _, report = _signed_report(monkeypatch, tmp_path)
    q = eq.pending_exceptions(report["record_hash"])
    assert q["n_pending"] == 2 and q["n_decided"] == 0
    assert {p["pcaob_standard"] for p in q["pending"]} == {"AS 2201", "AS 2305"}
    assert q["pending"][0]["evidence_payload"]["invoice"]["employee_name"] == "[REDACTED]"
    # Egress redaction must NOT leak back into the stored artifact.
    assert report["findings"][0]["evidence_payload"]["invoice"]["employee_name"] == "Ada"


def test_record_decision_signs_worm_logs_and_shrinks_queue(monkeypatch, tmp_path):
    _, worm, report = _signed_report(monkeypatch, tmp_path)
    fid = report["findings"][0]["finding_id"]
    decision = eq.record_decision(report["record_hash"], fid,
                                  "auditor-7", "manual review: legit invoice", approved=False)
    assert decision["document_type"] == "HumanOverrideRecord"
    assert decision["signature_status"] == "signed"
    assert fr.verify_report(decision["record_hash"])["verified"] is True
    assert worm == [(fid, "auditor-7", "manual review: legit invoice", False)]
    q = eq.pending_exceptions(report["record_hash"])
    assert q["n_pending"] == 1 and q["n_decided"] == 1
    assert fid not in {p["finding_id"] for p in q["pending"]}


def test_double_decision_conflict(monkeypatch, tmp_path):
    _, _, report = _signed_report(monkeypatch, tmp_path)
    fid = report["findings"][0]["finding_id"]
    eq.record_decision(report["record_hash"], fid, "a1", "ok", approved=True)
    with pytest.raises(eq.AlreadyDecidedError):
        eq.record_decision(report["record_hash"], fid, "a2", "again", approved=False)


def test_unknown_report_and_finding(monkeypatch, tmp_path):
    _, _, report = _signed_report(monkeypatch, tmp_path)
    with pytest.raises(LookupError):
        eq.pending_exceptions("0" * 64)
    with pytest.raises(LookupError):
        eq.record_decision(report["record_hash"], "0" * 64, "a", "r", approved=True)


def test_empty_rationale_rejected(monkeypatch, tmp_path):
    _, _, report = _signed_report(monkeypatch, tmp_path)
    fid = report["findings"][0]["finding_id"]
    with pytest.raises(ValueError):
        eq.record_decision(report["record_hash"], fid, "a", "   ", approved=True)


# ── Task 4: endpoints e2e ────────────────────────────────────────────


def test_exception_endpoints_e2e(monkeypatch, tmp_path):
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path))
    _store(monkeypatch)
    from fastapi import HTTPException

    import counterfactual_service.main as m

    report = asyncio.run(m.financial_audit(m.FinancialAuditRequest(
        tenant_id="t1",
        ledger=[{"internal_id": "L1", "account_code": "4000", "amount": 250000.0}],
        purchase_orders=[{"po_number": "PO-1"}],
        invoices=[{"invoice_number": "INV-9", "po_number": "PO-MISSING",
                   "employee_name": "Ada"}],
        journal_entries=[],
    )))
    rh = report["record_hash"]

    q = asyncio.run(m.financial_audit_exceptions(rh))
    assert q["n_pending"] == 2 and q["n_decided"] == 0
    assert "Ada" not in str(q["pending"])     # egress redaction

    fid = q["pending"][0]["finding_id"]
    body = m.ExceptionDecisionRequest(
        human_auditor_id="auditor-7", rationale="confirmed with vendor", approved=True)
    decision = asyncio.run(m.financial_audit_decide(rh, fid, body))
    assert decision["document_type"] == "HumanOverrideRecord"
    assert decision["verify_url"].endswith(decision["record_hash"])
    # The decision verifies through the same generic verify endpoint.
    assert asyncio.run(m.financial_audit_verify(decision["record_hash"]))["verified"] is True

    q2 = asyncio.run(m.financial_audit_exceptions(rh))
    assert q2["n_pending"] == 1 and q2["n_decided"] == 1

    with pytest.raises(HTTPException) as exc409:
        asyncio.run(m.financial_audit_decide(rh, fid, body))
    assert exc409.value.status_code == 409
    with pytest.raises(HTTPException) as exc404:
        asyncio.run(m.financial_audit_exceptions("0" * 64))
    assert exc404.value.status_code == 404
    with pytest.raises(HTTPException) as exc404b:
        asyncio.run(m.financial_audit_decide(rh, "0" * 64, body))
    assert exc404b.value.status_code == 404
