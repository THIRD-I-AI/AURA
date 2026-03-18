"""
Tool Registry
==============
Agents don't call external systems directly — they go through registered tools.
This gives us logging, rate-limiting, dry-run mode, and permission checks for free.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional


@dataclass
class Tool:
    """One callable tool an agent can invoke."""
    name: str
    description: str
    category: str                               # "sql", "file", "schema", "transform", "quality"
    fn: Callable[..., Awaitable[Any]]           # the actual async function
    requires_approval: bool = False             # human-in-the-loop gate
    is_destructive: bool = False                # DROP / DELETE / TRUNCATE
    parameters_schema: Dict[str, Any] = field(default_factory=dict)

    async def __call__(self, **kwargs: Any) -> Any:
        return await self.fn(**kwargs)


@dataclass
class ToolCallRecord:
    tool_name: str
    input_args: Dict[str, Any]
    output: Any
    duration_ms: float
    timestamp: float = field(default_factory=time.time)
    approved: bool = True


class ToolRegistry:
    """
    Central catalog of tools available to agents.

    Usage:
        registry = ToolRegistry()
        registry.register(Tool(name="execute_sql", ...))
        result = await registry.call("execute_sql", query="SELECT 1")
    """

    def __init__(self, dry_run: bool = False) -> None:
        self._tools: Dict[str, Tool] = {}
        self._history: List[ToolCallRecord] = []
        self.dry_run = dry_run                      # if True, tools log but don't execute

    # ── registration ────────────────────────────────────────────────

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def register_many(self, tools: List[Tool]) -> None:
        for t in tools:
            self.register(t)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def list_tools(self, category: Optional[str] = None) -> List[Tool]:
        tools = list(self._tools.values())
        if category:
            tools = [t for t in tools if t.category == category]
        return tools

    def tool_names(self) -> List[str]:
        return list(self._tools.keys())

    def describe_tools(self, category: Optional[str] = None) -> str:
        """Return a plain-text catalogue the LLM can read."""
        lines: List[str] = []
        for tool in self.list_tools(category):
            flag = " [DESTRUCTIVE]" if tool.is_destructive else ""
            flag += " [NEEDS APPROVAL]" if tool.requires_approval else ""
            lines.append(f"• {tool.name}{flag}: {tool.description}")
        return "\n".join(lines) if lines else "(no tools registered)"

    # ── invocation ──────────────────────────────────────────────────

    async def call(self, name: str, **kwargs: Any) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Tool '{name}' not found. Available: {self.tool_names()}")

        if self.dry_run:
            record = ToolCallRecord(
                tool_name=name,
                input_args=kwargs,
                output="[DRY RUN — not executed]",
                duration_ms=0,
                approved=True,
            )
            self._history.append(record)
            return record.output

        start = time.time()
        output = await tool.fn(**kwargs)
        elapsed = (time.time() - start) * 1000

        self._history.append(ToolCallRecord(
            tool_name=name,
            input_args=kwargs,
            output=output,
            duration_ms=elapsed,
        ))
        return output

    # ── audit ───────────────────────────────────────────────────────

    @property
    def history(self) -> List[ToolCallRecord]:
        return list(self._history)

    def last_call(self) -> Optional[ToolCallRecord]:
        return self._history[-1] if self._history else None
