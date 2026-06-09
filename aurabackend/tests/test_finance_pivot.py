"""Validation tests for the AI-native finance-auditor pivot hardening:
- ED25519 soft-revocation now actually blocks NEW signatures (was cosmetic).
- JWKS reflects revoked keys; signatures verify against the published key.
- Perimeter PII redaction; AS-2401 duplicate-payment detection (was a dead set).
- The key-revocation endpoint's admin gate.
The ERP circular-import fix is covered by tests_contract/test_erp_contracts.py.
"""
import asyncio
import base64
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives import serialization

from agents.specialists.financial_auditor import FinancialAuditorAgent
from counterfactual_service import cryptography as crypto
from shared.pii_masking import redact_pii


def test_revoked_key_cannot_sign():
    kid = "agent-revoke-test"
    crypto.generate_agent_keypair(kid)
    assert crypto.sign_payload(kid, {"x": 1})            # signs before revocation
    crypto.soft_revoke_key(kid)
    with pytest.raises(ValueError):
        crypto.sign_payload(kid, {"x": 2})               # blocked after revocation


def test_jwks_marks_revoked_key():
    kid = "agent-jwks-test"
    crypto.generate_agent_keypair(kid)
    crypto.soft_revoke_key(kid)
    entry = next(k for k in crypto.get_jwks()["keys"] if k["kid"] == kid)
    assert entry["revoked"] is True
    assert entry["kty"] == "OKP" and entry["crv"] == "Ed25519"


def test_signature_verifies_against_published_key():
    kid = "agent-verify-test"
    _, pub_pem = crypto.generate_agent_keypair(kid)
    payload = {"a": 1, "b": 2}
    sig = base64.b64decode(crypto.sign_payload(kid, payload))
    pub = serialization.load_pem_public_key(pub_pem.encode())
    msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    pub.verify(sig, msg)  # raises on a bad signature; returns None on success


def test_redact_pii_masks_known_keys_recursively():
    data = {"ssn": "123-45-6789", "amount": 100,
            "nested": {"email": "x@y.com", "account": "4000"},
            "rows": [{"first_name": "Ada", "id": 1}]}
    out = redact_pii(data)
    assert out["ssn"] == "[REDACTED]"
    assert out["amount"] == 100                       # non-PII preserved
    assert out["nested"]["email"] == "[REDACTED]"     # nested PII masked
    assert out["nested"]["account"] == "4000"
    assert out["rows"][0]["first_name"] == "[REDACTED]"  # list-of-dict masked
    assert out["rows"][0]["id"] == 1


def test_as2401_detects_duplicate_and_round_dollar(monkeypatch):
    import agents.specialists.financial_auditor as fa
    monkeypatch.setattr(fa, "audit_event", lambda *a, **k: None)
    agent = fa.FinancialAuditorAgent(tenant_id="t1")
    jes = [
        {"internal_id": "1", "amount": 5000.0, "account_code": "6000", "vendor_id": "V1"},
        {"internal_id": "2", "amount": 5000.0, "account_code": "6000", "vendor_id": "V1"},  # duplicate
        {"internal_id": "3", "amount": 1234.50, "account_code": "6000", "vendor_id": "V2"},  # clean
    ]
    findings = asyncio.run(agent.execute_as2401_fraud_detection(jes))
    descs = [f.description.lower() for f in findings]
    assert any("duplicate" in d for d in descs)
    assert any("round-dollar" in d for d in descs)
    assert all(f.evidence_payload.get("je_id") != "3" for f in findings)  # clean entry unflagged


def test_revoke_key_requires_admin_role():
    from shared.exceptions import ForbiddenError

    from counterfactual_service.main import _require_admin
    with pytest.raises(ForbiddenError):
        asyncio.run(_require_admin(user={"role": "user"}))
    assert asyncio.run(_require_admin(user={"role": "admin"}))["role"] == "admin"
