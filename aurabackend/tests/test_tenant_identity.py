"""SaaS Phase 1 — the tenant is derived from the verified JWT, never a header.

These guard the isolation primitive (`require_tenant`): a token holder must not
be able to act as another org by forging a header, and legacy tokens minted
before `org_id` existed must still map to a stable per-subject tenant.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.auth import create_access_token, decode_access_token, require_tenant


def test_require_tenant_returns_org_id_from_token():
    user = {"sub": "user-1", "org_id": "org-abc", "role": "user"}
    assert asyncio.run(require_tenant(user=user)) == "org-abc"


def test_require_tenant_falls_back_to_subject_for_legacy_tokens():
    # A token minted before org_id existed carries no org_id; the caller must
    # still get a stable tenant (their own subject) rather than None.
    user = {"sub": "user-1", "role": "user"}
    assert asyncio.run(require_tenant(user=user)) == "user-1"


def test_require_tenant_ignores_any_workspace_header_key():
    # Even if a forged claim/header-like key is present, only org_id (or sub)
    # is honoured — never an arbitrary workspace field.
    user = {"sub": "user-1", "org_id": "org-abc", "workspace_id": "org-victim"}
    assert asyncio.run(require_tenant(user=user)) == "org-abc"


def test_org_id_roundtrips_through_the_signed_token():
    token = create_access_token({"sub": "user-1", "org_id": "org-abc"})
    claims = decode_access_token(token)
    assert claims["org_id"] == "org-abc"
    assert claims["sub"] == "user-1"
