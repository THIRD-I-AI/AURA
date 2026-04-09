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

import time
import uuid
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from shared.exceptions import AuraError
from shared.logging_config import get_logger

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

        response = await call_next(request)
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
                "%s %s → %s (%.1f ms) [%s]",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                request_id,
            )
        return response


# ── API Key Authentication Middleware ────────────────────────────────

# Paths that never require authentication
_PUBLIC_PATHS = {"/health", "/healthz", "/ready", "/docs", "/openapi.json", "/redoc"}


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
        if supplied != self._api_key:
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




# ── Exception Handlers ──────────────────────────────────────────────────

def register_exception_handlers(app: FastAPI) -> None:
    """Attach global exception handlers so every error returns structured JSON."""

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
        return JSONResponse(status_code=exc.status_code, content=payload)

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
        return JSONResponse(status_code=500, content=payload)
