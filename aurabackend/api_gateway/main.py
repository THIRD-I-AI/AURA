"""
AURA API Gateway — Enterprise Orchestrator
============================================
Mounts all domain routers, starts background services (evolution engine,
pipeline monitor), and exposes a unified system health endpoint.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx

from shared.logging_config import get_logger
from shared.service_factory import create_service

logger = get_logger("aura.api_gateway")


# ── Lifespan: start/stop background services ──────────────────────

_UASR_URL = os.getenv("AURA_UASR_URL", "http://localhost:8009")
_UASR_POLL_INTERVAL = float(os.getenv("AURA_UASR_POLL_SECONDS", "5"))
_FILE_METADATA_REFRESH_INTERVAL = float(
    os.getenv("AURA_FILE_METADATA_REFRESH_SECONDS", "60"),
)


async def _file_metadata_refresh_loop(stop_event: asyncio.Event) -> None:
    """Sprint P-2a — defensive 60s refresh of gateway_file_metadata.

    The upload endpoint already pushes a populate task on every new
    file, so this loop is a safety net for: (a) files placed in the
    upload dir out-of-band (e.g. scp'd by an operator), (b) files
    whose mtime changes after upload (a manual edit), (c) dropped
    background tasks. Each tick walks the upload dir, re-indexes
    anything stale, and prunes rows for missing files.

    Never raises — exceptions are logged at debug so a momentary DB
    blip doesn't spam the warning channel."""
    from pathlib import Path as _Path

    from api_gateway import persistence

    base = _Path(__file__).resolve().parent.parent
    upload_dir = base / "data" / "uploads"
    while not stop_event.is_set():
        try:
            stats = await persistence.refresh_stale_file_metadata(str(upload_dir))
            if stats["indexed"] or stats["pruned"]:
                logger.debug("file_metadata refresh: %s", stats)
        except Exception as exc:
            logger.debug("file_metadata refresh tick failed: %s", exc)
        # Sprint P-2b: keep schema context in sync with the upload dir.
        # Only rebuilds when the fingerprint (dir × file × mtime hash)
        # differs from what's cached — idempotent on a quiet dir.
        try:
            fp = persistence.compute_schema_fingerprint([str(upload_dir)])
            if fp and not await persistence.get_schema_context(fp):
                await persistence.refresh_schema_context([str(upload_dir)])
        except Exception as exc:
            logger.debug("schema_context refresh tick failed: %s", exc)
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=_FILE_METADATA_REFRESH_INTERVAL,
            )
        except asyncio.TimeoutError:
            pass


async def _uasr_metrics_poller(stop_event: asyncio.Event) -> None:
    """Poll UASR metrics every few seconds and publish to ``uasr:metrics``."""
    from shared.streaming_manager import StreamEvent, streaming_manager

    topic = "uasr:metrics"
    async with httpx.AsyncClient(timeout=5) as client:
        while not stop_event.is_set():
            try:
                r = await client.get(f"{_UASR_URL}/uasr/metrics")
                if r.status_code == 200:
                    await streaming_manager.publish(StreamEvent(
                        topic=topic, event_type="data", payload=r.json(),
                    ))
            except Exception as exc:
                logger.debug("UASR metrics poll failed: %s", exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_UASR_POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass


@asynccontextmanager
async def _lifespan(app) -> AsyncGenerator[None, None]:
    # Run Alembic migrations to head — single source of truth for schema.
    # Falls back to create_all() if Alembic fails (e.g. dev environments
    # without the migration tree installed).
    try:
        from shared.db_migrations import run_migrations_to_head
        await run_migrations_to_head()
        logger.info("Alembic migrations applied (head)")
    except Exception as exc:
        logger.warning("Alembic migrations failed, falling back to create_all: %s", exc)
        try:
            from evolution.db import init_evolution_db
            from metadata_store.db import init_db
            await init_db()
            await init_evolution_db()
            logger.info("DB tables initialised via create_all (fallback)")
        except Exception as exc2:
            logger.warning("DB init failed (non-fatal): %s", exc2)

    # Sprint P-1: initialise the gateway's own persistence (query
    # history + saved queries + share tokens). Tables auto-create on
    # first run; safe to re-invoke. Without this, the routes that
    # read/write the SQL-backed stores would fail on cold start.
    try:
        from api_gateway.persistence import init_database as init_gateway_persistence
        await init_gateway_persistence()
        logger.info("Gateway persistence ready")
    except Exception as exc:
        logger.warning("Gateway persistence init failed (non-fatal): %s", exc)

    # Start the evolution engine background loop
    try:
        from evolution.engine import get_evolution_engine
        engine = get_evolution_engine()
        engine.start()
        logger.info("Evolution engine started")
    except Exception as exc:
        logger.warning("Could not start evolution engine: %s", exc)

    # Start UASR metrics poller
    uasr_stop = asyncio.Event()
    uasr_task = asyncio.create_task(_uasr_metrics_poller(uasr_stop))
    logger.info("UASR metrics poller started (interval=%ss)", _UASR_POLL_INTERVAL)

    # Start outbound webhook dispatcher
    try:
        from shared.webhook_dispatcher import webhook_dispatcher
        await webhook_dispatcher.start()
        logger.info("Webhook dispatcher started")
    except Exception as exc:
        logger.warning("Could not start webhook dispatcher: %s", exc)

    # Start saved-query in-process scheduler
    try:
        from api_gateway.routers.queries import start_saved_query_scheduler
        await start_saved_query_scheduler()
        logger.info("Saved-query scheduler started")
    except Exception as exc:
        logger.warning("Could not start saved-query scheduler: %s", exc)

    # Sprint P-2a: file metadata cache refresh tick. Walks the upload
    # dir every 60s; re-indexes anything whose mtime changed and
    # prunes rows for deleted files. Defensive fallback to the
    # upload-time hook in files.py.
    file_meta_stop = asyncio.Event()
    file_meta_task = asyncio.create_task(_file_metadata_refresh_loop(file_meta_stop))
    logger.info("File metadata refresh loop started (interval=60s)")

    yield  # ── application runs ──

    # Stop file-metadata refresh
    file_meta_stop.set()
    try:
        await asyncio.wait_for(file_meta_task, timeout=2)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        file_meta_task.cancel()

    # Stop UASR poller
    uasr_stop.set()
    try:
        await asyncio.wait_for(uasr_task, timeout=2)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        uasr_task.cancel()

    # Stop webhook dispatcher
    try:
        from shared.webhook_dispatcher import webhook_dispatcher
        await webhook_dispatcher.stop()
    except Exception:
        pass

    # Stop saved-query scheduler
    try:
        from api_gateway.routers.queries import stop_saved_query_scheduler
        await stop_saved_query_scheduler()
    except Exception:
        pass

    # Sprint P-3 finding #6: drain the PostgreSQL connection pool registry
    try:
        from api_gateway.routers.queries import close_all_pg_pools
        await close_all_pg_pools()
    except Exception:
        pass

    # Stop evolution engine gracefully
    try:
        from evolution.engine import get_evolution_engine
        get_evolution_engine().stop()
    except Exception:
        pass


# ── Create the app ─────────────────────────────────────────────────

app = create_service(
    name="API Gateway",
    service_tag="api_gateway",
    description="Enterprise self-healing data analytics platform gateway",
    lifespan=_lifespan,
)


# ── Mount routers under /api/v1 ────────────────────────────────────
# All domain routers are versioned. Infrastructure endpoints (/health,
# /system/*, /) stay at root so load balancers and probes don't break.

_API_V1 = "/api/v1"

from api_gateway.routers.auth import router as auth_router
from api_gateway.routers.chat import router as chat_router
from api_gateway.routers.collab import router as collab_router
from api_gateway.routers.connections import router as connections_router
from api_gateway.routers.counterfactual import router as counterfactual_router
from api_gateway.routers.dashboards import router as dashboards_router
from api_gateway.routers.etl import router as etl_router
from api_gateway.routers.files import router as files_router
from api_gateway.routers.inbound_hooks import router as inbound_hooks_router
from api_gateway.routers.lineage import router as lineage_router
from api_gateway.routers.pipelines import router as pipelines_router
from api_gateway.routers.queries import router as queries_router
from api_gateway.routers.stream import router as stream_router
from api_gateway.routers.webhooks import router as webhooks_router
from api_gateway.routers.workspaces import router as workspaces_router

app.include_router(auth_router, prefix=_API_V1)
app.include_router(workspaces_router, prefix=_API_V1)
app.include_router(chat_router, prefix=_API_V1)
app.include_router(files_router, prefix=_API_V1)
app.include_router(connections_router, prefix=_API_V1)
app.include_router(queries_router, prefix=_API_V1)
app.include_router(dashboards_router, prefix=_API_V1)
app.include_router(lineage_router, prefix=_API_V1)
app.include_router(etl_router, prefix=_API_V1)
app.include_router(pipelines_router, prefix=_API_V1)
app.include_router(stream_router, prefix=_API_V1)
app.include_router(webhooks_router, prefix=_API_V1)
app.include_router(inbound_hooks_router, prefix=_API_V1)
app.include_router(counterfactual_router, prefix=_API_V1)
# Collab WS sits at the root, not /api/v1/, because browsers cannot send
# auth headers on WebSocket handshakes — keeping it off the JWT-guarded
# prefix avoids the accidental block that the auth middleware would do.
app.include_router(collab_router)

# Agentic DE framework
try:
    from agents.api import router as agent_router
    app.include_router(agent_router, prefix=_API_V1)
except ImportError:
    logger.info("Agent framework not available — skipping")

# Streaming pipeline engine
try:
    from pipeline.streaming.streaming_api import router as streaming_router
    app.include_router(streaming_router, prefix=_API_V1)
except ImportError:
    logger.info("Streaming engine not available — skipping")

# Evolution engine API
try:
    from evolution.api import router as evolution_router
    app.include_router(evolution_router, prefix=_API_V1)
    logger.info("Evolution engine API mounted at %s/evolution", _API_V1)
except ImportError as exc:
    logger.warning("Evolution API not available: %s", exc)


# ── Unified system health endpoint ─────────────────────────────────

_SERVICES = {
    "code_generation": "http://localhost:8001/health",
    "database_service": "http://localhost:8002/health",
    "execution_sandbox": "http://localhost:8003/health",
    "scheduler":        "http://localhost:8004/health",
    "insights":         "http://localhost:8005/health",
    "metadata_store":   "http://localhost:8007/health",
    "uasr":             "http://localhost:8009/health",
}


@app.get("/system/health")
async def system_health():
    """
    Poll every microservice and return a unified health report.
    Also fetches the latest Hᵤ healing score from UASR.

    The API gateway itself always counts as healthy (it's serving this
    request). If no optional services respond, the endpoint still
    returns a useful result for the dashboard.
    """
    results: dict = {"api_gateway": {"status": "healthy"}}
    async with httpx.AsyncClient(timeout=2.0) as client:
        responses = await asyncio.gather(
            *[client.get(url) for url in _SERVICES.values()],
            return_exceptions=True,
        )

    for name, resp in zip(_SERVICES.keys(), responses):
        if isinstance(resp, Exception):
            results[name] = {"status": "down", "error": str(resp)}
        elif resp.status_code == 200:
            results[name] = {"status": "healthy"}
        else:
            results[name] = {"status": "degraded", "http_status": resp.status_code}

    # Healing coefficient
    hu_score = None
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get("http://localhost:8009/uasr/metrics")
            if r.status_code == 200:
                hu_score = r.json().get("hu_score")
    except Exception:
        pass

    healthy = sum(1 for v in results.values() if v["status"] == "healthy")
    total = len(results)
    overall = (
        "healthy" if healthy == total
        else "degraded" if healthy >= total // 2
        else "critical"
    )

    # Circuit breaker states
    try:
        from shared.circuit_breaker import all_breaker_states
        circuit_breakers = all_breaker_states()
    except Exception:
        circuit_breakers = {}

    # Publish snapshot to the streaming manager so the LiveDashboard gets it
    try:
        from shared.streaming_manager import TOPIC_SYSTEM, streaming_manager
        from shared.tasks import fire_and_forget
        fire_and_forget(streaming_manager.publish(
            __import__("shared.streaming_manager", fromlist=["StreamEvent"]).StreamEvent(
                topic=f"{TOPIC_SYSTEM}:health",
                event_type="data",
                payload={
                    "overall": overall,
                    "healthy_services": healthy,
                    "total_services": total,
                    "hu_score": hu_score,
                },
            )
        ), name="health-broadcast")
    except Exception as exc:
        logger.debug("health snapshot broadcast scheduling failed: %s", exc)

    return {
        "overall": overall,
        "healthy_services": healthy,
        "total_services": total,
        "services": results,
        "hu_score": hu_score,
        "circuit_breakers": circuit_breakers,
    }


@app.get("/system/evolution")
async def evolution_summary():
    """Quick summary of the self-evolution engine state."""
    try:
        from evolution.engine import get_evolution_engine
        engine = get_evolution_engine()
        return {
            "running": engine._running,
            "cycle_count": engine._cycle_count,
            "interval_seconds": engine._interval,
        }
    except Exception as exc:
        return {"error": str(exc)}


# ── Root ───────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "message": "AURA Enterprise API Gateway",
        "version": "3.0.0",
        "features": [
            "self-healing (UASR)",
            "self-evolving (Evolution Engine)",
            "pipeline monitoring (MonitorAgent)",
            "multi-agent DAG execution",
            "real-time streaming (SSE)",
        ],
    }


# ── Entrypoint ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_GATEWAY_PORT", "8000")),
    )
