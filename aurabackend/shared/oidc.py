"""Generic OIDC client (authorization-code + PKCE) for enterprise SSO.

One standards-compliant integration instead of per-IdP code: Entra ID, Okta,
Google Workspace, Ping, Auth0, Keycloak all speak this. Configuration is
entirely env-driven (see shared/config.py):

    AURA_OIDC_ISSUER          e.g. https://login.microsoftonline.com/<tenant>/v2.0
    AURA_OIDC_CLIENT_ID
    AURA_OIDC_CLIENT_SECRET
    AURA_OIDC_REDIRECT_URI    gateway /auth/oidc/callback URL
    AURA_OIDC_ORG_CLAIM       claim carrying the tenant (default org_id; falls
                              back to tid/hd, then the email domain)
    AURA_OIDC_POST_LOGIN_REDIRECT  frontend handoff page

Security posture: state is random, single-use, TTL-bound; PKCE S256 always;
the id_token signature is verified against the issuer's JWKS and iss/aud are
enforced before any AURA JWT is minted (fail-closed).

Known limitation (documented): the state store is in-process, so the login
and callback must hit the same replica. Multi-replica deployments need the
Postgres-backed store — tracked as an enterprise follow-up.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

from shared.config import settings

STATE_TTL_SECONDS = 600
_state_store: Dict[str, Tuple[str, float]] = {}
_discovery_cache: Optional[Dict[str, Any]] = None


def is_configured() -> bool:
    return bool(
        getattr(settings, "oidc_issuer", "")
        and getattr(settings, "oidc_client_id", "")
        and getattr(settings, "oidc_redirect_uri", "")
    )


def new_state() -> Tuple[str, str, str]:
    """Returns (state, code_verifier, code_challenge) and stores the pair."""
    now = time.time()
    # opportunistic TTL sweep so the store cannot grow unbounded
    for k in [k for k, (_, exp) in _state_store.items() if exp < now]:
        _state_store.pop(k, None)
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    _state_store[state] = (verifier, now + STATE_TTL_SECONDS)
    return state, verifier, challenge


def pop_state(state: str) -> Optional[str]:
    """Single-use retrieval of the PKCE verifier for a state; None if unknown/expired."""
    entry = _state_store.pop(state, None)
    if entry is None:
        return None
    verifier, exp = entry
    return verifier if exp >= time.time() else None


async def discover() -> Dict[str, Any]:
    """Fetch (and cache) the issuer's OIDC discovery document."""
    global _discovery_cache
    if _discovery_cache is None:
        import httpx
        url = settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            _discovery_cache = resp.json()
    return _discovery_cache


async def build_auth_url() -> str:
    doc = await discover()
    state, _verifier, challenge = new_state()
    params = {
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{doc['authorization_endpoint']}?{urlencode(params)}"


async def exchange_code(code: str, verifier: str) -> Dict[str, Any]:
    """Exchange the authorization code for tokens at the IdP."""
    import httpx
    doc = await discover()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oidc_redirect_uri,
        "client_id": settings.oidc_client_id,
        "client_secret": getattr(settings, "oidc_client_secret", ""),
        "code_verifier": verifier,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(doc["token_endpoint"], data=data)
        resp.raise_for_status()
        return resp.json()


async def validate_id_token(id_token: str) -> Dict[str, Any]:
    """Verify the id_token signature against the issuer JWKS; enforce iss/aud."""
    import jwt as pyjwt
    doc = await discover()
    jwk_client = pyjwt.PyJWKClient(doc["jwks_uri"])
    signing_key = jwk_client.get_signing_key_from_jwt(id_token)
    return pyjwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256", "ES256"],
        audience=settings.oidc_client_id,
        issuer=settings.oidc_issuer,
    )


def map_org(claims: Dict[str, Any]) -> str:
    """Tenant mapping: configured claim → tid/hd → email domain → sub.

    Same-company users land in the same org so collaboration and the
    per-tenant audit ledger work out of the box.
    """
    configured = getattr(settings, "oidc_org_claim", "org_id") or "org_id"
    for key in (configured, "tid", "hd"):
        v = claims.get(key)
        if v:
            return str(v)
    email = claims.get("email") or ""
    if "@" in email:
        return email.split("@", 1)[1].lower()
    return str(claims.get("sub", "default"))
