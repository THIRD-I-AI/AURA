"""
Auth Router — ``/auth``
========================
Token issuance, user registration, and current-user introspection.

Supports two modes (controlled by ``AURA_AUTH_MODE``):

- **open** (default): Issues a token for any ``user_id`` — for development
  and testing.  No credential validation.
- **password**: Requires ``email`` + ``password``.  Validates against the
  ``users`` table using bcrypt hashes.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Cookie, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from shared.auth import create_access_token, require_user
from shared.config import settings
from shared.exceptions import AuthenticationError, ConflictError, ValidationError
from shared.logging_config import get_logger

logger = get_logger("aura.router.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Request / Response schemas ──────────────────────────────────────────

class TokenRequest(BaseModel):
    """Login request.

    In **open** mode only ``user_id`` is required (email/password ignored).
    In **password** mode ``email`` + ``password`` are required.
    """
    user_id: str | None = None
    email: str | None = None
    password: str | None = None
    name: str | None = None
    role: str = "user"


class RegisterRequest(BaseModel):
    """Create a new user account (password mode)."""
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=8)
    name: str = Field(..., min_length=1)
    role: str = "user"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserInfo(BaseModel):
    sub: str
    email: str | None = None
    name: str | None = None
    role: str | None = None


# ── Endpoints ───────────────────────────────────────────────────────────

@router.post("/token", response_model=TokenResponse)
async def issue_token(body: TokenRequest):
    """Issue a signed JWT.

    Behaviour depends on ``AURA_AUTH_MODE``:
    - **open**: mints a token for any ``user_id`` (dev/demo).
    - **password**: validates ``email`` + ``password`` against the DB.
    """
    if settings.auth_mode == "password":
        return await _issue_token_password(body)
    return await _issue_token_open(body)


async def _issue_token_open(body: TokenRequest) -> TokenResponse:
    """Open mode — no credential validation."""
    if not body.user_id:
        raise ValidationError("user_id is required in open auth mode")

    logger.info("Token issued (open mode) for user_id=%s", body.user_id)
    # Dev/demo: the tenant is the user itself (single-user org).
    claims = {"sub": body.user_id, "role": body.role, "org_id": body.user_id}
    if body.email:
        claims["email"] = body.email
    if body.name:
        claims["name"] = body.name
    return TokenResponse(access_token=create_access_token(claims))


async def _issue_token_password(body: TokenRequest) -> TokenResponse:
    """Password mode — validate credentials against DB."""
    if not body.email or not body.password:
        raise ValidationError("email and password are required in password auth mode")

    from metadata_store.db import get_session_factory
    from metadata_store.models import User
    from shared.password import verify_password

    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(User).where(User.email == body.email)
        )
        user = result.scalar_one_or_none()

    if user is None or not user.password_hash:
        raise AuthenticationError("Invalid credentials")

    if not verify_password(body.password, user.password_hash):
        raise AuthenticationError("Invalid credentials")

    claims = {
        "sub": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role or "user",
        # Tenant boundary from the verified identity (Phase 1). Legacy rows
        # with no org_id fall back to their own id so they stay isolated.
        "org_id": user.org_id or user.id,
    }
    logger.info("Token issued (password mode) for email=%s", body.email)
    return TokenResponse(access_token=create_access_token(claims))


@router.post("/register", response_model=UserInfo, status_code=201)
async def register_user(body: RegisterRequest):
    """Create a new user with a hashed password.

    In production (``auth_mode=password``), this endpoint should be
    protected with ``require_role("admin")``.  In development it is open.
    """
    from metadata_store.db import get_session_factory
    from metadata_store.models import User
    from shared.password import hash_password

    session_factory = get_session_factory()
    async with session_factory() as session:
        # Check for existing email
        result = await session.execute(
            select(User).where(User.email == body.email)
        )
        if result.scalar_one_or_none() is not None:
            raise ConflictError(f"User with email '{body.email}' already exists")

        user = User(
            id=str(uuid.uuid4()),
            name=body.name,
            email=body.email,
            password_hash=hash_password(body.password),
            role=body.role,
            # New users get their own org (single-user tenant); org invites
            # that add members to an existing org come in a later phase.
            org_id=str(uuid.uuid4()),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    logger.info("User registered: email=%s id=%s", user.email, user.id)
    return UserInfo(
        sub=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
    )


# ── Enterprise SSO — generic OIDC (authorization-code + PKCE) ───────────
# One integration covers every standards-compliant IdP (Entra, Okta, Google,
# Ping, Auth0, Keycloak). See shared/oidc.py for the security posture.

@router.get("/oidc/status")
async def oidc_status():
    """Whether SSO is configured — the frontend shows/hides SSO buttons on this."""
    from shared import oidc
    return {"enabled": oidc.is_configured(), "issuer": settings.oidc_issuer or None}


class ExchangeRequest(BaseModel):
    """Redeem a single-use SSO handoff code for the AURA JWT."""
    code: str


@router.get("/oidc/login")
async def oidc_login():
    """Redirect the browser to the IdP's authorization endpoint (PKCE S256).
    The state is also bound to THIS browser via an HttpOnly cookie so a
    forged callback cannot ride another session (login-CSRF hardening)."""
    from fastapi.responses import RedirectResponse

    from shared import oidc
    if not oidc.is_configured():
        raise ValidationError("SSO is not configured on this deployment")
    url, state = await oidc.build_auth_url()
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie("aura_oidc_state", state, max_age=600, httponly=True, samesite="lax")
    return resp


@router.get("/oidc/callback")
async def oidc_callback(code: str, state: str,
                        oidc_state: str | None = Cookie(default=None, alias="aura_oidc_state")):
    """IdP redirects here. Verify state (single-use AND cookie-bound to this
    browser), exchange the code, signature-verify the id_token, then mint an
    AURA JWT whose org_id keys tenant isolation and the audit ledger.

    Security (ECC + commit-review findings): the JWT itself never appears in
    any URL — the browser receives a 60s single-use handoff CODE in the
    fragment and redeems it via POST /auth/oidc/exchange, so proxy/access
    logs of the Location header can never capture a live token. The redirect
    destination must be an absolute http(s) URL from deployment config."""
    from fastapi.responses import RedirectResponse

    from shared import oidc
    if not oidc.is_configured():
        raise ValidationError("SSO is not configured on this deployment")
    dest = settings.oidc_post_login_redirect or ""
    if not (dest.startswith("http://") or dest.startswith("https://")):
        raise ValidationError("AURA_OIDC_POST_LOGIN_REDIRECT must be an absolute http(s) URL")
    if oidc_state != state:
        raise AuthenticationError("SSO state does not match this browser — restart sign-in")
    verifier = oidc.pop_state(state)
    if verifier is None:
        raise AuthenticationError("Unknown or expired SSO state — restart sign-in")

    tokens = await oidc.exchange_code(code, verifier)
    id_token = tokens.get("id_token")
    if not id_token:
        raise AuthenticationError("IdP response carried no id_token")
    claims = await oidc.validate_id_token(id_token)

    aura_claims = {
        "sub": str(claims.get("sub")),
        "role": "user",
        "org_id": oidc.map_org(claims),   # fail-closed tenant mapping
    }
    if claims.get("email"):
        aura_claims["email"] = claims["email"]
    if claims.get("name"):
        aura_claims["name"] = claims["name"]
    logger.info("Token issued (oidc) for sub=%s org=%s", aura_claims["sub"], aura_claims["org_id"])
    handoff = oidc.new_handoff(create_access_token(aura_claims))
    resp = RedirectResponse(f"{dest}#code={handoff}", status_code=302)
    resp.delete_cookie("aura_oidc_state")
    return resp


@router.post("/oidc/exchange", response_model=TokenResponse)
async def oidc_exchange(body: ExchangeRequest):
    """Redeem the single-use handoff code for the AURA JWT (response body —
    tokens never transit URLs or logs)."""
    from shared import oidc
    token = oidc.pop_handoff(body.code)
    if token is None:
        raise AuthenticationError("Unknown or expired SSO code — restart sign-in")
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserInfo)
async def current_user(user: dict = Depends(require_user)):
    """Return the claims of the authenticated user."""
    return UserInfo(
        sub=user["sub"],
        email=user.get("email"),
        name=user.get("name"),
        role=user.get("role"),
    )
