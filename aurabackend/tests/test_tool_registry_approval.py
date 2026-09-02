"""
BUG-026 regression tests — ToolRegistry.call() must actually enforce
requires_approval/is_destructive instead of only rendering them as
describe_tools() display badges.
"""
from __future__ import annotations

import pytest

from agents.tool_registry import Tool, ToolApprovalRequiredError, ToolRegistry


async def _destructive_fn(*, table: str) -> str:
    return f"dropped {table}"


async def _sql_like_fn(*, query: str, approved: bool = True) -> dict:
    """Mimics agents.tools.execute_sql's shape: accepts an `approved` kwarg
    and reports whatever value it was actually called with."""
    return {"query": query, "approved": approved}


def _registry_with_destructive_tool() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(Tool(
        name="drop_table",
        fn=_destructive_fn,
        description="Drop a table.",
        category="schema",
        is_destructive=True,
    ))
    reg.register(Tool(
        name="needs_approval_tool",
        fn=_destructive_fn,
        description="A non-destructive tool that still needs a human sign-off.",
        category="schema",
        requires_approval=True,
    ))
    reg.register(Tool(
        name="safe_tool",
        fn=_destructive_fn,
        description="An ordinary tool with neither flag set.",
        category="schema",
    ))
    reg.register(Tool(
        name="execute_sql_like",
        fn=_sql_like_fn,
        description="Shaped like agents.tools.execute_sql.",
        category="execution",
    ))
    return reg


@pytest.mark.asyncio
async def test_destructive_tool_does_not_execute_without_approval():
    reg = _registry_with_destructive_tool()
    with pytest.raises(ToolApprovalRequiredError):
        await reg.call("drop_table", table="users")
    # never reached tool.fn, so nothing was recorded as executed
    last = reg.last_call()
    assert last is not None
    assert last.approved is False
    assert last.output is None


@pytest.mark.asyncio
async def test_requires_approval_tool_does_not_execute_without_approval():
    reg = _registry_with_destructive_tool()
    with pytest.raises(ToolApprovalRequiredError):
        await reg.call("needs_approval_tool", table="users")


@pytest.mark.asyncio
async def test_destructive_tool_executes_once_approved():
    reg = _registry_with_destructive_tool()
    result = await reg.call("drop_table", table="users", approved=True)
    assert result == "dropped users"
    last = reg.last_call()
    assert last.approved is True


@pytest.mark.asyncio
async def test_unflagged_tool_runs_without_needing_approval():
    reg = _registry_with_destructive_tool()
    result = await reg.call("safe_tool", table="reports")
    assert result == "dropped reports"


@pytest.mark.asyncio
async def test_dry_run_also_respects_the_approval_gate():
    reg = _registry_with_destructive_tool()
    reg.dry_run = True
    with pytest.raises(ToolApprovalRequiredError):
        await reg.call("drop_table", table="users")


@pytest.mark.asyncio
async def test_resolved_approval_forwarded_to_tool_fn_when_it_accepts_one():
    """Mirrors the execute_sql fix: the registry's own approval decision is
    what reaches the tool function, not a value the tool hardcodes itself."""
    reg = _registry_with_destructive_tool()
    result = await reg.call("execute_sql_like", query="SELECT 1")
    assert result == {"query": "SELECT 1", "approved": True}
