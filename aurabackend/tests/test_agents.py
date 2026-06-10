"""
AURA Agent Framework Unit Tests
================================
Tests for the agentic DE framework: PlannerAgent fallback plans,
ExecutionPlan dependency resolution, AgentResult, and DAGExecutor.
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent, Severity
from agents.planner import ExecutionPlan, PlannerAgent, TaskNode, TaskType

# ── AgentResult Tests ────────────────────────────────────────────────

class TestAgentResult:
    def test_default_status(self):
        result = AgentResult()
        assert result.status == AgentStatus.SUCCESS
        assert result.succeeded is True

    def test_failed_status(self):
        result = AgentResult(status=AgentStatus.FAILED, error="boom")
        assert result.succeeded is False
        assert result.error == "boom"

    def test_add_step(self):
        result = AgentResult()
        result.add_step(action="test_action", tool_name="tool", output_summary="did things")
        assert len(result.steps) == 1
        assert result.steps[0].action == "test_action"
        assert result.steps[0].tool_name == "tool"

    def test_step_severity(self):
        result = AgentResult()
        result.add_step(action="error_action", severity=Severity.ERROR)
        assert result.steps[0].severity == Severity.ERROR


# ── PlannerAgent Fallback Plan Tests ─────────────────────────────────

class TestPlannerFallback:
    def _make_ctx(self, prompt: str, files=None) -> AgentContext:
        return AgentContext(
            user_prompt=prompt,
            task_description="test",
            files=files or [],
        )

    def test_sql_default(self):
        """Default plan: just SQL generation when no keywords match."""
        ctx = self._make_ctx("What is the total revenue?")
        plan = PlannerAgent._fallback_plan(ctx)
        assert len(plan["tasks"]) == 1
        assert plan["tasks"][0]["task_type"] == "generate_sql"
        assert plan["tasks"][0]["agent_name"] == "SQLGeneratorAgent"

    def test_ingest_from_files(self):
        """Files present → ingest task."""
        ctx = self._make_ctx("upload data", files=["sales.csv"])
        plan = PlannerAgent._fallback_plan(ctx)
        assert plan["tasks"][0]["task_type"] == "ingest"
        assert plan["tasks"][0]["agent_name"] == "IngestionAgent"

    def test_ingest_from_keywords(self):
        """Keyword 'csv' triggers ingest even without files."""
        ctx = self._make_ctx("load csv data")
        plan = PlannerAgent._fallback_plan(ctx)
        assert plan["tasks"][0]["task_type"] == "ingest"

    def test_transform_adds_quality(self):
        """Transform always adds a quality check after it."""
        ctx = self._make_ctx("transform and clean the data")
        plan = PlannerAgent._fallback_plan(ctx)
        types = [t["task_type"] for t in plan["tasks"]]
        assert "transform" in types
        assert "quality_check" in types
        # Quality check should come after transform
        assert types.index("quality_check") > types.index("transform")

    def test_pipeline_keywords(self):
        """Pipeline keywords trigger pipeline_build."""
        ctx = self._make_ctx("schedule a recurring pipeline")
        plan = PlannerAgent._fallback_plan(ctx)
        types = [t["task_type"] for t in plan["tasks"]]
        assert "pipeline_build" in types

    def test_optimize_keywords(self):
        """Optimization keywords trigger optimize."""
        ctx = self._make_ctx("optimize query performance with indexes")
        plan = PlannerAgent._fallback_plan(ctx)
        types = [t["task_type"] for t in plan["tasks"]]
        assert "optimize" in types

    def test_schema_keywords(self):
        """Schema keywords trigger schema_design."""
        ctx = self._make_ctx("create table for user data")
        plan = PlannerAgent._fallback_plan(ctx)
        types = [t["task_type"] for t in plan["tasks"]]
        assert "schema_design" in types

    def test_dependency_chain(self):
        """Complex prompt produces chained dependencies."""
        ctx = self._make_ctx("ingest csv, transform, and optimize")
        plan = PlannerAgent._fallback_plan(ctx)
        # Each task except the first should depend on the previous
        for i, task in enumerate(plan["tasks"]):
            if i > 0:
                assert len(task["depends_on"]) > 0, f"Task {task['id']} has no dependencies"

    def test_plan_has_summary(self):
        """Fallback plans always have a summary."""
        ctx = self._make_ctx("hello")
        plan = PlannerAgent._fallback_plan(ctx)
        assert "summary" in plan
        assert "estimated_duration_sec" in plan
        assert plan["estimated_duration_sec"] > 0


# ── ExecutionPlan Dependency Resolution ──────────────────────────────

class TestExecutionPlan:
    def test_ready_tasks_no_deps(self):
        """Tasks with no dependencies are immediately ready."""
        plan = ExecutionPlan(
            plan_id="test",
            user_prompt="test",
            tasks=[
                TaskNode(id="t1", task_type=TaskType.GENERATE_SQL, description="gen", agent_name="SQLGeneratorAgent"),
                TaskNode(id="t2", task_type=TaskType.EXECUTE_SQL, description="exec", agent_name="ExecutionAgent", depends_on=["t1"]),
            ],
        )
        ready = plan.ready_tasks(set())
        assert len(ready) == 1
        assert ready[0].id == "t1"

    def test_ready_tasks_after_completion(self):
        """Tasks become ready when dependencies complete."""
        plan = ExecutionPlan(
            plan_id="test",
            user_prompt="test",
            tasks=[
                TaskNode(id="t1", task_type=TaskType.GENERATE_SQL, description="gen", agent_name="SQLGeneratorAgent"),
                TaskNode(id="t2", task_type=TaskType.EXECUTE_SQL, description="exec", agent_name="ExecutionAgent", depends_on=["t1"]),
                TaskNode(id="t3", task_type=TaskType.TRANSFORM, description="viz", agent_name="VisualizationAgent", depends_on=["t2"]),
            ],
        )
        # After t1 completes
        ready = plan.ready_tasks({"t1"})
        ids = [t.id for t in ready]
        assert "t2" in ids
        assert "t3" not in ids

    def test_parallel_tasks(self):
        """Tasks with the same dependency become ready together."""
        plan = ExecutionPlan(
            plan_id="test",
            user_prompt="test",
            tasks=[
                TaskNode(id="t1", task_type=TaskType.GENERATE_SQL, description="gen", agent_name="SQLGeneratorAgent"),
                TaskNode(id="t2", task_type=TaskType.TRANSFORM, description="viz", agent_name="VisualizationAgent", depends_on=["t1"]),
                TaskNode(id="t3", task_type=TaskType.TRANSFORM, description="analysis", agent_name="AnalysisAgent", depends_on=["t1"]),
            ],
        )
        # Simulate t1 completed by marking its status
        plan.tasks[0].status = AgentStatus.SUCCESS
        ready = plan.ready_tasks({"t1"})
        ids = {t.id for t in ready}
        assert ids == {"t2", "t3"}

    def test_to_dict(self):
        """ExecutionPlan serializes correctly."""
        plan = ExecutionPlan(
            plan_id="abc",
            user_prompt="test",
            summary="test plan",
            tasks=[
                TaskNode(id="t1", task_type=TaskType.GENERATE_SQL, description="gen", agent_name="SQLGeneratorAgent"),
            ],
        )
        d = plan.to_dict()
        assert d["plan_id"] == "abc"
        assert d["summary"] == "test plan"
        assert len(d["tasks"]) == 1
        assert d["tasks"][0]["task_type"] == "generate_sql"


# ── AgentContext Tests ───────────────────────────────────────────────

class TestAgentContext:
    def test_default_timeout(self):
        ctx = AgentContext(user_prompt="test", task_description="test")
        assert ctx.timeout_seconds == 120  # default from env

    def test_session_id_generated(self):
        ctx = AgentContext(user_prompt="test", task_description="test")
        assert ctx.session_id  # auto-generated
        assert len(ctx.session_id) == 12


# ── BaseAgent Contract Tests ─────────────────────────────────────────

class DummyAgent(BaseAgent):
    name = "DummyAgent"

    async def _run(self, ctx, result):
        result.output = {"answer": 42}
        return result


class FailingAgent(BaseAgent):
    name = "FailingAgent"

    async def _run(self, ctx, result):
        raise ValueError("intentional failure")


class TestBaseAgent:
    def test_successful_execution(self):
        agent = DummyAgent()
        ctx = AgentContext(user_prompt="test", task_description="test")
        result = asyncio.run(agent.execute(ctx))
        assert result.succeeded
        assert result.output["answer"] == 42
        assert result.duration_ms > 0

    def test_failed_execution(self):
        agent = FailingAgent()
        ctx = AgentContext(user_prompt="test", task_description="test")
        result = asyncio.run(agent.execute(ctx))
        assert result.status == AgentStatus.FAILED
        assert "intentional failure" in result.error
        assert len(result.steps) >= 1  # error step logged


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
