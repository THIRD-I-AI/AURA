"""
DAR FastAPI app
================
Suggested port: 8011 (after causal_service on 8010).

    uvicorn dar_service.main:app --port 8011

Endpoints:

  POST /dar/research/run      — one-shot research for (source, table)
  GET  /dar/insights          — list recent findings (filterable)
  GET  /dar/insights/{id}     — one finding
  GET  /dar/daemon/status     — daemon liveness + last tick details
  POST /dar/daemon/start      — manual start (when AURA_DAR_ENABLED is unset)
  POST /dar/daemon/stop       — manual stop
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from metadata_store.db import get_session, init_db
from metadata_store.models import DARInsight
from shared.service_factory import create_service

from .daemon import DARDaemon, DARDaemonConfig
from .graph import run_dar

logger = logging.getLogger("aura.dar.main")


# ── Singletons ────────────────────────────────────────────────────────

_daemon: Optional[DARDaemon] = None


def get_daemon() -> DARDaemon:
    global _daemon
    if _daemon is None:
        _daemon = DARDaemon(DARDaemonConfig())
    return _daemon


@asynccontextmanager
async def _lifespan(_):
    # Ensure metadata tables exist (alembic is the canonical path; this
    # is a safety net for first-boot dev environments).
    await init_db()
    if os.getenv("AURA_DAR_ENABLED", "false").lower() == "true":
        try:
            await get_daemon().start()
        except Exception as exc:
            logger.error("DAR daemon failed to start: %s", exc)
    try:
        yield
    finally:
        if _daemon is not None and _daemon.is_running:
            await _daemon.stop()


app = create_service(
    name="DAR Service",
    service_tag="dar_service",
    description="Data Agnostic Researcher — headless background research over DuckDB analytics lake",
    lifespan=_lifespan,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for sess in get_session():
        yield sess


# ── Request / response models ─────────────────────────────────────────

class ResearchRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table_name: str = Field(min_length=1)
    source_id: str = "duckdb_lake"
    duckdb_path: Optional[str] = None


class InsightOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    source_id: str
    table_name: str
    question: str
    sql_query: Optional[str]
    finding_type: str
    summary: str
    score: float
    is_anomaly: bool
    run_id: Optional[str]
    created_at: str
    payload: Dict[str, Any] = Field(default_factory=dict)


def _serialize(row: DARInsight) -> InsightOut:
    return InsightOut(
        id=row.id,
        source_id=row.source_id,
        table_name=row.table_name,
        question=row.question,
        sql_query=row.sql_query,
        finding_type=row.finding_type,
        summary=row.summary,
        score=row.score,
        is_anomaly=row.is_anomaly,
        run_id=row.run_id,
        created_at=row.created_at.isoformat() if row.created_at else "",
        payload=row.payload or {},
    )


# ── Endpoints ─────────────────────────────────────────────────────────

@app.post("/dar/research/run")
async def trigger_research_run(req: ResearchRunRequest) -> Dict[str, Any]:
    """Run one DAR cycle synchronously. Returns the run summary plus
    the IDs of any findings persisted. For long-running workloads use
    the daemon; this endpoint is for ad-hoc / on-demand runs."""
    duckdb_path = req.duckdb_path or os.getenv("UASR_DUCKDB_PATH", "data/uasr_lake.duckdb")
    state = await run_dar(req.source_id, req.table_name, duckdb_path=duckdb_path)
    return {
        "run_id": state.run_id,
        "source_id": state.source_id,
        "table_name": state.table_name,
        "findings": len(state.findings),
        "persisted_ids": state.persisted_ids,
        "completed_nodes": state.completed_nodes,
        "errors": [{"node": e.node, "message": e.message} for e in state.errors],
    }


@app.get("/dar/insights", response_model=List[InsightOut])
async def list_insights(
    db: AsyncSession = Depends(get_db),
    table: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    finding_type: Optional[str] = Query(default=None),
    min_score: float = Query(default=0.0, ge=0.0, le=1.0),
    anomalies_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=500),
) -> List[InsightOut]:
    stmt = select(DARInsight).order_by(desc(DARInsight.created_at)).limit(limit)
    if table:
        stmt = stmt.where(DARInsight.table_name == table)
    if source:
        stmt = stmt.where(DARInsight.source_id == source)
    if finding_type:
        stmt = stmt.where(DARInsight.finding_type == finding_type)
    if min_score > 0:
        stmt = stmt.where(DARInsight.score >= min_score)
    if anomalies_only:
        stmt = stmt.where(DARInsight.is_anomaly.is_(True))
    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize(r) for r in rows]


@app.get("/dar/insights/{insight_id}", response_model=InsightOut)
async def get_insight(insight_id: str, db: AsyncSession = Depends(get_db)) -> InsightOut:
    row = (await db.execute(select(DARInsight).where(DARInsight.id == insight_id))).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"insight {insight_id} not found")
    return _serialize(row)


@app.get("/dar/daemon/status")
async def daemon_status() -> Dict[str, Any]:
    """Surface daemon liveness — operators check this after a deploy
    to confirm the background loop actually came up."""
    if _daemon is None:
        return {"running": False, "reason": "daemon not instantiated (AURA_DAR_ENABLED unset)"}
    return _daemon.status()


@app.post("/dar/daemon/start")
async def daemon_start() -> Dict[str, Any]:
    daemon = get_daemon()
    if daemon.is_running:
        return {"running": True, "message": "already running"}
    await daemon.start()
    return {"running": True, "message": "started"}


@app.post("/dar/daemon/stop")
async def daemon_stop() -> Dict[str, Any]:
    if _daemon is None or not _daemon.is_running:
        return {"running": False, "message": "not running"}
    await _daemon.stop()
    return {"running": False, "message": "stopped"}
