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
HANDOFF_TTL_SECONDS = 60
DISCOVERY_TTL_SECONDS = 3600
_state_store: Dict[str, Tuple[str, float]] = {}
_handoff_store: Dict[str, Tuple[str, float]] = {}
_discovery_cache: Optional[Tuple[Dict[str, Any], float]] = None
_jwk_client: Optional[Any] = None


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


def new_handoff(token: str) -> str:
    """Store a minted AURA JWT behind a short-lived single-use code so the
    JWT itself never appears in any URL (redirect Location headers can be
    logged by proxies — ECC security-review finding)."""
    now = time.time()
    for k in [k for k, (_, exp) in _handoff_store.items() if exp < now]:
        _handoff_store.pop(k, None)
    code = secrets.token_urlsafe(32)
    _handoff_store[code] = (token, now + HANDOFF_TTL_SECONDS)
    return code


def pop_handoff(code: str) -> Optional[str]:
    entry = _handoff_store.pop(code, None)
    if entry is None:
        return None
    token, exp = entry
    return token if exp >= time.time() else None


async def discover() -> Dict[str, Any]:
    """Fetch the issuer's OIDC discovery document; cached with a TTL so IdP
    key/endpoint rotation is picked up without a process restart."""
    global _discovery_cache
    now = time.time()
    if _discovery_cache is None or _discovery_cache[1] < now:
        import httpx
        url = settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            _discovery_cache = (resp.json(), now + DISCOVERY_TTL_SECONDS)
    return _discovery_cache[0]


async def build_auth_url() -> Tuple[str, str]:
    """Returns (authorization_url, state) — the caller binds state to the
    browser via a cookie so a forged callback cannot ride another session
    (login-CSRF / session-fixation hardening)."""
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
    return f"{doc['authorization_endpoint']}?{urlencode(params)}", state


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
    """Verify the id_token signature against the issuer JWKS; enforce iss/aud.

    The blocking JWKS fetch runs off the event loop (to_thread) with a
    timeout, and the client caches the key set with a short lifespan so
    routine IdP key rotation is picked up without a restart and a slow IdP
    cannot stall concurrent requests (ECC review findings).
    """
    import asyncio

    import jwt as pyjwt
    global _jwk_client
    doc = await discover()
    if _jwk_client is None or getattr(_jwk_client, "uri", None) != doc["jwks_uri"]:
        _jwk_client = pyjwt.PyJWKClient(
            doc["jwks_uri"], cache_jwk_set=True, lifespan=300, timeout=10)
    signing_key = await asyncio.to_thread(_jwk_client.get_signing_key_from_jwt, id_token)
    return await asyncio.to_thread(
        lambda: pyjwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=settings.oidc_client_id,
            issuer=settings.oidc_issuer,
        ))


def map_org(claims: Dict[str, Any]) -> str:
    """Tenant mapping: configured claim → tid/hd → VERIFIED-email domain.

    Fail-closed (tenant-impersonation hardening): with no org claim and no
    ``email_verified: true``, the login is refused rather than letting a
    self-asserted email place the user inside another company's tenant.
    Operators of shared/multi-tenant IdPs should set AURA_OIDC_ORG_CLAIM.
    """
    from shared.exceptions import AuthenticationError
    configured = getattr(settings, "oidc_org_claim", "org_id") or "org_id"
    for key in (configured, "tid", "hd"):
        v = claims.get(key)
        if v:
            return str(v)
    if claims.get("email_verified") is True:
        email = claims.get("email") or ""
        if "@" in email:
            return email.split("@", 1)[1].lower()
    raise AuthenticationError(
        "IdP returned no tenant identifier — an org claim or a verified email domain is required")
