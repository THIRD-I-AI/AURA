"""Enterprise SSO — generic OIDC (authorization-code + PKCE).

One standards-compliant integration covers Entra ID / Okta / Google / Ping /
Auth0 / Keycloak — the user's "login with anything their company provides".
Fail-closed: endpoints refuse when OIDC is unconfigured; state is single-use
with TTL; the id_token is signature-verified before any AURA JWT is minted.
"""
from __future__ import annotations

import os
import sys
from urllib.parse import parse_qs, urlparse

import jwt as pyjwt
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import oidc
from shared.config import settings


@pytest.fixture
def oidc_env(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "oidc_issuer", "https://idp.example.com", raising=False)
    monkeypatch.setattr(settings, "oidc_client_id", "aura-client", raising=False)
    monkeypatch.setattr(settings, "oidc_client_secret", "s3cret", raising=False)
    monkeypatch.setattr(settings, "oidc_redirect_uri", "http://localhost:8000/api/v1/auth/oidc/callback", raising=False)
    monkeypatch.setattr(settings, "oidc_post_login_redirect", "http://localhost:5173/auth/sso", raising=False)
    # metadata store on tmp sqlite so user upserts do not touch dev data
    monkeypatch.setenv("AURA_METADATA_DB", f"sqlite+aiosqlite:///{tmp_path/'md.db'}")
    oidc._discovery_cache = None
    oidc._state_store.clear()
    oidc._handoff_store.clear()
    monkeypatch.setattr(oidc, "discover", _fake_discover)
    yield


async def _fake_discover():
    return {
        "authorization_endpoint": "https://idp.example.com/authorize",
        "token_endpoint": "https://idp.example.com/token",
        "jwks_uri": "https://idp.example.com/jwks",
    }


def test_unconfigured_reports_disabled(monkeypatch):
    monkeypatch.setattr(settings, "oidc_issuer", "", raising=False)
    assert oidc.is_configured() is False


@pytest.mark.asyncio
async def test_status_endpoint(oidc_env):
    from api_gateway.routers.auth import oidc_status
    assert (await oidc_status())["enabled"] is True


@pytest.mark.asyncio
async def test_login_redirects_with_pkce_and_single_use_state(oidc_env):
    from api_gateway.routers.auth import oidc_login
    resp = await oidc_login()
    q = parse_qs(urlparse(resp.headers["location"]).query)
    assert q["client_id"] == ["aura-client"]
    assert q["response_type"] == ["code"]
    assert q["code_challenge_method"] == ["S256"]
    assert "openid" in q["scope"][0]
    state = q["state"][0]
    assert oidc.pop_state(state) is not None      # stored…
    assert oidc.pop_state(state) is None          # …and single-use


@pytest.mark.asyncio
async def test_callback_rejects_unknown_state(oidc_env):
    from api_gateway.routers.auth import oidc_callback
    from shared.exceptions import AuthenticationError
    # forged state with no browser cookie → refused (CSRF binding)
    with pytest.raises(AuthenticationError):
        await oidc_callback(code="abc", state="forged-state")
    # even a cookie-matching but never-issued state → refused (single-use store)
    with pytest.raises(AuthenticationError):
        await oidc_callback(code="abc", state="forged-state", oidc_state="forged-state")


async def _sso_roundtrip(monkeypatch, idp_claims):
    """login → callback → fragment handoff CODE → exchange → AURA JWT.
    The JWT itself never appears in any URL (ECC security review finding)."""
    from api_gateway.routers.auth import ExchangeRequest, oidc_callback, oidc_exchange, oidc_login

    async def fake_exchange(code, verifier):
        assert verifier
        return {"id_token": "idtok"}

    async def fake_validate(id_token):
        return idp_claims

    monkeypatch.setattr(oidc, "exchange_code", fake_exchange)
    monkeypatch.setattr(oidc, "validate_id_token", fake_validate)

    login = await oidc_login()
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    resp = await oidc_callback(code="authcode-1", state=state, oidc_state=state)

    loc = resp.headers["location"]
    assert loc.startswith("http://localhost:5173/auth/sso#code=")
    assert "token=" not in loc                      # the JWT never rides in a URL
    handoff = loc.split("#code=", 1)[1]
    token = (await oidc_exchange(ExchangeRequest(code=handoff))).access_token
    return handoff, pyjwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])


@pytest.mark.asyncio
async def test_callback_hands_off_code_then_exchange_mints_jwt(oidc_env, monkeypatch):
    handoff, claims = await _sso_roundtrip(
        monkeypatch, {"sub": "idp|u1", "email": "ada@bank.example", "name": "Ada", "org_id": "bank-example"})
    assert claims["email"] == "ada@bank.example"
    assert claims["org_id"] == "bank-example"       # tenant from the IdP claim

    # the handoff code is single-use
    from api_gateway.routers.auth import ExchangeRequest, oidc_exchange
    from shared.exceptions import AuthenticationError
    with pytest.raises(AuthenticationError):
        await oidc_exchange(ExchangeRequest(code=handoff))


@pytest.mark.asyncio
async def test_org_fallback_requires_verified_email(oidc_env, monkeypatch):
    _, claims = await _sso_roundtrip(
        monkeypatch, {"sub": "idp|u2", "email": "rob@acme.io", "email_verified": True})
    assert claims["org_id"] == "acme.io"            # same company → same tenant


@pytest.mark.asyncio
async def test_unverified_email_cannot_choose_tenant(oidc_env, monkeypatch):
    """Tenant impersonation hardening: without an org claim AND without a
    VERIFIED email, we fail closed rather than trust a self-asserted domain."""
    from shared.exceptions import AuthenticationError
    with pytest.raises(AuthenticationError):
        await _sso_roundtrip(monkeypatch, {"sub": "idp|u3", "email": "fake@bank.example"})


@pytest.mark.asyncio
async def test_callback_refuses_misconfigured_post_login_redirect(oidc_env, monkeypatch):
    """Open-redirect hardening: the handoff destination must be an absolute
    http(s) URL configured by the deployment, or the callback refuses."""
    from api_gateway.routers.auth import oidc_callback, oidc_login
    from shared.exceptions import ValidationError

    async def fake_exchange(code, verifier):
        return {"id_token": "t"}

    async def fake_validate(id_token):
        return {"sub": "u", "email": "a@b.co", "email_verified": True, "org_id": "b-co"}

    monkeypatch.setattr(oidc, "exchange_code", fake_exchange)
    monkeypatch.setattr(oidc, "validate_id_token", fake_validate)
    monkeypatch.setattr(settings, "oidc_post_login_redirect", "javascript:alert(1)", raising=False)

    login = await oidc_login()
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    with pytest.raises(ValidationError):
        await oidc_callback(code="c", state=state, oidc_state=state)
