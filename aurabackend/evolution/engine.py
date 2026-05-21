"""
Evolution Engine
=================
The self-improving brain of AURA.

Runs as a background loop that:
  1. Reviews recent agent execution history and feedback
  2. Identifies recurring failures and slow patterns
  3. Generates improvement proposals via LLM
  4. Validates proposals in a sandbox
  5. Promotes validated improvements (updates planner knowledge base,
     tunes agent prompts, flags risky patterns for the UASR system)
  6. Logs every decision to the SystemEvolutionLog for full auditability

The engine never deploys changes destructively — it proposes and tracks
confidence scores, deploying only when confidence ≥ threshold.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from metadata_store.db import get_session
from shared.llm_provider import get_llm

from .models import (
    AgentFeedback,
    ExecutionPattern,
    ImprovementProposal,
    ImprovementStatus,
    PatternType,
    SystemEvolutionLog,
)
from .pattern_library import PatternLibrary

logger = logging.getLogger("evolution.engine")

# How often the engine wakes up (seconds)
_DEFAULT_CYCLE_INTERVAL = int(3600)  # 1 hour
_MIN_SAMPLES_FOR_ANALYSIS = 5
_CONFIDENCE_DEPLOY_THRESHOLD = 0.75


class EvolutionEngine:
    """
    Background service that continuously improves AURA's behaviour.

    Call `start()` once at application startup — it runs as an asyncio task.
    """

    def __init__(self, cycle_interval_seconds: int = _DEFAULT_CYCLE_INTERVAL) -> None:
        self._interval = cycle_interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._cycle_count = 0

    # ── Lifecycle ──────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="evolution-engine")
        logger.info("Evolution engine started (interval=%ds)", self._interval)

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("Evolution engine stopped after %d cycles", self._cycle_count)

    # ── Main loop ──────────────────────────────────────────────────

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Evolution cycle failed: %s", exc, exc_info=True)
            await asyncio.sleep(self._interval)

    async def _run_cycle(self) -> None:
        self._cycle_count += 1
        cycle_id = uuid.uuid4().hex[:12]
        logger.info("Evolution cycle #%d started (id=%s)", self._cycle_count, cycle_id)

        async for db in get_session():
            try:
                await self._analyse_failures(db, cycle_id)
                await self._analyse_slow_patterns(db, cycle_id)
                await self._promote_validated_proposals(db, cycle_id)
                await self._persist_hu_snapshot(db, cycle_id)
                await db.commit()
                logger.info("Evolution cycle #%d complete", self._cycle_count)
            except Exception as exc:
                await db.rollback()
                logger.error("Cycle DB error: %s", exc, exc_info=True)
            break

    # ── Analysis: failures ─────────────────────────────────────────

    async def _analyse_failures(self, db: AsyncSession, cycle_id: str) -> None:
        """
        Find agent tasks with high failure rates over the past 24 hours
        and propose targeted improvements.
        """
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        result = await db.execute(
            select(
                AgentFeedback.agent_name,
                AgentFeedback.task_type,
                func.count().label("total"),
                func.sum(
                    (AgentFeedback.success == False).cast(type_=type(1))
                ).label("failures"),
                func.avg(AgentFeedback.duration_ms).label("avg_ms"),
            )
            .where(AgentFeedback.created_at >= since)
            .group_by(AgentFeedback.agent_name, AgentFeedback.task_type)
        )
        rows = result.all()

        for row in rows:
            agent, task, total, failures, avg_ms = row
            failures = failures or 0
            if total < _MIN_SAMPLES_FOR_ANALYSIS:
                continue
            failure_rate = failures / total
            if failure_rate < 0.3:
                continue  # Not concerning

            logger.warning(
                "High failure rate detected: agent=%s task=%s rate=%.1f%%",
                agent, task, failure_rate * 100,
            )

            # Gather sample failures for LLM analysis
            fail_result = await db.execute(
                select(AgentFeedback)
                .where(AgentFeedback.agent_name == agent)
                .where(AgentFeedback.task_type == task)
                .where(AgentFeedback.success == False)
                .where(AgentFeedback.created_at >= since)
                .limit(5)
            )
            samples = fail_result.scalars().all()

            proposal = await self._generate_improvement_proposal(
                target=agent,
                improvement_type="failure_reduction",
                context={
                    "agent": agent,
                    "task_type": task,
                    "failure_rate": failure_rate,
                    "total_executions": total,
                    "sample_failures": [
                        {
                            "prompt": s.user_prompt[:300],
                            "output": str(s.agent_output)[:300],
                            "correction": s.correction,
                        }
                        for s in samples
                    ],
                },
            )

            if proposal:
                db.add(proposal)
                await self._log_event(
                    db, cycle_id, "failure_analysis", agent,
                    f"Proposed improvement for {agent}/{task}: {proposal.description[:100]}",
                    {"failure_rate": failure_rate, "proposal_id": proposal.id},
                )

    # ── Analysis: slow patterns ────────────────────────────────────

    async def _analyse_slow_patterns(self, db: AsyncSession, cycle_id: str) -> None:
        """
        Identify patterns that consistently take too long and propose
        optimisations (e.g. query rewriting, schema caching).
        """
        result = await db.execute(
            select(
                ExecutionPattern.id,
                ExecutionPattern.pattern_type,
                ExecutionPattern.intent_summary,
                ExecutionPattern.avg_duration_ms,
                ExecutionPattern.success_count,
            )
            .where(ExecutionPattern.avg_duration_ms > 5000)  # > 5 seconds
            .where(ExecutionPattern.success_count >= _MIN_SAMPLES_FOR_ANALYSIS)
            .order_by(ExecutionPattern.avg_duration_ms.desc())
            .limit(5)
        )
        slow_patterns = result.all()

        for pid, ptype, summary, avg_ms, usage in slow_patterns:
            proposal = await self._generate_improvement_proposal(
                target=f"Pattern:{ptype}",
                improvement_type="performance_optimisation",
                context={
                    "pattern_id": pid,
                    "pattern_type": ptype,
                    "intent": summary,
                    "avg_duration_ms": avg_ms,
                    "usage_count": usage,
                },
            )
            if proposal:
                db.add(proposal)
                await self._log_event(
                    db, cycle_id, "slow_pattern_analysis", ptype,
                    f"Optimisation proposed for slow pattern ({avg_ms:.0f}ms avg): {summary[:80]}",
                    {"pattern_id": pid, "avg_duration_ms": avg_ms},
                )

    # ── Promote validated proposals ────────────────────────────────

    async def _promote_validated_proposals(self, db: AsyncSession, cycle_id: str) -> None:
        """
        Move proposals with sufficient confidence to DEPLOYED status
        and update the pattern library accordingly.
        """
        result = await db.execute(
            select(ImprovementProposal)
            .where(ImprovementProposal.status == ImprovementStatus.VALIDATED.value)
            .where(ImprovementProposal.confidence_score >= _CONFIDENCE_DEPLOY_THRESHOLD)
        )
        proposals = result.scalars().all()

        for proposal in proposals:
            proposal.status = ImprovementStatus.DEPLOYED.value
            proposal.deployed_at = datetime.now(timezone.utc)
            await self._log_event(
                db, cycle_id, "proposal_deployed", proposal.target,
                f"Improvement deployed: {proposal.description[:120]}",
                {"proposal_id": proposal.id, "confidence": proposal.confidence_score},
                outcome="deployed",
            )
            logger.info("Improvement deployed: %s -> %s", proposal.target, proposal.id)

    # ── Persist Hᵤ snapshot ────────────────────────────────────────

    async def _persist_hu_snapshot(self, db: AsyncSession, cycle_id: str) -> None:
        """Save a periodic Hᵤ snapshot to the HealingMetric table."""
        try:
            from uasr.models import HealingMetric
            from uasr.service import _tracker
            report = _tracker.compute()
            now = datetime.now(timezone.utc)
            snapshot = HealingMetric(
                domain="global",
                period_start=now - timedelta(seconds=self._interval),
                period_end=now,
                total_drift_events=report.total_events,
                resolved_anomalies=report.resolved_events,
                avg_latency_seconds=report.global_avg_latency,
                recovery_rate=report.global_resolution_rate,
                hu_score=report.hu_score,
            )
            db.add(snapshot)
        except Exception as exc:
            logger.debug("Could not snapshot Hᵤ (UASR may not be running): %s", exc)

    # ── LLM: generate improvement proposal ────────────────────────

    async def _generate_improvement_proposal(
        self,
        target: str,
        improvement_type: str,
        context: Dict[str, Any],
    ) -> Optional[ImprovementProposal]:
        """
        Ask the LLM to analyse context and propose a concrete improvement.
        Returns an unsaved ImprovementProposal or None if LLM is unavailable.
        """
        llm = get_llm()
        if not llm.is_available():
            return None

        prompt = (
            "You are AURA's self-evolution engine analysing system performance data.\n\n"
            f"Target component: {target}\n"
            f"Improvement type: {improvement_type}\n"
            f"Context:\n{json.dumps(context, indent=2, default=str)[:2000]}\n\n"
            "Respond with a JSON object containing:\n"
            "  description (str): one sentence describing the improvement\n"
            "  rationale (str): why this will help (2-3 sentences)\n"
            "  proposed_change (object): specific parameter or prompt changes\n"
            "  confidence (float 0-1): how confident you are this will help\n"
            "Only respond with the JSON object, no markdown fences."
        )

        try:
            response = llm.generate_json(prompt)
            if not response:
                return None

            return ImprovementProposal(
                target=target,
                improvement_type=improvement_type,
                description=str(response.get("description", ""))[:500],
                rationale=str(response.get("rationale", ""))[:1000],
                proposed_change=response.get("proposed_change", {}),
                confidence_score=float(response.get("confidence", 0.5)),
                status=ImprovementStatus.PROPOSED.value,
            )
        except Exception as exc:
            logger.warning("LLM improvement proposal failed: %s", exc)
            return None

    # ── Utility ────────────────────────────────────────────────────

    @staticmethod
    async def _log_event(
        db: AsyncSession,
        cycle_id: str,
        event_type: str,
        component: str,
        summary: str,
        payload: Dict[str, Any],
        outcome: Optional[str] = None,
    ) -> None:
        entry = SystemEvolutionLog(
            cycle_id=cycle_id,
            event_type=event_type,
            component=component,
            summary=summary,
            payload=payload,
            outcome=outcome,
        )
        db.add(entry)

    # ── Public API (for manual triggers) ──────────────────────────

    async def record_feedback(
        self,
        session_id: str,
        agent_name: str,
        task_type: str,
        user_prompt: str,
        agent_output: Dict[str, Any],
        success: bool,
        duration_ms: float,
        user_rating: Optional[int] = None,
        correction: Optional[str] = None,
    ) -> None:
        """Record agent execution feedback (called by agent framework)."""
        async for db in get_session():
            feedback = AgentFeedback(
                session_id=session_id,
                agent_name=agent_name,
                task_type=task_type,
                user_prompt=user_prompt[:1000],
                agent_output=agent_output,
                success=success,
                duration_ms=duration_ms,
                user_rating=user_rating,
                correction=correction,
            )
            db.add(feedback)
            await db.commit()
            break

    async def record_pattern(
        self,
        pattern_type: PatternType,
        intent: str,
        pattern_data: Dict[str, Any],
        duration_ms: float,
        success: bool,
    ) -> None:
        """Record an execution pattern (called after successful agent runs)."""
        async for db in get_session():
            lib = PatternLibrary(db)
            if success:
                await lib.record_success(pattern_type, intent, pattern_data, duration_ms)
            else:
                await lib.record_failure(pattern_type, intent)
            break

    async def get_similar_patterns(
        self,
        intent: str,
        pattern_type: Optional[PatternType] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve similar patterns to seed a new execution plan."""
        async for db in get_session():
            lib = PatternLibrary(db)
            return await lib.find_similar(intent, pattern_type)
        return []

    async def trigger_cycle(self) -> Dict[str, Any]:
        """Manually trigger an evolution cycle (for API/testing)."""
        cycle_id = uuid.uuid4().hex[:12]
        async for db in get_session():
            try:
                await self._analyse_failures(db, cycle_id)
                await self._analyse_slow_patterns(db, cycle_id)
                await self._promote_validated_proposals(db, cycle_id)
                await self._persist_hu_snapshot(db, cycle_id)
                await db.commit()
                return {"status": "completed", "cycle_id": cycle_id,
                        "total_cycles": self._cycle_count}
            except Exception as exc:
                await db.rollback()
                # Sec-2 #11: this dict propagates through evolution/api.py:84
                # straight to an authenticated client. Log full exception
                # detail (with traceback) server-side; return a generic
                # message so we don't leak DB-engine internals.
                logger.error(
                    "Evolution cycle %s failed: %s",
                    cycle_id, exc, exc_info=True,
                )
                return {"status": "error", "error": "evolution cycle failed", "cycle_id": cycle_id}
            break
        return {"status": "error", "error": "no db session"}


# ── Singleton ──────────────────────────────────────────────────────

_engine_instance: Optional[EvolutionEngine] = None


def get_evolution_engine() -> EvolutionEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = EvolutionEngine()
    return _engine_instance
