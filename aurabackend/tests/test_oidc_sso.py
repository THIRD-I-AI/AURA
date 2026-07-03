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
    with pytest.raises(AuthenticationError):
        await oidc_callback(code="abc", state="forged-state")


@pytest.mark.asyncio
async def test_callback_mints_aura_jwt_from_verified_claims(oidc_env, monkeypatch):
    from api_gateway.routers.auth import oidc_callback, oidc_login

    async def fake_exchange(code, verifier):
        assert code == "authcode-1" and verifier
        return {"id_token": "idtok"}

    async def fake_validate(id_token):
        assert id_token == "idtok"
        return {"sub": "idp|u1", "email": "ada@bank.example", "name": "Ada", "org_id": "bank-example"}

    monkeypatch.setattr(oidc, "exchange_code", fake_exchange)
    monkeypatch.setattr(oidc, "validate_id_token", fake_validate)

    login = await oidc_login()
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    resp = await oidc_callback(code="authcode-1", state=state)

    loc = resp.headers["location"]
    assert loc.startswith("http://localhost:5173/auth/sso#token=")
    token = loc.split("#token=", 1)[1]
    claims = pyjwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    assert claims["email"] == "ada@bank.example"
    assert claims["org_id"] == "bank-example"       # tenant from the IdP claim


@pytest.mark.asyncio
async def test_org_falls_back_to_email_domain(oidc_env, monkeypatch):
    from api_gateway.routers.auth import oidc_callback, oidc_login

    async def fake_exchange(code, verifier):
        return {"id_token": "t"}

    async def fake_validate(id_token):
        return {"sub": "idp|u2", "email": "rob@acme.io"}   # no org claim

    monkeypatch.setattr(oidc, "exchange_code", fake_exchange)
    monkeypatch.setattr(oidc, "validate_id_token", fake_validate)

    login = await oidc_login()
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    resp = await oidc_callback(code="c", state=state)
    token = resp.headers["location"].split("#token=", 1)[1]
    claims = pyjwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    assert claims["org_id"] == "acme.io"            # same company → same tenant
