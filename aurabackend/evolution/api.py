"""
Evolution API Router
=====================
Exposes the self-evolution engine over REST.
Mounted by the API gateway at /evolution/...
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metadata_store.db import get_session
from .db import init_evolution_db
from .engine import get_evolution_engine
from .models import (
    AgentFeedback,
    ExecutionPattern,
    ImprovementProposal,
    ImprovementStatus,
    PatternType,
    SystemEvolutionLog,
)
from .pattern_library import PatternLibrary

router = APIRouter(prefix="/evolution", tags=["Evolution Engine"])


# ── DB dependency ──────────────────────────────────────────────────

async def get_db():
    async for session in get_session():
        yield session


# ── Request/Response models ────────────────────────────────────────

class FeedbackRequest(BaseModel):
    session_id: str
    agent_name: str
    task_type: str
    user_prompt: str
    agent_output: Dict[str, Any] = Field(default_factory=dict)
    success: bool = True
    duration_ms: float = 0.0
    user_rating: Optional[int] = None
    correction: Optional[str] = None


class PatternSearchRequest(BaseModel):
    intent: str
    pattern_type: Optional[str] = None
    limit: int = 5


class ProposalUpdateRequest(BaseModel):
    status: str
    test_results: Optional[Dict[str, Any]] = None
    confidence_score: Optional[float] = None


# ── Endpoints ──────────────────────────────────────────────────────

@router.get("/status")
async def evolution_status():
    """Return the current state of the evolution engine."""
    engine = get_evolution_engine()
    return {
        "running": engine._running,
        "cycle_count": engine._cycle_count,
        "interval_seconds": engine._interval,
    }


@router.post("/cycle")
async def trigger_cycle():
    """Manually trigger an evolution analysis cycle."""
    engine = get_evolution_engine()
    result = await engine.trigger_cycle()
    return result


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest):
    """Submit agent execution feedback for learning."""
    engine = get_evolution_engine()
    await engine.record_feedback(
        session_id=req.session_id,
        agent_name=req.agent_name,
        task_type=req.task_type,
        user_prompt=req.user_prompt,
        agent_output=req.agent_output,
        success=req.success,
        duration_ms=req.duration_ms,
        user_rating=req.user_rating,
        correction=req.correction,
    )
    return {"status": "recorded"}


@router.post("/patterns/search")
async def search_patterns(req: PatternSearchRequest, db: AsyncSession = Depends(get_db)):
    """Find similar past execution patterns for a given intent."""
    pt = PatternType(req.pattern_type) if req.pattern_type else None
    lib = PatternLibrary(db)
    patterns = await lib.find_similar(req.intent, pt, limit=req.limit)
    return {"patterns": patterns, "count": len(patterns)}


@router.get("/patterns")
async def list_patterns(
    pattern_type: Optional[str] = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """List top performing execution patterns."""
    lib = PatternLibrary(db)
    pt = PatternType(pattern_type) if pattern_type else None
    patterns = await lib.top_patterns(pt, limit)
    return {"patterns": patterns, "count": len(patterns)}


@router.get("/proposals")
async def list_proposals(
    status: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List improvement proposals."""
    stmt = select(ImprovementProposal).order_by(
        ImprovementProposal.created_at.desc()
    ).limit(limit)
    if status:
        stmt = stmt.where(ImprovementProposal.status == status)
    result = await db.execute(stmt)
    proposals = result.scalars().all()
    return {
        "proposals": [
            {
                "id": p.id,
                "target": p.target,
                "improvement_type": p.improvement_type,
                "description": p.description,
                "status": p.status,
                "confidence_score": p.confidence_score,
                "created_at": p.created_at.isoformat(),
                "deployed_at": p.deployed_at.isoformat() if p.deployed_at else None,
            }
            for p in proposals
        ],
        "count": len(proposals),
    }


@router.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str, db: AsyncSession = Depends(get_db)):
    """Get full details of an improvement proposal."""
    result = await db.execute(
        select(ImprovementProposal).where(ImprovementProposal.id == proposal_id)
    )
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return {
        "id": p.id,
        "target": p.target,
        "improvement_type": p.improvement_type,
        "description": p.description,
        "rationale": p.rationale,
        "proposed_change": p.proposed_change,
        "status": p.status,
        "confidence_score": p.confidence_score,
        "test_results": p.test_results,
        "created_at": p.created_at.isoformat(),
        "deployed_at": p.deployed_at.isoformat() if p.deployed_at else None,
    }


@router.patch("/proposals/{proposal_id}")
async def update_proposal(
    proposal_id: str,
    req: ProposalUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update a proposal's status or test results (e.g. after manual review)."""
    result = await db.execute(
        select(ImprovementProposal).where(ImprovementProposal.id == proposal_id)
    )
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found")

    p.status = req.status
    if req.test_results is not None:
        p.test_results = req.test_results
    if req.confidence_score is not None:
        p.confidence_score = req.confidence_score

    await db.commit()
    return {"status": "updated", "proposal_id": proposal_id}


@router.get("/log")
async def get_evolution_log(limit: int = 100, db: AsyncSession = Depends(get_db)):
    """Return the system evolution audit log."""
    result = await db.execute(
        select(SystemEvolutionLog)
        .order_by(SystemEvolutionLog.created_at.desc())
        .limit(limit)
    )
    entries = result.scalars().all()
    return {
        "log": [
            {
                "id": e.id,
                "cycle_id": e.cycle_id,
                "event_type": e.event_type,
                "component": e.component,
                "summary": e.summary,
                "outcome": e.outcome,
                "created_at": e.created_at.isoformat(),
            }
            for e in entries
        ],
        "count": len(entries),
    }


@router.get("/feedback/summary")
async def feedback_summary(days: int = 7, db: AsyncSession = Depends(get_db)):
    """Aggregate feedback statistics for the past N days."""
    from datetime import timedelta, timezone
    from sqlalchemy import func

    since = __import__("datetime").datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(
            AgentFeedback.agent_name,
            func.count().label("total"),
            func.avg(AgentFeedback.duration_ms).label("avg_ms"),
        )
        .where(AgentFeedback.created_at >= since)
        .group_by(AgentFeedback.agent_name)
        .order_by(func.count().desc())
    )
    rows = result.all()
    return {
        "period_days": days,
        "agents": [
            {
                "agent": r[0],
                "total_executions": r[1],
                "avg_duration_ms": round(r[2] or 0, 1),
            }
            for r in rows
        ],
    }
