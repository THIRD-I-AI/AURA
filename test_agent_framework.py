"""
test_agent_framework.py
========================
Offline integration test for the AURA Agentic DE framework.
Verifies PlannerAgent, DAGExecutor, ToolRegistry, and AgentMemory
without requiring live microservices.

Run: python -m pytest test_agent_framework.py -v
  or: python test_agent_framework.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import json

# Ensure aurabackend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "aurabackend"))

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent
from agents.tool_registry import ToolRegistry, Tool
from agents.memory import AgentMemory
from agents.planner import PlannerAgent, ExecutionPlan, TaskNode, TaskType
from agents.executor import DAGExecutor


# ── Helpers ───────────────────────────────────────────────────────────

def _make_ctx(prompt: str = "Load sales.csv, clean nulls, build a summary report") -> AgentContext:
    return AgentContext(
        user_prompt=prompt,
        task_description=prompt,
        session_id="test-session-001",
        files=["sales.csv"],
        metadata={"execute": False},
    )


# ── Test: ToolRegistry ────────────────────────────────────────────────

def test_tool_registry_register_and_call():
    """Register a tool and call it."""
    reg = ToolRegistry()
    call_log: list[str] = []

    async def dummy_tool(*, value: str) -> str:
        call_log.append(value)
        return f"echoed: {value}"

    reg.register(Tool(name="echo", fn=dummy_tool, description="Echo a value", category="test"))

    assert "echo" in reg.tool_names()

    result = asyncio.run(reg.call("echo", value="hello"))
    assert result == "echoed: hello"
    assert call_log == ["hello"]
    assert len(reg.history) == 1
    print("  ✓ ToolRegistry register + call")


def test_tool_registry_dry_run():
    """Dry-run mode should NOT execute the function."""
    reg = ToolRegistry(dry_run=True)

    async def fail_tool() -> str:
        raise RuntimeError("Should not be called")

    reg.register(Tool(name="boom", fn=fail_tool, description="Fails", category="test"))
    result = asyncio.run(reg.call("boom"))
    # Dry-run returns a string marker instead of calling the function
    assert "DRY RUN" in str(result)
    print("  ✓ ToolRegistry dry-run mode")


# ── Test: AgentMemory ─────────────────────────────────────────────────

def test_memory_basic():
    mem = AgentMemory(max_entries=5)
    mem.add(role="user", content="Load file", agent_name="test")
    mem.add(role="agent", content="File loaded", agent_name="IngestionAgent")
    assert len(mem.entries()) == 2
    assert "File loaded" in mem.as_text()

    mem.set_fact("row_count", 1000)
    assert mem.get_fact("row_count") == 1000

    mem.cache_schema("sales", {"columns": ["id", "amount"]})
    assert mem.get_schema("sales") is not None

    # Max-entries eviction
    for i in range(10):
        mem.add(role="system", content=f"msg-{i}", agent_name="test")
    assert len(mem.entries()) <= 5
    print("  ✓ AgentMemory basics + eviction")


# ── Test: PlannerAgent (fallback mode — no LLM key) ──────────────────

def test_planner_fallback():
    """Without a Gemini API key, the planner should use heuristic fallback."""
    # Temporarily unset keys to force fallback
    saved_keys = {}
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        saved_keys[key] = os.environ.pop(key, None)

    try:
        planner = PlannerAgent()
        ctx = _make_ctx("Load sales.csv, clean nulls, build a summary report")

        result = asyncio.run(planner.execute(ctx))
        assert result.status == AgentStatus.SUCCESS, f"Planner failed: {result.error}"
        assert "plan" in result.artifacts

        plan: ExecutionPlan = result.artifacts["plan"]
        assert len(plan.tasks) >= 2, f"Expected ≥2 tasks, got {len(plan.tasks)}"
        assert plan.summary, "Plan should have a summary"

        # Verify topological ordering (no task depends on a later task)
        task_ids_seen: set[str] = set()
        for task in plan.tasks:
            for dep in task.depends_on:
                assert dep in task_ids_seen, f"Task {task.id} depends on {dep} which hasn't appeared yet"
            task_ids_seen.add(task.id)

        print(f"  ✓ PlannerAgent fallback — {len(plan.tasks)} tasks, summary: {plan.summary[:60]}")
    finally:
        for key, val in saved_keys.items():
            if val is not None:
                os.environ[key] = val


# ── Test: DAGExecutor dry-run ─────────────────────────────────────────

def test_executor_dry_run():
    """Run the full executor without any tool registry (dry-run)."""
    saved_keys = {}
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        saved_keys[key] = os.environ.pop(key, None)

    progress_log: list[str] = []

    async def track_progress(message: str, agent: str, pct: float) -> None:
        progress_log.append(f"[{agent}] {message}")

    try:
        planner = PlannerAgent()
        ctx = _make_ctx("Ingest users.csv, add email validation, optimize queries")
        async def _run_executor():
            plan_result = await planner.execute(ctx)
            assert plan_result.status == AgentStatus.SUCCESS
            plan = plan_result.artifacts["plan"]

            executor = DAGExecutor(
                tool_registry=None,
                progress_cb=track_progress,
            )

            return await executor.execute(
                plan=plan,
                user_prompt=ctx.user_prompt,
                files=ctx.files,
            )

        report = asyncio.run(_run_executor())

        assert report.duration_ms > 0
        assert len(report.task_results) > 0, "Should have at least one task result"
        assert len(progress_log) > 0, "Progress callback should have been called"

        print(f"  ✓ DAGExecutor dry-run — {report.summary}")
        print(f"    Progress events: {len(progress_log)}")
    finally:
        for key, val in saved_keys.items():
            if val is not None:
                os.environ[key] = val


# ── Test: Plan serialisation ──────────────────────────────────────────

def test_plan_to_dict():
    """Plans should serialise to JSON-safe dicts."""
    saved_keys = {}
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        saved_keys[key] = os.environ.pop(key, None)

    try:
        planner = PlannerAgent()
        ctx = _make_ctx()
        result = asyncio.run(planner.execute(ctx))
        plan = result.artifacts["plan"]

        d = plan.to_dict()
        serialised = json.dumps(d)
        assert '"plan_id"' in serialised
        assert '"tasks"' in serialised
        print("  ✓ ExecutionPlan.to_dict() serialisable")
    finally:
        for key, val in saved_keys.items():
            if val is not None:
                os.environ[key] = val


# ── Test: tool implementations register ───────────────────────────────

def test_tool_implementations_register():
    """All concrete tools should register without import errors."""
    from agents.tools import register_all_tools
    reg = ToolRegistry()
    register_all_tools(reg)

    names = reg.tool_names()
    assert "execute_sql" in names
    assert "ingest_and_profile" in names
    assert "introspect_database" in names
    assert "create_schedule" in names
    assert len(names) >= 7, f"Expected ≥7 tools, got {len(names)}"
    print(f"  ✓ register_all_tools — {len(names)} tools registered: {', '.join(names)}")


# ── Runner ────────────────────────────────────────────────────────────

def main() -> None:
    print("\n🧪 AURA Agent Framework — Offline Integration Tests\n")

    tests = [
        test_tool_registry_register_and_call,
        test_tool_registry_dry_run,
        test_memory_basic,
        test_planner_fallback,
        test_executor_dry_run,
        test_plan_to_dict,
        test_tool_implementations_register,
    ]

    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  ✗ {test_fn.__name__}: {e}")

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")
    if failed:
        sys.exit(1)
    else:
        print("✅ All tests passed!\n")


if __name__ == "__main__":
    main()
