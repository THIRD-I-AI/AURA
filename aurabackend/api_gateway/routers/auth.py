"""
Auth Router — ``/auth``
========================
Token issuance and current-user introspection.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from shared.auth import create_access_token, require_user
from shared.logging_config import get_logger

logger = get_logger("aura.router.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Request / Response schemas ──────────────────────────────────────────

class TokenRequest(BaseModel):
    """Minimal login: user id + optional metadata.

    In production this should validate credentials (password, OAuth code,
    etc.).  For now it issues a token for the given ``user_id`` so the
    rest of the JWT pipeline can be exercised end-to-end.
    """
    user_id: str = Field(..., min_length=1)
    email: str | None = None
    name: str | None = None
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
    """Issue a signed JWT for the given user.

    **Note:** This is a development/demo endpoint.  In production, swap
    the body for real credential validation (OAuth2 password flow, SSO
    callback, etc.).
    """
    claims = {"sub": body.user_id, "role": body.role}
    if body.email:
        claims["email"] = body.email
    if body.name:
        claims["name"] = body.name

    token = create_access_token(claims)
    logger.info("Token issued for user_id=%s role=%s", body.user_id, body.role)
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
