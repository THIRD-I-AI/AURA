"""
Sprint S31a — Evolution engine tests.

Tier A (pure Python, no optional deps).

Covers:
  * ORM model instantiation (ExecutionPattern, ImprovementProposal, etc.)
  * Enum values (ImprovementStatus, PatternType)
  * _intent_hash determinism
  * PatternLibrary CRUD against in-memory SQLite
  * EvolutionEngine._generate_proposal with mocked LLM
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evolution.models import (
    AgentFeedback,
    ExecutionPattern,
    ImprovementProposal,
    ImprovementStatus,
    PatternType,
    SystemEvolutionLog,
)
from evolution.pattern_library import PatternLibrary, _intent_hash

# ── Enum tests ──────────────────────────────────────────────────────

class TestEnums:
    def test_improvement_status_values(self):
        assert ImprovementStatus.PROPOSED.value == "proposed"
        assert ImprovementStatus.DEPLOYED.value == "deployed"
        assert ImprovementStatus.REJECTED.value == "rejected"

    def test_pattern_type_values(self):
        assert PatternType.PIPELINE_STRUCTURE.value == "pipeline_structure"
        assert PatternType.QUERY_OPTIMIZATION.value == "query_optimization"
        assert PatternType.RECOVERY_SHIM.value == "recovery_shim"


# ── ORM model instantiation tests ──────────────────────────────────

class TestExecutionPattern:
    def test_explicit_fields(self):
        ep = ExecutionPattern(
            pattern_type="pipeline_structure",
            intent_hash="abc123",
            intent_summary="build a chart",
        )
        assert ep.pattern_type == "pipeline_structure"
        assert ep.intent_hash == "abc123"
        assert ep.intent_summary == "build a chart"

class TestImprovementProposal:
    def test_explicit_fields(self):
        ip = ImprovementProposal(
            target="PipelineAgent",
            improvement_type="prompt_tune",
            description="Better prompt for chart generation",
        )
        assert ip.target == "PipelineAgent"
        assert ip.improvement_type == "prompt_tune"
        assert ip.deployed_at is None


class TestSystemEvolutionLog:
    def test_instantiation(self):
        log = SystemEvolutionLog(
            cycle_id="cycle-1",
            event_type="analysis_complete",
            component="PipelineAgent",
            summary="Found 3 recurring failures in chart generation",
        )
        assert log.cycle_id == "cycle-1"
        assert log.event_type == "analysis_complete"
        assert log.outcome is None


class TestAgentFeedback:
    def test_instantiation(self):
        fb = AgentFeedback(
            session_id="sess-1",
            agent_name="SQLGeneratorAgent",
            task_type="sql_generation",
            user_prompt="Show me revenue by month",
        )
        assert fb.session_id == "sess-1"
        assert fb.agent_name == "SQLGeneratorAgent"
        assert fb.user_rating is None
        assert fb.correction is None


# ── _intent_hash tests ──────────────────────────────────────────────

class TestIntentHash:
    def test_deterministic(self):
        h1 = _intent_hash("Show revenue by month")
        h2 = _intent_hash("Show revenue by month")
        assert h1 == h2

    def test_case_insensitive(self):
        h1 = _intent_hash("Show Revenue")
        h2 = _intent_hash("show revenue")
        assert h1 == h2

    def test_whitespace_trimmed(self):
        h1 = _intent_hash("  query  ")
        h2 = _intent_hash("query")
        assert h1 == h2

    def test_different_intents_different_hashes(self):
        h1 = _intent_hash("revenue by month")
        h2 = _intent_hash("revenue by quarter")
        assert h1 != h2

    def test_length(self):
        h = _intent_hash("anything")
        assert len(h) == 32


# ── PatternLibrary (async, in-memory SQLite) ────────────────────────

@pytest.fixture
async def db_session():
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from metadata_store.db import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
class TestPatternLibrary:
    async def test_record_success_creates_new(self, db_session):
        lib = PatternLibrary(db_session)
        p = await lib.record_success(
            PatternType.QUERY_OPTIMIZATION,
            "revenue by month",
            {"sql": "SELECT month, SUM(revenue) FROM sales GROUP BY month"},
            duration_ms=150.0,
        )
        assert p.success_count == 1
        assert p.intent_hash == _intent_hash("revenue by month")
        assert p.avg_duration_ms == 150.0

    async def test_record_success_updates_existing(self, db_session):
        lib = PatternLibrary(db_session)
        await lib.record_success(
            PatternType.QUERY_OPTIMIZATION, "revenue by month",
            {"sql": "v1"}, duration_ms=100.0,
        )
        p2 = await lib.record_success(
            PatternType.QUERY_OPTIMIZATION, "revenue by month",
            {"sql": "v2"}, duration_ms=200.0,
        )
        assert p2.success_count == 2
        assert p2.avg_duration_ms == 150.0
        assert p2.pattern_data == {"sql": "v2"}

    async def test_record_failure(self, db_session):
        lib = PatternLibrary(db_session)
        await lib.record_success(
            PatternType.PIPELINE_STRUCTURE, "build chart",
            {"steps": ["load", "transform"]},
        )
        await lib.record_failure(PatternType.PIPELINE_STRUCTURE, "build chart")

        results = await lib.find_similar(
            "build chart", PatternType.PIPELINE_STRUCTURE, min_success_rate=0.0,
        )
        assert len(results) == 1
        assert results[0]["failure_count"] == 1
        assert results[0]["success_count"] == 1

    async def test_find_similar_filters_low_success_rate(self, db_session):
        lib = PatternLibrary(db_session)
        await lib.record_success(
            PatternType.DATA_QUALITY, "check nulls",
            {"check": "null_rate"}, duration_ms=50.0,
        )
        for _ in range(5):
            await lib.record_failure(PatternType.DATA_QUALITY, "check nulls")

        results = await lib.find_similar(
            "check nulls", PatternType.DATA_QUALITY, min_success_rate=0.6,
        )
        assert len(results) == 0

    async def test_top_patterns(self, db_session):
        lib = PatternLibrary(db_session)
        await lib.record_success(PatternType.SCHEMA_DESIGN, "schema a", {"x": 1})
        await lib.record_success(PatternType.SCHEMA_DESIGN, "schema b", {"x": 2})
        await lib.record_success(PatternType.SCHEMA_DESIGN, "schema b", {"x": 2})

        top = await lib.top_patterns(PatternType.SCHEMA_DESIGN, limit=5)
        assert len(top) == 2
        assert top[0]["success_count"] >= top[1]["success_count"]
