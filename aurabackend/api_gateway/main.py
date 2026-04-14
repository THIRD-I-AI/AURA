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
    # Initialise evolution DB tables
    try:
        from evolution.db import init_evolution_db
        from metadata_store.db import init_db
        await init_db()
        await init_evolution_db()
        logger.info("Evolution DB tables initialised")
    except Exception as exc:
        logger.warning("Evolution DB init failed (non-fatal): %s", exc)

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

    yield  # ── application runs ──

    # Stop UASR poller
    uasr_stop.set()
    try:
        await asyncio.wait_for(uasr_task, timeout=2)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        uasr_task.cancel()

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


# ── Mount routers ──────────────────────────────────────────────────

from api_gateway.routers.chat import router as chat_router
from api_gateway.routers.connections import router as connections_router
from api_gateway.routers.etl import router as etl_router
from api_gateway.routers.files import router as files_router
from api_gateway.routers.pipelines import router as pipelines_router
from api_gateway.routers.queries import router as queries_router
from api_gateway.routers.stream import router as stream_router

app.include_router(chat_router)
app.include_router(files_router)
app.include_router(connections_router)
app.include_router(queries_router)
app.include_router(etl_router)
app.include_router(pipelines_router)
app.include_router(stream_router)

# Agentic DE framework
try:
    from agents.api import router as agent_router
    app.include_router(agent_router)
except ImportError:
    logger.info("Agent framework not available — skipping")

# Streaming pipeline engine
try:
    from pipeline.streaming.streaming_api import router as streaming_router
    app.include_router(streaming_router)
except ImportError:
    logger.info("Streaming engine not available — skipping")

# Evolution engine API
try:
    from evolution.api import router as evolution_router
    app.include_router(evolution_router)
    logger.info("Evolution engine API mounted at /evolution")
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
    """
    results: dict = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
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
        async with httpx.AsyncClient(timeout=3.0) as client:
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
        asyncio.create_task(streaming_manager.publish(
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
        ))
    except Exception:
        pass

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
