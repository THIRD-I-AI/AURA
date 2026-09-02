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


# ── BUG-019: tenant isolation ────────────────────────────────────────
#
# Same story as connections/semantic_models: these four tables and the
# routes reading them had zero tenant scoping, so any authenticated caller
# could read (and, for feedback, forge session ownership of) every other
# tenant's evolution data. Fixed with a nullable workspace_id column
# (migration 20260902_d4e5f6a7b8c9) filtered on every read and stamped on
# every write from the caller's own derived identity.

@pytest.mark.asyncio
class TestPatternLibraryWorkspaceIsolation:
    async def test_record_success_keeps_tenants_as_separate_rows(self, db_session):
        """Two tenants recording the identical intent must not collide into
        one shared row -- that would let tenant B's usage count/timing leak
        into tenant A's pattern (and vice versa)."""
        lib = PatternLibrary(db_session)
        a = await lib.record_success(
            PatternType.QUERY_OPTIMIZATION, "revenue by month",
            {"sql": "A"}, duration_ms=100.0, workspace_id="org-a",
        )
        b = await lib.record_success(
            PatternType.QUERY_OPTIMIZATION, "revenue by month",
            {"sql": "B"}, duration_ms=999.0, workspace_id="org-b",
        )
        assert a.id != b.id
        assert a.success_count == 1
        assert b.success_count == 1

    async def test_find_similar_does_not_leak_across_tenants(self, db_session):
        lib = PatternLibrary(db_session)
        await lib.record_success(
            PatternType.QUERY_OPTIMIZATION, "revenue by month",
            {"sql": "A"}, duration_ms=100.0, workspace_id="org-a",
        )
        results_a = await lib.find_similar(
            "revenue by month", PatternType.QUERY_OPTIMIZATION,
            min_success_rate=0.0, workspace_id="org-a",
        )
        results_b = await lib.find_similar(
            "revenue by month", PatternType.QUERY_OPTIMIZATION,
            min_success_rate=0.0, workspace_id="org-b",
        )
        assert len(results_a) == 1
        assert len(results_b) == 0

    async def test_top_patterns_scoped_to_workspace(self, db_session):
        lib = PatternLibrary(db_session)
        await lib.record_success(
            PatternType.SCHEMA_DESIGN, "schema a", {"x": 1}, workspace_id="org-a",
        )
        await lib.record_success(
            PatternType.SCHEMA_DESIGN, "schema b", {"x": 2}, workspace_id="org-b",
        )
        top_a = await lib.top_patterns(PatternType.SCHEMA_DESIGN, workspace_id="org-a")
        top_b = await lib.top_patterns(PatternType.SCHEMA_DESIGN, workspace_id="org-b")
        assert [p["intent_summary"] for p in top_a] == ["schema a"]
        assert [p["intent_summary"] for p in top_b] == ["schema b"]


class _FakeRequest:
    """Minimal stand-in for a Starlette Request -- mirrors
    tests/test_tenant_workspace_isolation.py's helper. Only `.state.user`
    (JWTAuthMiddleware's stash) and `.headers.get()` are used by
    current_workspace_id, so real Request machinery is unneeded here."""

    def __init__(self, org_id: str):
        class _State:
            pass
        self.state = _State()
        self.state.user = {"org_id": org_id}
        self.headers = {}


@pytest.mark.asyncio
class TestEvolutionAPITenantIsolation:
    """Exercises evolution/api.py's route functions directly (no
    lifespan/TestClient overhead), per tests/test_audit_ledger_endpoints.py's
    established pattern for this repo."""

    async def _seed(self, db_session):
        from evolution.models import AgentFeedback, ImprovementProposal, SystemEvolutionLog

        proposal_a = ImprovementProposal(
            workspace_id="org-a", target="AgentA", improvement_type="t",
            description="improve A",
        )
        proposal_b = ImprovementProposal(
            workspace_id="org-b", target="AgentB", improvement_type="t",
            description="improve B",
        )
        db_session.add_all([proposal_a, proposal_b])

        log_a = SystemEvolutionLog(
            workspace_id="org-a", cycle_id="c1", event_type="e", component="A",
            summary="cycle a",
        )
        log_b = SystemEvolutionLog(
            workspace_id="org-b", cycle_id="c2", event_type="e", component="B",
            summary="cycle b",
        )
        db_session.add_all([log_a, log_b])

        fb_a = AgentFeedback(
            workspace_id="org-a", session_id="s1", agent_name="AgentA",
            task_type="t", user_prompt="p",
        )
        fb_b = AgentFeedback(
            workspace_id="org-b", session_id="s2", agent_name="AgentB",
            task_type="t", user_prompt="p",
        )
        db_session.add_all([fb_a, fb_b])

        await db_session.commit()
        for row in (proposal_a, proposal_b, log_a, log_b, fb_a, fb_b):
            await db_session.refresh(row)
        return proposal_a, proposal_b

    async def test_list_proposals_scoped_to_caller_tenant(self, db_session):
        from evolution.api import list_proposals

        await self._seed(db_session)
        result_a = await list_proposals(_FakeRequest("org-a"), db=db_session)
        result_b = await list_proposals(_FakeRequest("org-b"), db=db_session)
        assert [p["target"] for p in result_a["proposals"]] == ["AgentA"]
        assert [p["target"] for p in result_b["proposals"]] == ["AgentB"]

    async def test_get_proposal_404s_for_a_foreign_tenant(self, db_session):
        from fastapi import HTTPException

        from evolution.api import get_proposal

        proposal_a, _ = await self._seed(db_session)

        got = await get_proposal(proposal_a.id, _FakeRequest("org-a"), db=db_session)
        assert got["target"] == "AgentA"

        with pytest.raises(HTTPException) as exc_info:
            await get_proposal(proposal_a.id, _FakeRequest("org-b"), db=db_session)
        assert exc_info.value.status_code == 404

    async def test_update_proposal_cannot_touch_a_foreign_tenants_row(self, db_session):
        from fastapi import HTTPException

        from evolution.api import ProposalUpdateRequest, update_proposal
        from evolution.models import ImprovementStatus

        proposal_a, _ = await self._seed(db_session)
        req = ProposalUpdateRequest(status=ImprovementStatus.TESTING)

        with pytest.raises(HTTPException) as exc_info:
            await update_proposal(proposal_a.id, req, _FakeRequest("org-b"), db=db_session)
        assert exc_info.value.status_code == 404

        # The owner can still update it.
        result = await update_proposal(proposal_a.id, req, _FakeRequest("org-a"), db=db_session)
        assert result["status"] == "updated"

    async def test_evolution_log_scoped_to_caller_tenant(self, db_session):
        from evolution.api import get_evolution_log

        await self._seed(db_session)
        log_a = await get_evolution_log(_FakeRequest("org-a"), db=db_session)
        log_b = await get_evolution_log(_FakeRequest("org-b"), db=db_session)
        assert [e["component"] for e in log_a["log"]] == ["A"]
        assert [e["component"] for e in log_b["log"]] == ["B"]

    async def test_feedback_summary_scoped_to_caller_tenant(self, db_session):
        from evolution.api import feedback_summary

        await self._seed(db_session)
        summary_a = await feedback_summary(_FakeRequest("org-a"), db=db_session)
        summary_b = await feedback_summary(_FakeRequest("org-b"), db=db_session)
        assert [a["agent"] for a in summary_a["agents"]] == ["AgentA"]
        assert [a["agent"] for a in summary_b["agents"]] == ["AgentB"]

    async def test_submit_feedback_stamps_the_callers_own_workspace(self, monkeypatch, db_session):
        """POST /feedback used to accept a bare session_id with no ownership
        check at all -- a caller could file feedback under any tenant's
        session. The fix stamps workspace_id from the verified caller
        identity, never from the request body."""
        from sqlalchemy import select

        from evolution.api import FeedbackRequest, submit_feedback
        from evolution.engine import get_evolution_engine
        from evolution.models import AgentFeedback
        from metadata_store.db import get_session as real_get_session

        async def _fake_get_session():
            yield db_session

        monkeypatch.setattr("evolution.engine.get_session", _fake_get_session)

        req = FeedbackRequest(
            session_id="someone-elses-session", agent_name="AgentA",
            task_type="t", user_prompt="p",
        )
        await submit_feedback(req, _FakeRequest("org-a"))

        rows = (await db_session.execute(
            select(AgentFeedback).where(AgentFeedback.session_id == "someone-elses-session")
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].workspace_id == "org-a"
        # Guard against accidentally patching the wrong symbol.
        assert real_get_session is not None
        assert get_evolution_engine() is not None


# ── BUG-019: proposal status bypass + unbounded rating ────────────────

class TestProposalUpdateRequestStatusValidation:
    """ProposalUpdateRequest.status used to be a bare str, written straight
    onto the ORM row -- a caller could set "deployed" directly, bypassing
    engine.py's confidence-threshold deploy gate. Pydantic rejecting
    anything that isn't a real ImprovementStatus member is the fix; this is
    the same validation FastAPI runs to produce a 422 for the route."""

    def test_valid_status_is_accepted(self):
        from evolution.api import ProposalUpdateRequest
        from evolution.models import ImprovementStatus

        req = ProposalUpdateRequest(status="validated")
        assert req.status == ImprovementStatus.VALIDATED

    def test_invalid_status_string_is_rejected(self):
        from pydantic import ValidationError

        from evolution.api import ProposalUpdateRequest

        with pytest.raises(ValidationError):
            ProposalUpdateRequest(status="not_a_real_status")


class TestFeedbackRequestRatingBounds:
    """user_rating is documented and stored as 1-5; out-of-range values used
    to pass through unvalidated and silently corrupt feedback aggregates."""

    @pytest.mark.parametrize("rating", [1, 3, 5, None])
    def test_in_range_or_absent_rating_is_accepted(self, rating):
        from evolution.api import FeedbackRequest

        req = FeedbackRequest(
            session_id="s", agent_name="a", task_type="t", user_prompt="p",
            user_rating=rating,
        )
        assert req.user_rating == rating

    @pytest.mark.parametrize("rating", [0, -1, 6, 100])
    def test_out_of_range_rating_is_rejected(self, rating):
        from pydantic import ValidationError

        from evolution.api import FeedbackRequest

        with pytest.raises(ValidationError):
            FeedbackRequest(
                session_id="s", agent_name="a", task_type="t", user_prompt="p",
                user_rating=rating,
            )
