"""
AURA Custom Exceptions
=======================
Structured exception hierarchy so every service raises consistent,
machine-readable errors instead of bare HTTPException scattered everywhere.

Usage:
    from shared.exceptions import NotFoundError, ValidationError
    raise NotFoundError("User", user_id)
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class AuraError(Exception):
    """Base for all AURA domain errors."""

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str = "An internal error occurred",
        *,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "error": self.error_code,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


# ── 4xx Client Errors ────────────────────────────────────────────────────

class ValidationError(AuraError):
    status_code = 422
    error_code = "VALIDATION_ERROR"

    def __init__(self, message: str = "Validation failed", **kw):
        super().__init__(message, **kw)


class NotFoundError(AuraError):
    status_code = 404
    error_code = "NOT_FOUND"

    def __init__(self, resource: str = "Resource", identifier: Any = None, **kw):
        msg = f"{resource} not found" if identifier is None else f"{resource} '{identifier}' not found"
        super().__init__(msg, **kw)


class AuthenticationError(AuraError):
    status_code = 401
    error_code = "AUTHENTICATION_REQUIRED"

    def __init__(self, message: str = "Authentication required", **kw):
        super().__init__(message, **kw)


class ForbiddenError(AuraError):
    status_code = 403
    error_code = "FORBIDDEN"

    def __init__(self, message: str = "Access denied", **kw):
        super().__init__(message, **kw)


class ConflictError(AuraError):
    status_code = 409
    error_code = "CONFLICT"

    def __init__(self, message: str = "Resource conflict", **kw):
        super().__init__(message, **kw)


class RateLimitError(AuraError):
    status_code = 429
    error_code = "RATE_LIMIT_EXCEEDED"

    def __init__(self, message: str = "Rate limit exceeded", **kw):
        super().__init__(message, **kw)


# ── 5xx Server Errors ───────────────────────────────────────────────────

class ServiceUnavailableError(AuraError):
    status_code = 503
    error_code = "SERVICE_UNAVAILABLE"

    def __init__(self, service: str = "Downstream service", **kw):
        super().__init__(f"{service} is unavailable", **kw)


class DatabaseError(AuraError):
    status_code = 502
    error_code = "DATABASE_ERROR"

    def __init__(self, message: str = "Database operation failed", **kw):
        super().__init__(message, **kw)


class LLMError(AuraError):
    status_code = 502
    error_code = "LLM_ERROR"

    def __init__(self, message: str = "LLM generation failed", **kw):
        super().__init__(message, **kw)
