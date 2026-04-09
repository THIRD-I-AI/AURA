"""
AURA Service Factory
=====================
One function to rule them all.  Every microservice calls ``create_service()``
instead of raw ``FastAPI()`` and gets CORS, logging, error handling, request-ID
tracking, and a standard ``/health`` endpoint — for free.

Usage:
    from shared.service_factory import create_service

    app = create_service(
        name="Code Generation",
        service_tag="code_generation",
    )

    @app.post("/generate_code")
    async def generate_code(...):
        ...
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.config import settings
from shared.logging_config import get_logger, setup_logging
from shared.middleware import (
    APIKeyMiddleware,
    RequestIDMiddleware,
    RequestLoggingMiddleware,
    register_exception_handlers,
)

logger = get_logger("aura.factory")


def create_service(
    *,
    name: str,
    service_tag: str,
    version: str = "2.0.0",
    description: str | None = None,
    lifespan: Optional[Callable[..., Any]] = None,
) -> FastAPI:
    """
    Build a fully-configured FastAPI application.

    Parameters
    ----------
    name : str
        Human-readable service name, e.g. ``"Code Generation"``.
    service_tag : str
        Machine identifier, e.g. ``"code_generation"``.  Used in health
        responses and log prefixes.
    version : str
        SemVer string shown in OpenAPI docs.
    description : str | None
        Optional long description for the API docs.
    lifespan : callable | None
        Optional async context-manager for startup/shutdown logic.
        Signature must match ``async def lifespan(app: FastAPI)``.

    Returns
    -------
    FastAPI
        Ready-to-serve application with:
        • CORS middleware  (reads ``settings.cors_origins``)
        • Request-ID middleware  (``X-Request-ID``)
        • Request-logging middleware
        • Structured exception handlers for ``AuraError`` tree
        • ``GET /health`` endpoint
    """

    # ── Ensure logging is configured ────────────────────────────────────
    setup_logging(
        level=settings.log_level,
        fmt=settings.log_format,
        log_file=settings.log_file,
    )

    # ── Wrap user-supplied lifespan if needed ────────────────────────────
    @asynccontextmanager
    async def _default_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        logger.info("Starting AURA %s service (env=%s)", name, settings.environment)
        yield
        logger.info("Shutting down AURA %s service", name)

    effective_lifespan = lifespan or _default_lifespan

    # ── Create the app ──────────────────────────────────────────────────
    app = FastAPI(
        title=f"AURA {name} Service",
        description=description or f"AURA {name} — part of the AURA analytics platform",
        version=version,
        lifespan=effective_lifespan,
    )

    # ── Middleware (order matters — outermost first) ─────────────────────
    #  1. CORS  (outermost so preflight always gets headers)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    #  2. API Key auth  (opt-in — only when AURA_API_KEY is set)
    if settings.api_key:
        app.add_middleware(APIKeyMiddleware, api_key=settings.api_key)
        logger.info("API key authentication ENABLED for %s", name)
    #  3. Request-ID  (sets request.state.request_id)
    app.add_middleware(RequestIDMiddleware)
    #  4. Request logging  (uses request_id set above)
    app.add_middleware(RequestLoggingMiddleware)

    # ── Exception handlers ──────────────────────────────────────────────
    register_exception_handlers(app)

    # ── Standard health endpoint ────────────────────────────────────────
    @app.get("/health")
    async def health():
        return {
            "status": "healthy",
            "service": service_tag,
            "version": version,
            "environment": settings.environment,
        }

    logger.info(
        "AURA %s service created (CORS origins=%s)", name, settings.cors_origins,
    )
    return app
