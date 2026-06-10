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
