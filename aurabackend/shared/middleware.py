"""
AURA Middleware
================
Reusable ASGI middleware for every microservice:
  • Request-ID tracking (X-Request-ID header)
  • Structured request/response logging
  • Centralized exception → JSON error response mapping

Usage:
    These are applied automatically by ``shared.service_factory.create_service()``.
"""

from __future__ import annotations

import hmac
import time
import uuid
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from shared.exceptions import AuraError
from shared.logging_config import bind_request_id, get_logger, reset_request_id

logger = get_logger("aura.middleware")


# ── Request-ID Middleware ────────────────────────────────────────────────

class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Injects a unique ``X-Request-ID`` header into every request/response.
    If the client sends one, it is reused; otherwise a new UUID is generated.
    """

    async def dispatch(self, request: Request, call_next: Callable):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id

        token = bind_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)
        response.headers["X-Request-ID"] = request_id
        return response


# ── Request Logging Middleware ───────────────────────────────────────────

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs method, path, status code, and duration for every request."""

    async def dispatch(self, request: Request, call_next: Callable):
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        # Skip noisy health checks from logs
        if request.url.path not in ("/health", "/healthz", "/ready"):
            request_id = getattr(request.state, "request_id", "-")
            logger.info(
                # ASCII arrow only — Windows local runs default stdout to
                # cp1252 which can't encode → and turns request logging
                # into a UnicodeEncodeError per request. Linux CI is
                # UTF-8 either way.
                "%s %s -> %s (%.1f ms) [%s]",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                request_id,
            )
        return response


class AuditLogMiddleware(BaseHTTPMiddleware):
    """TRAIGA: append every request to the immutable audit log.

    Health/metrics paths are skipped — they're noise and would dwarf the
    real prompt records, defeating retention math. Authentication paths
    (token issue/exchange) are kept since TRAIGA wants identity events.
    """

    _SKIP = {"/health", "/healthz", "/ready", "/metrics"}

    async def dispatch(self, request: Request, call_next: Callable):
        response = await call_next(request)
        if request.url.path in self._SKIP:
            return response
        try:
            from shared.audit_log import AUDIT_ENABLED, audit_request
            if AUDIT_ENABLED:
                user = ""
                # JWTAuthMiddleware stashes the decoded principal here
                principal = getattr(request.state, "principal", None)
                if isinstance(principal, dict):
                    user = principal.get("sub", "") or principal.get("email", "")
                audit_request(
                    method=request.method,
                    path=request.url.path,
                    status=response.status_code,
                    request_id=getattr(request.state, "request_id", ""),
                    user=user,
                )
        except Exception as exc:
            logger.warning("audit middleware failed (non-fatal): %s", exc)
        return response


# ── API Key Authentication Middleware ────────────────────────────────

# Paths that never require authentication
_PUBLIC_PATHS = {
    "/health", "/healthz", "/ready", "/docs", "/openapi.json", "/redoc",
    "/api/v1/auth/token", "/api/v1/auth/register",
}


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Opt-in API key gate.

    When ``api_key`` is set (non-empty), every request must include::

        X-API-Key: <the-configured-key>

    Health and OpenAPI doc endpoints are always public.
    """

    def __init__(self, app, api_key: str) -> None:
        super().__init__(app)
        self._api_key = api_key

    async def dispatch(self, request: Request, call_next: Callable):
        # Always allow public paths
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        # Allow CORS preflight
        if request.method == "OPTIONS":
            return await call_next(request)

        supplied = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(supplied, self._api_key):
            logger.warning(
                "API key rejected for %s %s (from %s)",
                request.method,
                request.url.path,
                request.client.host if request.client else "unknown",
            )
            return JSONResponse(
                status_code=401,
                content={"error": "UNAUTHORIZED", "message": "Invalid or missing API key"},
            )

        return await call_next(request)



# ── JWT Authentication Middleware ──────────────────────────────────────

class JWTAuthMiddleware(BaseHTTPMiddleware):
    """Opt-in Bearer-token gate.

    When enabled, every request (except public paths and OPTIONS) must
    include a valid ``Authorization: Bearer <jwt>`` header.  The decoded
    payload is stashed on ``request.state.user`` for downstream handlers.
    """

    async def dispatch(self, request: Request, call_next: Callable):
        if request.url.path in _PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"error": "AUTHENTICATION_REQUIRED", "message": "Bearer token required"},
            )

        token = auth_header[7:]  # strip "Bearer "
        try:
            from shared.auth import decode_access_token
            payload = decode_access_token(token)
        except Exception as exc:
            logger.warning(
                "JWT rejected for %s %s: %s",
                request.method, request.url.path, exc,
            )
            # Sec-2 #25: don't leak token-validation internals (which
            # claim failed, signature vs expiry vs audience) to an
            # unauthenticated caller. Generic message; full reason
            # already logged above.
            return JSONResponse(
                status_code=401,
                content={"error": "AUTHENTICATION_REQUIRED", "message": "Authentication required"},
            )

        request.state.user = payload
        return await call_next(request)


# ── Rate Limiting Middleware ────────────────────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter with pluggable backend.

    Delegates to a ``RateLimitBackend`` (in-memory or Redis).
    When no backend is provided, falls back to in-memory.

    Parameters
    ----------
    requests_per_window : int
        Maximum requests allowed per window (default 100).
    window_seconds : int
        Sliding window duration in seconds (default 60).
    backend : RateLimitBackend | None
        Storage backend.  ``None`` = auto-create ``InMemoryBackend``.
    """

    def __init__(
        self,
        app,
        requests_per_window: int = 100,
        window_seconds: int = 60,
        exempt_path_prefixes: tuple[str, ...] = ("/stream/",),
        backend=None,
        trust_forwarded_for: bool | None = None,
    ) -> None:
        super().__init__(app)
        self._max_requests = requests_per_window
        self._window = window_seconds
        self._exempt_prefixes = exempt_path_prefixes

        if backend is None:
            from shared.rate_limit import InMemoryBackend
            backend = InMemoryBackend()
        self._backend = backend

        # Sec-4: X-Forwarded-For is spoofable by any client. Honour it
        # only when explicitly opted in via AURA_TRUST_FORWARDED_FOR=true,
        # which the operator should set only when the service runs
        # behind a known reverse proxy that overwrites the header. None
        # at construction time falls back to the global setting so
        # tests can override the flag without monkey-patching env vars.
        # Sec-4.1: narrow except to (ImportError, AttributeError) so a
        # production-config ValueError raised by config field-validators
        # propagates instead of being silently swallowed into a fail-
        # closed default. ValueErrors here mean the operator botched the
        # config and should hear about it loudly at startup.
        if trust_forwarded_for is None:
            try:
                from shared.config import settings
                trust_forwarded_for = bool(settings.trust_forwarded_for)
            except (ImportError, AttributeError):
                trust_forwarded_for = False
        self._trust_forwarded_for = trust_forwarded_for

    def _client_ip(self, request: Request) -> str:
        if self._trust_forwarded_for:
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path
        if path in _PUBLIC_PATHS:
            return await call_next(request)
        # Exempt long-lived SSE streams — they hold one connection open for
        # minutes and would instantly exhaust a per-IP sliding window.
        if any(path.startswith(p) for p in self._exempt_prefixes):
            return await call_next(request)

        ip = self._client_ip(request)
        allowed, retry_after = await self._backend.check_and_record(
            ip, self._max_requests, self._window,
        )

        if not allowed:
            logger.warning(
                "Rate limit exceeded for %s on %s %s",
                ip, request.method, request.url.path,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": "RATE_LIMITED",
                    "message": f"Too many requests. Retry after {retry_after}s.",
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)


# ── Exception Handlers ──────────────────────────────────────────────────

# ── Security Headers Middleware ─────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Set defensive response headers on every response.

    Sec-4: AURA didn't set X-Content-Type-Options / X-Frame-Options /
    Referrer-Policy / HSTS on responses, leaving the api_gateway public
    surface exposed to MIME-sniffing, clickjacking, and referer-leak
    classes of attacks. HSTS is set only in production to avoid
    breaking the localhost http:// dev flow.

    Headers chosen are the OWASP-recommended minimum set for a JSON
    API. No Content-Security-Policy because the gateway also serves
    no HTML — CSP would have no effect on the API surface and would
    need bespoke per-page values if the frontend were ever co-served.
    """

    def __init__(self, app, *, hsts: bool = False, hsts_max_age: int = 31536000) -> None:
        super().__init__(app)
        self._hsts = hsts
        self._hsts_max_age = hsts_max_age

    def apply_to(self, response) -> None:
        """Set defensive headers on an arbitrary response.

        Sec-4.1: exposed as a public method so global exception handlers
        can call it on their JSONResponse — when ``call_next`` raises,
        the response constructed by an `@app.exception_handler` doesn't
        re-traverse this middleware on its way back to the client, so
        the headers would otherwise be missing on every 4xx/5xx error
        response.
        """
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if self._hsts:
            response.headers["Strict-Transport-Security"] = (
                f"max-age={self._hsts_max_age}; includeSubDomains"
            )

    async def dispatch(self, request: Request, call_next: Callable):
        response = await call_next(request)
        self.apply_to(response)
        return response


def register_exception_handlers(app: FastAPI) -> None:
    """Attach global exception handlers so every error returns structured JSON."""

    def _apply_security_headers(response) -> None:
        # Sec-4.1: walk the app's user_middleware to find the
        # SecurityHeadersMiddleware instance and apply its headers
        # to error responses. The middleware's dispatch path doesn't
        # run for exception-handler-produced responses because
        # `await call_next(request)` raised before the dispatch
        # reached the post-call_next header block. Look up by class
        # name so this stays decoupled from import order (the
        # service_factory registers the middleware AFTER calling
        # register_exception_handlers in some flows).
        for mw in app.user_middleware:
            cls = getattr(mw, "cls", None)
            if cls is not None and cls.__name__ == "SecurityHeadersMiddleware":
                kwargs = getattr(mw, "kwargs", None) or {}
                hsts = bool(kwargs.get("hsts", False))
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["X-Frame-Options"] = "DENY"
                response.headers["Referrer-Policy"] = "no-referrer"
                if hsts:
                    response.headers["Strict-Transport-Security"] = (
                        "max-age=31536000; includeSubDomains"
                    )
                return

    @app.exception_handler(AuraError)
    async def _handle_aura_error(request: Request, exc: AuraError):
        request_id = getattr(request.state, "request_id", None)
        payload = exc.to_dict()
        if request_id:
            payload["request_id"] = request_id
        logger.warning(
            "AuraError %s on %s %s: %s",
            exc.error_code,
            request.method,
            request.url.path,
            exc.message,
        )
        resp = JSONResponse(status_code=exc.status_code, content=payload)
        _apply_security_headers(resp)
        return resp

    @app.exception_handler(Exception)
    async def _handle_unhandled(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", None)
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        payload = {
            "error": "INTERNAL_ERROR",
            "message": "An unexpected error occurred",
        }
        if request_id:
            payload["request_id"] = request_id
        resp = JSONResponse(status_code=500, content=payload)
        _apply_security_headers(resp)
        return resp
