"""BUG-304: the production guard rejected only the exact string "open", while the token
endpoint treated every value except the exact string "password" as open mode. A
mistyped or unsupported AURA_AUTH_MODE booted in production and minted admin tokens for
any user_id with no credential check."""
from __future__ import annotations

import asyncio

import pytest

from api_gateway.routers import auth as auth_router
from shared.config import AuraSettings
from shared.exceptions import ServiceUnavailableError

_PROD = dict(
    _env_file=None, ENVIRONMENT="production", SECRET_KEY="real-secret",
    CORS_ALLOWED_ORIGINS="https://app.example.com", AURA_JWT_ENABLED="true",
    AURA_AUDIT_ENABLED="true",
)


@pytest.mark.parametrize("value", ["oidc", "sso", "none", "", "passwd", "open-ish"])
def test_an_unknown_auth_mode_is_rejected_everywhere(value):
    with pytest.raises(ValueError, match="AURA_AUTH_MODE must be"):
        AuraSettings(_env_file=None, AURA_AUTH_MODE=value)
    with pytest.raises(ValueError):
        AuraSettings(**_PROD, AURA_AUTH_MODE=value)


@pytest.mark.parametrize("value", ["Password", "PASSWORD", " password ", "password\n"])
def test_a_miscased_or_padded_password_mode_is_password_mode(value):
    assert AuraSettings(_env_file=None, AURA_AUTH_MODE=value).auth_mode == "password"


@pytest.mark.parametrize("value", ["OPEN", " Open "])
def test_a_miscased_open_mode_is_still_refused_in_production(value):
    with pytest.raises(ValueError, match="not allowed in production"):
        AuraSettings(**_PROD, AURA_AUTH_MODE=value)
    assert AuraSettings(_env_file=None, AURA_AUTH_MODE=value).auth_mode == "open"


def test_the_token_endpoint_never_falls_back_to_open_minting(monkeypatch):
    """Defence in depth: even if settings held an unrecognised mode, no token is minted."""
    monkeypatch.setattr(auth_router.settings, "auth_mode", "oidc")
    body = auth_router.TokenRequest(user_id="victim-org", role="admin")

    with pytest.raises(ServiceUnavailableError) as exc:
        asyncio.run(auth_router.issue_token(body))

    assert exc.value.status_code == 503
