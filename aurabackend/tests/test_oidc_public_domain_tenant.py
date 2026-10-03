"""BUG-306: with no org claim, the OIDC tenant was the verified email's domain -- so
unrelated people with gmail.com (or any consumer mailbox) addresses shared a tenant."""
from __future__ import annotations

import pytest

from shared.exceptions import AuthenticationError
from shared.oidc import map_org

ISS = "https://accounts.google.com"


def _claims(email, sub, **extra):
    return {"iss": ISS, "sub": sub, "email": email, "email_verified": True, **extra}


def test_two_users_of_a_public_mailbox_do_not_share_a_tenant():
    alice = map_org(_claims("alice@gmail.com", "sub-alice"))
    mallory = map_org(_claims("mallory@gmail.com", "sub-mallory"))

    assert alice != mallory
    assert "gmail" not in alice and "gmail" not in mallory


def test_a_personal_tenant_is_stable_across_logins_and_safe_as_a_directory_name():
    first = map_org(_claims("alice@gmail.com", "sub-alice"))

    assert map_org(_claims("Alice@GMAIL.com", "sub-alice")) == first
    assert first.startswith("personal-") and first.replace("-", "").isalnum()


def test_the_same_subject_at_another_issuer_is_another_tenant():
    here = map_org(_claims("alice@outlook.com", "42"))
    there = map_org({**_claims("alice@outlook.com", "42"), "iss": "https://other-idp.example"})

    assert here != there


def test_a_company_domain_is_still_the_tenant():
    assert map_org(_claims("ada@bank.example", "s1")) == "bank.example"
    assert map_org(_claims("bob@bank.example", "s2")) == "bank.example"


def test_an_org_claim_takes_precedence_over_a_public_domain():
    assert map_org(_claims("alice@gmail.com", "s1", org_id="acme")) == "acme"
    assert map_org(_claims("alice@gmail.com", "s1", hd="acme.com")) == "acme.com"


def test_an_unverified_email_is_still_refused():
    with pytest.raises(AuthenticationError):
        map_org({"iss": ISS, "sub": "s", "email": "alice@gmail.com", "email_verified": False})
