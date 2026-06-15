"""
AURA JWT Authentication
========================
Provides token creation, validation, and a FastAPI dependency that extracts
the current user from an ``Authorization: Bearer <token>`` header.

Opt-in: the ``JWTAuthMiddleware`` only activates when ``AURA_JWT_ENABLED=true``
(see ``shared.config``).  API-key auth (``APIKeyMiddleware``) remains a
separate, independent layer.

Usage — protecting a route::

    from shared.auth import require_user
    @app.get("/me")
    async def me(user: dict = Depends(require_user)):
        return user

Usage — issuing tokens::

    from shared.auth import create_access_token
    token = create_access_token({"sub": user_id, "email": email})
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from shared.config import settings
from shared.exceptions import AuthenticationError, ForbiddenError
from shared.logging_config import get_logger

logger = get_logger("aura.auth")

_bearer_scheme = HTTPBearer(auto_error=False)


# ── Token creation ──────────────────────────────────────────────────────

def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Return a signed JWT containing *data* as claims.

    ``sub`` (subject) is required in *data*.  An ``exp`` claim is always
    added based on ``settings.access_token_expire_minutes``.
    """
    if "sub" not in data:
        raise ValueError("Token payload must include 'sub' (subject)")

    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


# ── Token validation ────────────────────────────────────────────────────

def decode_access_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT.  Raises ``AuthenticationError`` on failure."""
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError:
        raise AuthenticationError("Token has expired")
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError(f"Invalid token: {exc}")

    if "sub" not in payload:
        raise AuthenticationError("Token missing 'sub' claim")

    return payload


# ── FastAPI dependencies ────────────────────────────────────────────────

async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Optional[Dict[str, Any]]:
    """Extract user from Bearer token if present, else return ``None``.

    Use ``require_user`` when authentication is mandatory.
    """
    # Already resolved by middleware and stashed on request.state?
    if hasattr(request.state, "user"):
        return request.state.user

    if credentials is None:
        return None

    return decode_access_token(credentials.credentials)


async def require_user(
    user: Optional[Dict[str, Any]] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Like ``get_current_user`` but raises 401 if no valid token."""
    if user is None:
        raise AuthenticationError("Bearer token required")
    return user


async def require_tenant(
    user: Dict[str, Any] = Depends(require_user),
) -> str:
    """The caller's tenant (org) id, derived from the *verified* token.

    SaaS Phase 1: tenant isolation keys on this, NOT on any client-supplied
    workspace/tenant header (which a token holder could forge to read another
    org's data). Falls back to the subject for tokens minted before ``org_id``
    existed, so a single-user dev token still maps to a stable tenant.
    """
    return str(user.get("org_id") or user.get("sub"))


async def require_role(
    *roles: str,
):
    """Return a dependency that checks the user has one of the given roles."""
    async def _check(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
        user_role = user.get("role", "")
        if user_role not in roles:
            raise ForbiddenError(f"Role '{user_role}' not allowed; requires one of {roles}")
        return user
    return _check
