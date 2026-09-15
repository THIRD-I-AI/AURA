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

import asyncio
import secrets
import uuid

from fastapi import APIRouter, Cookie, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from shared.auth import create_access_token, require_user
from shared.config import settings
from shared.exceptions import (
    AuthenticationError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from shared.logging_config import get_logger
from shared.password import hash_password

logger = get_logger("aura.router.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

# BUG-059: a fixed bcrypt hash of a random, unknown value, computed once
# at import time -- used to run a real (equal-cost) bcrypt comparison for
# a nonexistent email or a user with no password_hash, so response
# latency never reveals which case caused the rejection (a "user not
# found" path that skips bcrypt entirely is measurably faster than one
# that runs it, letting an attacker enumerate valid emails by timing).
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_hex(32))


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


class UpdateProfileRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class UpdateProfileResponse(BaseModel):
    """The claims changed (name), so the caller gets a freshly-signed token
    carrying them — otherwise the browser's existing JWT would keep showing
    the stale name until it expired."""
    user: UserInfo
    access_token: str
    token_type: str = "bearer"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


class DeleteAccountRequest(BaseModel):
    password: str


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
        # BUG-059: run the same-cost bcrypt comparison a real user would
        # trigger, so this path takes as long as a wrong-password
        # rejection below -- the response time must not distinguish
        # "no such email" from "wrong password".
        await asyncio.to_thread(verify_password, body.password, _DUMMY_PASSWORD_HASH)
        raise AuthenticationError("Invalid credentials")

    if not await asyncio.to_thread(verify_password, body.password, user.password_hash):
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
    if settings.is_production and not settings.allow_self_registration:
        raise ForbiddenError(
            "Self-registration is disabled on this deployment. "
            "Set AURA_ALLOW_SELF_REGISTRATION=true to re-enable it, or "
            "provision accounts via SSO / an administrator."
        )

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

        password_hash = await asyncio.to_thread(hash_password, body.password)
        user = User(
            id=str(uuid.uuid4()),
            name=body.name,
            email=body.email,
            password_hash=password_hash,
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


async def _load_own_user_row(session, user: dict):
    """Fetch the DB row backing the caller's JWT ``sub``. Only exists in
    password mode — an open-mode token is a bare claims bundle with no
    persisted account, so profile edits and password changes have nothing
    to write to."""
    if settings.auth_mode != "password":
        raise ValidationError(
            "Profile editing requires password auth mode — this session has no persisted account"
        )

    from metadata_store.models import User

    result = await session.execute(select(User).where(User.id == user["sub"]))
    db_user = result.scalar_one_or_none()
    if db_user is None:
        raise NotFoundError("User", user["sub"])
    return db_user


@router.patch("/me", response_model=UpdateProfileResponse)
async def update_profile(body: UpdateProfileRequest, user: dict = Depends(require_user)):
    """Update the caller's display name and reissue a token carrying it."""
    from metadata_store.db import get_session_factory

    session_factory = get_session_factory()
    async with session_factory() as session:
        db_user = await _load_own_user_row(session, user)
        db_user.name = body.name
        await session.commit()
        await session.refresh(db_user)

    claims = {
        "sub": db_user.id,
        "email": db_user.email,
        "name": db_user.name,
        "role": db_user.role or "user",
        "org_id": db_user.org_id or db_user.id,
    }
    logger.info("Profile updated for user_id=%s", db_user.id)
    return UpdateProfileResponse(
        user=UserInfo(sub=db_user.id, email=db_user.email, name=db_user.name, role=db_user.role),
        access_token=create_access_token(claims),
    )


@router.post("/change-password", status_code=204)
async def change_password(body: ChangePasswordRequest, user: dict = Depends(require_user)):
    """Change the caller's password. Requires the current password — this is
    a self-service change, not an admin reset, so it re-proves identity the
    same way login does."""
    from metadata_store.db import get_session_factory
    from shared.password import verify_password

    session_factory = get_session_factory()
    async with session_factory() as session:
        db_user = await _load_own_user_row(session, user)
        if not db_user.password_hash or not await asyncio.to_thread(
            verify_password, body.current_password, db_user.password_hash
        ):
            raise AuthenticationError("Current password is incorrect")

        db_user.password_hash = await asyncio.to_thread(hash_password, body.new_password)
        await session.commit()

    logger.info("Password changed for user_id=%s", db_user.id)


@router.post("/delete-account", status_code=204)
async def delete_account(body: DeleteAccountRequest, user: dict = Depends(require_user)):
    """Permanently delete the caller's own account. Requires the current
    password (a destructive-action guard, same reasoning as change-password) —
    this is a self-service deletion, not an admin action."""
    from metadata_store.db import get_session_factory
    from shared.password import verify_password

    session_factory = get_session_factory()
    async with session_factory() as session:
        db_user = await _load_own_user_row(session, user)
        if not db_user.password_hash or not await asyncio.to_thread(
            verify_password, body.password, db_user.password_hash
        ):
            raise AuthenticationError("Password is incorrect")

        await session.delete(db_user)
        await session.commit()

    logger.info("Account deleted for user_id=%s", user["sub"])
