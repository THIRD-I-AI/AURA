"""
Base Agent & Core Types
========================
Every agent in AURA inherits from BaseAgent.
An agent receives an AgentContext, calls tools, and returns an AgentResult.
"""
from __future__ import annotations

import uuid
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Callable, Awaitable


# ────────────────────────────────────────────────────────────────────
# Enums & value objects
# ────────────────────────────────────────────────────────────────────

class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    WAITING = "waiting"      # waiting for human approval
    SKIPPED = "skipped"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# ────────────────────────────────────────────────────────────────────
# Context — everything an agent needs to do its work
# ────────────────────────────────────────────────────────────────────

@dataclass
class AgentContext:
    """Immutable bag of data handed to every agent invocation."""
    # What the user originally asked
    user_prompt: str

    # The specific sub-task assigned to this agent
    task_description: str

    # Session-level identifiers
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # Data passed from upstream agents
    upstream_results: Dict[str, Any] = field(default_factory=dict)

    # Known schema / table context (populated by planner or schema agent)
    schema_context: Dict[str, Any] = field(default_factory=dict)

    # Connection details (db_type, host, port, database, etc.)
    connection: Dict[str, Any] = field(default_factory=dict)

    # File references
    files: List[str] = field(default_factory=list)

    # Arbitrary metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Maximum seconds this agent may run
    timeout_seconds: int = 120


# ────────────────────────────────────────────────────────────────────
# Result — what every agent returns
# ────────────────────────────────────────────────────────────────────

@dataclass
class StepLog:
    """One atomic action an agent performed."""
    timestamp: float = field(default_factory=time.time)
    action: str = ""
    tool_name: str = ""
    input_summary: str = ""
    output_summary: str = ""
    duration_ms: float = 0
    severity: Severity = Severity.INFO


@dataclass
class AgentResult:
    status: AgentStatus = AgentStatus.SUCCESS
    output: Dict[str, Any] = field(default_factory=dict)
    steps: List[StepLog] = field(default_factory=list)
    error: Optional[str] = None
    suggestions: List[str] = field(default_factory=list)
    artifacts: Dict[str, Any] = field(default_factory=dict)   # files, SQL, schemas produced
    duration_ms: float = 0

    @property
    def succeeded(self) -> bool:
        return self.status == AgentStatus.SUCCESS

    def add_step(self, action: str, tool_name: str = "",
                 input_summary: str = "", output_summary: str = "",
                 duration_ms: float = 0, severity: Severity = Severity.INFO) -> None:
        self.steps.append(StepLog(
            action=action,
            tool_name=tool_name,
            input_summary=input_summary,
            output_summary=output_summary,
            duration_ms=duration_ms,
            severity=severity,
        ))


# ────────────────────────────────────────────────────────────────────
# Progress callback (streams updates to frontend)
# ────────────────────────────────────────────────────────────────────

ProgressCallback = Callable[[str, str, float], Awaitable[None]]
# signature: (agent_name, message, progress_pct) → None


# ────────────────────────────────────────────────────────────────────
# Base Agent
# ────────────────────────────────────────────────────────────────────

class BaseAgent(ABC):
    """
    Abstract base for every AURA agent.

    Subclasses implement `_run()`.  The public `execute()` wraps it
    with timing, error handling, and progress reporting.
    """

    name: str = "BaseAgent"
    description: str = ""

    def __init__(self, tool_registry: Any = None) -> None:
        self.tools = tool_registry          # ToolRegistry instance
        self._progress_cb: Optional[ProgressCallback] = None

    def set_progress_callback(self, cb: ProgressCallback) -> None:
        self._progress_cb = cb

    async def _report(self, message: str, pct: float = -1) -> None:
        if self._progress_cb:
            await self._progress_cb(self.name, message, pct)

    # ── public entry-point ──────────────────────────────────────────

    async def execute(self, ctx: AgentContext) -> AgentResult:
        start = time.time()
        result = AgentResult(status=AgentStatus.RUNNING)
        try:
            await self._report(f"Starting {self.name}…")
            result = await self._run(ctx, result)
            if result.status == AgentStatus.RUNNING:
                result.status = AgentStatus.SUCCESS
        except Exception as exc:
            result.status = AgentStatus.FAILED
            result.error = str(exc)
            result.add_step(
                action="agent_error",
                output_summary=str(exc),
                severity=Severity.ERROR,
            )
        finally:
            result.duration_ms = (time.time() - start) * 1000
            await self._report(
                f"{'✓' if result.succeeded else '✗'} {self.name} "
                f"({result.duration_ms:.0f} ms)",
                pct=100 if result.succeeded else -1,
            )
        return result

    # ── subclass implements this ────────────────────────────────────

    @abstractmethod
    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        """Do the actual work.  Mutate & return `result`."""
        ...
