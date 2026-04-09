"""
Pattern Library
================
Stores, retrieves, and scores successful execution patterns.
Used by the evolution engine to reuse winning strategies and
by the planner to seed new task decompositions.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ExecutionPattern, PatternType

logger = logging.getLogger("evolution.pattern_library")


def _intent_hash(intent: str) -> str:
    """Stable hash of an intent string for deduplication."""
    return hashlib.sha256(intent.lower().strip().encode()).hexdigest()[:32]


class PatternLibrary:
    """
    Persistent catalog of successful execution patterns.
    Patterns are keyed by a hash of the user intent so semantically
    similar requests can reuse proven solutions.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def record_success(
        self,
        pattern_type: PatternType,
        intent: str,
        pattern_data: Dict[str, Any],
        duration_ms: float = 0.0,
    ) -> ExecutionPattern:
        """Record a successful execution as a reusable pattern."""
        ih = _intent_hash(intent)
        result = await self._db.execute(
            select(ExecutionPattern)
            .where(ExecutionPattern.intent_hash == ih)
            .where(ExecutionPattern.pattern_type == pattern_type.value)
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update running stats
            total = existing.success_count + 1
            existing.success_count = total
            existing.avg_duration_ms = (
                (existing.avg_duration_ms * (total - 1) + duration_ms) / total
            )
            existing.pattern_data = pattern_data  # refresh with latest
            existing.last_used_at = datetime.now(timezone.utc)
            await self._db.commit()
            return existing
        else:
            pattern = ExecutionPattern(
                pattern_type=pattern_type.value,
                intent_hash=ih,
                intent_summary=intent[:500],
                pattern_data=pattern_data,
                avg_duration_ms=duration_ms,
            )
            self._db.add(pattern)
            await self._db.commit()
            await self._db.refresh(pattern)
            logger.info("New pattern recorded: type=%s, intent_hash=%s", pattern_type.value, ih)
            return pattern

    async def record_failure(
        self,
        pattern_type: PatternType,
        intent: str,
    ) -> None:
        """Increment failure count for a pattern."""
        ih = _intent_hash(intent)
        await self._db.execute(
            update(ExecutionPattern)
            .where(ExecutionPattern.intent_hash == ih)
            .where(ExecutionPattern.pattern_type == pattern_type.value)
            .values(failure_count=ExecutionPattern.failure_count + 1)
        )
        await self._db.commit()

    async def find_similar(
        self,
        intent: str,
        pattern_type: Optional[PatternType] = None,
        min_success_rate: float = 0.6,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Find the best matching patterns for a given intent.
        Returns patterns sorted by success_rate × usage_frequency.
        """
        ih = _intent_hash(intent)

        # Exact match first
        stmt = select(ExecutionPattern).where(ExecutionPattern.intent_hash == ih)
        if pattern_type:
            stmt = stmt.where(ExecutionPattern.pattern_type == pattern_type.value)
        result = await self._db.execute(stmt)
        exact = result.scalars().all()

        # Fallback: top patterns by type sorted by success rate
        if not exact:
            stmt = select(ExecutionPattern).order_by(
                ExecutionPattern.success_count.desc()
            ).limit(limit * 2)
            if pattern_type:
                stmt = stmt.where(ExecutionPattern.pattern_type == pattern_type.value)
            result = await self._db.execute(stmt)
            exact = result.scalars().all()

        scored = []
        for p in exact:
            total = p.success_count + p.failure_count
            success_rate = p.success_count / total if total > 0 else 0.0
            if success_rate < min_success_rate:
                continue
            scored.append({
                "id": p.id,
                "pattern_type": p.pattern_type,
                "intent_summary": p.intent_summary,
                "pattern_data": p.pattern_data,
                "success_count": p.success_count,
                "failure_count": p.failure_count,
                "success_rate": round(success_rate, 3),
                "avg_duration_ms": p.avg_duration_ms,
                "last_used_at": p.last_used_at.isoformat() if p.last_used_at else None,
            })

        scored.sort(key=lambda x: x["success_rate"] * x["success_count"], reverse=True)
        return scored[:limit]

    async def top_patterns(
        self,
        pattern_type: Optional[PatternType] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return the top performing patterns overall."""
        stmt = select(ExecutionPattern).order_by(
            ExecutionPattern.success_count.desc()
        ).limit(limit)
        if pattern_type:
            stmt = stmt.where(ExecutionPattern.pattern_type == pattern_type.value)
        result = await self._db.execute(stmt)
        patterns = result.scalars().all()
        return [
            {
                "id": p.id,
                "pattern_type": p.pattern_type,
                "intent_summary": p.intent_summary,
                "success_count": p.success_count,
                "failure_count": p.failure_count,
                "avg_duration_ms": p.avg_duration_ms,
            }
            for p in patterns
        ]
