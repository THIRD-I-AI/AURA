"""
BUG-027 regression test — main.py's get_sth() (Signed Tree Head) must refuse
to sign with a revoked key, mirroring financial_report.py's _sign_document
(the signing path that already got this right) and engine.py's run_job()
(fixed alongside this one; see test_counterfactual_sth_revocation's sibling,
test_run_job_refuses_to_sign_with_a_revoked_key in
test_counterfactual_sprint9.py).
"""
from __future__ import annotations

import pytest

from counterfactual_service.main import get_sth


@pytest.mark.asyncio
async def test_get_sth_refuses_to_sign_with_a_revoked_key(monkeypatch):
    fake_merkle_info = {
        "tree_size": 3,
        "root_hash_hex": "a" * 64,
        "day": "20260902",
        "service_tag": "counterfactual_service",
    }
    monkeypatch.setattr(
        "shared.audit_log.daily_merkle_root", lambda day, service_tag=None: fake_merkle_info
    )

    from counterfactual_service import main as main_module
    monkeypatch.setattr(main_module.cryptography, "is_revoked", lambda kid=None: True)

    resp = await get_sth(day="20260902")

    assert resp.signature_status == "unsigned"
    assert resp.signature_b64 is None
    assert resp.signing_key_source is None
    # the root hash itself is still correct/attestable-in-content even though
    # it isn't cryptographically signed — matches get_sth's own docstring
    assert resp.root_hash_hex == "a" * 64


@pytest.mark.asyncio
async def test_get_sth_signs_when_key_is_not_revoked(monkeypatch, tmp_path):
    fake_merkle_info = {
        "tree_size": 3,
        "root_hash_hex": "b" * 64,
        "day": "20260902",
        "service_tag": "counterfactual_service",
    }
    monkeypatch.setattr(
        "shared.audit_log.daily_merkle_root", lambda day, service_tag=None: fake_merkle_info
    )
    monkeypatch.setenv("AURA_SIGNING_PRIVATE_KEY_HEX", bytes(range(32)).hex())

    from counterfactual_service import main as main_module
    monkeypatch.setattr(main_module.cryptography, "is_revoked", lambda kid=None: False)

    resp = await get_sth(day="20260902")

    assert resp.signature_status == "signed"
    assert resp.signature_b64 is not None
