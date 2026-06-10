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
