"""S34d — deterministic HMAC-keyed PII tokens at egress."""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import pii_masking as pm

TOKEN_RE = re.compile(r"^PII-[0-9a-f]{12}$")


@pytest.fixture()
def keyed(monkeypatch):
    monkeypatch.setenv("AURA_PII_TOKEN_KEY", "test-secret-key")


@pytest.fixture()
def unkeyed(monkeypatch):
    monkeypatch.delenv("AURA_PII_TOKEN_KEY", raising=False)


# ── Task 1: tokenizer ────────────────────────────────────────────────


def test_token_deterministic_and_salted(keyed):
    a = pm.tokenize_pii({"employee_name": "Ada"}, context="t1")
    b = pm.tokenize_pii({"employee_name": "Ada"}, context="t1")
    assert a == b                                              # deterministic
    assert TOKEN_RE.match(a["employee_name"])
    assert "Ada" not in str(a)
    # value-, tenant-, and field-salting all produce distinct tokens
    other_value = pm.tokenize_pii({"employee_name": "Bob"}, context="t1")
    other_tenant = pm.tokenize_pii({"employee_name": "Ada"}, context="t2")
    other_field = pm.tokenize_pii({"email": "Ada"}, context="t1")
    tokens = {a["employee_name"], other_value["employee_name"],
              other_tenant["employee_name"], other_field["email"]}
    assert len(tokens) == 4


def test_tokenize_recurses_and_preserves_non_pii(keyed):
    data = {"findings": [{"employee_name": "Ada", "amount": 5000.0,
                          "nested": {"ssn": "123-45-6789"}}]}
    out = pm.tokenize_pii(data, context="t1")
    assert TOKEN_RE.match(out["findings"][0]["employee_name"])
    assert TOKEN_RE.match(out["findings"][0]["nested"]["ssn"])
    assert out["findings"][0]["amount"] == 5000.0


def test_non_string_pii_value_tokenized(keyed):
    out = pm.tokenize_pii({"phone": 5551234567}, context="t1")
    assert TOKEN_RE.match(out["phone"])


def test_mask_pii_egress_unkeyed_falls_back_to_redaction(unkeyed):
    out = pm.mask_pii_egress({"employee_name": "Ada"}, context="t1")
    assert out["employee_name"] == "[REDACTED]"


def test_mask_pii_egress_keyed_tokenizes(keyed):
    out = pm.mask_pii_egress({"employee_name": "Ada"}, context="t1")
    assert TOKEN_RE.match(out["employee_name"])


# ── Task 2: egress call sites correlate the same entity ──────────────


def _signed_report_with_shared_employee(monkeypatch, tmp_path):
    from counterfactual_service import financial_report as fr

    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path))
    store = {}
    monkeypatch.setattr(fr.persistence, "write_artifact", lambda h, p: store.__setitem__(h, p) or h)
    monkeypatch.setattr(fr.persistence, "read_artifact", lambda h: store.get(h))
    monkeypatch.setattr(fr, "audit_event", lambda *a, **k: None)
    doc = fr.build_completion_document("t1", [
        {"pcaob_standard": "AS 2201", "risk_level": "Medium", "description": "inv A",
         "evidence_payload": {"employee_name": "Ada"}, "requires_human_review": True},
        {"pcaob_standard": "AS 2401", "risk_level": "High", "description": "inv B",
         "evidence_payload": {"employee_name": "Ada"}, "requires_human_review": True},
    ], "f" * 64, 50000.0)
    return fr, fr.sign_and_persist(doc)


def test_client_view_and_queue_tokens_correlate(keyed, monkeypatch, tmp_path):
    from counterfactual_service import exception_queue as eq

    fr, stored = _signed_report_with_shared_employee(monkeypatch, tmp_path)
    view = fr.client_view(stored)
    t1 = view["findings"][0]["evidence_payload"]["employee_name"]
    t2 = view["findings"][1]["evidence_payload"]["employee_name"]
    assert TOKEN_RE.match(t1) and t1 == t2      # same entity → same token
    # The exception queue emits the SAME token for the same entity.
    q = eq.pending_exceptions(stored["record_hash"])
    assert q["pending"][0]["evidence_payload"]["employee_name"] == t1
    # Raw evidence stays in the signed artifact, which still verifies.
    assert stored["findings"][0]["evidence_payload"]["employee_name"] == "Ada"
    assert fr.verify_report(stored["record_hash"])["verified"] is True


def test_egress_unkeyed_still_redacts(unkeyed, monkeypatch, tmp_path):
    fr, stored = _signed_report_with_shared_employee(monkeypatch, tmp_path)
    view = fr.client_view(stored)
    assert view["findings"][0]["evidence_payload"]["employee_name"] == "[REDACTED]"
