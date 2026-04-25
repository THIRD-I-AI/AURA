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

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Awaitable, Callable, Mapping, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from shared.config import settings
from shared.logging_config import get_logger, setup_logging
from shared.observability import init_metrics, init_sentry
from shared.middleware import (
    APIKeyMiddleware,
    JWTAuthMiddleware,
    RateLimitMiddleware,
    RequestIDMiddleware,
    RequestLoggingMiddleware,
    register_exception_handlers,
)

logger = get_logger("aura.factory")


HealthCheck = Callable[[], Awaitable[Optional[str]]]
"""A health probe — returns ``None`` when healthy, or a short error string."""


def create_service(
    *,
    name: str,
    service_tag: str,
    version: str = "2.0.0",
    description: str | None = None,
    lifespan: Optional[Callable[..., Any]] = None,
    health_checks: Optional[Mapping[str, HealthCheck]] = None,
    health_check_timeout: float = 2.0,
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

    # Init Sentry before app creation so errors during startup are captured.
    # No-op when AURA_SENTRY_DSN is unset.
    init_sentry(service_tag=service_tag)

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
    # Explicit methods/headers when allow_credentials=True; wildcard + credentials
    # is spec-violating and browsers silently drop such responses. Expose the
    # request-id header so the frontend can surface it in error reports.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS", "HEAD"],
        allow_headers=[
            "Authorization", "Content-Type", "X-API-Key", "X-Request-ID",
            "X-AURA-Signature", "X-AURA-Event", "X-AURA-Delivery",
            "X-Upload-Id", "X-Workspace-Id",
            "Last-Event-ID", "Accept", "Origin",
        ],
        expose_headers=["X-Request-ID"],
    )
    #  2. Rate limiting  (env-driven; can be disabled via AURA_RATE_LIMIT_ENABLED=0)
    if settings.rate_limit_enabled:
        from shared.rate_limit import get_rate_limit_backend
        rl_backend = get_rate_limit_backend()
        app.add_middleware(
            RateLimitMiddleware,
            requests_per_window=settings.rate_limit_requests,
            window_seconds=settings.rate_limit_window_seconds,
            backend=rl_backend,
        )
        logger.info(
            "Rate limiting ENABLED for %s (%d req / %ds per IP)",
            name, settings.rate_limit_requests, settings.rate_limit_window_seconds,
        )
    else:
        logger.warning("Rate limiting DISABLED for %s (AURA_RATE_LIMIT_ENABLED=false)", name)
    #  3. JWT auth  (opt-in — AURA_JWT_ENABLED=true)
    if settings.jwt_enabled:
        app.add_middleware(JWTAuthMiddleware)
        logger.info("JWT authentication ENABLED for %s", name)
    #  4. API Key auth  (opt-in — only when AURA_API_KEY is set)
    if settings.api_key:
        app.add_middleware(APIKeyMiddleware, api_key=settings.api_key)
        logger.info("API key authentication ENABLED for %s", name)
    #  5. Request-ID  (sets request.state.request_id)
    app.add_middleware(RequestIDMiddleware)
    #  6. Request logging  (uses request_id set above)
    app.add_middleware(RequestLoggingMiddleware)

    # ── Exception handlers ──────────────────────────────────────────────
    register_exception_handlers(app)

    # ── Prometheus /metrics  (no-op when dep missing or disabled) ───────
    init_metrics(app, service_tag=service_tag)

    # ── Standard health endpoint ────────────────────────────────────────
    # When `health_checks` is provided, each probe runs in parallel with a
    # bounded timeout. The endpoint returns 503 if any probe fails or times out.
    checks = dict(health_checks or {})

    async def _run_probe(probe: HealthCheck) -> Optional[str]:
        try:
            return await asyncio.wait_for(probe(), timeout=health_check_timeout)
        except asyncio.TimeoutError:
            return f"timeout after {health_check_timeout}s"
        except Exception as exc:  # noqa: BLE001 — surface message, never crash /health
            return f"{type(exc).__name__}: {exc}"

    @app.get("/health")
    async def health():
        base = {
            "status": "healthy",
            "service": service_tag,
            "version": version,
            "environment": settings.environment,
        }
        if not checks:
            return base
        results = await asyncio.gather(*(_run_probe(p) for p in checks.values()))
        details = dict(zip(checks.keys(), results))
        unhealthy = {k: v for k, v in details.items() if v is not None}
        if unhealthy:
            return JSONResponse(
                status_code=503,
                content={**base, "status": "degraded", "checks": details},
            )
        return {**base, "checks": {k: "ok" for k in checks}}

    logger.info(
        "AURA %s service created (CORS origins=%s)", name, settings.cors_origins,
    )
    return app
