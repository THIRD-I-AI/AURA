"""
Base Agent & Core Types
========================
Every agent in AURA inherits from BaseAgent.
An agent receives an AgentContext, calls tools, and returns an AgentResult.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypedDict


class AgentContextMetadata(TypedDict, total=False):
    """Typed structure for ``AgentContext.metadata``.

    Every key is optional (``total=False``) — the bag accumulates state
    as the agent pipeline runs. Keys are documented inline at each
    callsite, but the central catalogue lives here.
    """
    # Budget tracking — populated by BaseAgent.execute() before _run()
    budget_status: Dict[str, Any]

    # DAR research agent dispatch (mode + per-mode inputs)
    dar_mode: str
    table_name: str
    profile_text: str
    question: str
    sql: str
    rows: List[Dict[str, Any]]

    # Orchestrator routing flags + the shared DuckDB handle for the run
    skip_planner: bool
    skip_analysis: bool
    duckdb_con: Any

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

    # Arbitrary metadata — see AgentContextMetadata for known keys.
    metadata: AgentContextMetadata = field(default_factory=lambda: AgentContextMetadata())

    # Maximum seconds this agent may run
    timeout_seconds: int = int(os.getenv("AURA_AGENT_TIMEOUT", "120"))


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
    with timing, error handling, retry, and progress reporting.

    Class attributes for subclasses to override:
        llm_model_env:  env var name whose value (if set) overrides the
                        default LLM model. Lets each agent prefer a
                        cheaper/stronger model without re-importing get_llm.
        retry_on_failure: when True, ``execute`` re-runs ``_run`` up to
                        ``retry_attempts`` times with exponential backoff.
                        Opt-in because most agents are deterministic and
                        retrying just hides bugs.
    """

    name: str = "BaseAgent"
    description: str = ""

    # Cross-cutting knobs (override in subclasses)
    llm_model_env: Optional[str] = None
    retry_on_failure: bool = False
    retry_attempts: int = 3
    retry_initial_delay_seconds: float = 0.5

    def __init__(self, tool_registry: Any = None) -> None:
        self.tools = tool_registry          # ToolRegistry instance
        self._progress_cb: Optional[ProgressCallback] = None

        # Single shared LLM handle per agent instance. get_llm() already
        # caches by (provider, model), so subclasses that share a model
        # share the same wrapper and benefit from the response cache.
        from shared.llm_provider import get_llm  # late import to avoid cycles
        model = os.getenv(self.llm_model_env, "") if self.llm_model_env else ""
        self.llm = get_llm(model=model)
        # Backward-compat alias for older agents that still reference _llm
        self._llm = self.llm

    def set_progress_callback(self, cb: ProgressCallback) -> None:
        self._progress_cb = cb

    async def _report(self, message: str, pct: float = -1) -> None:
        if self._progress_cb:
            await self._progress_cb(self.name, message, pct)

    # ── public entry-point ──────────────────────────────────────────

    async def _run_with_retry(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        if not self.retry_on_failure:
            return await self._run(ctx, result)

        last_exc: Optional[BaseException] = None
        delay = self.retry_initial_delay_seconds
        for attempt in range(1, self.retry_attempts + 1):
            try:
                return await self._run(ctx, result)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                raise
            except Exception as exc:
                last_exc = exc
                if attempt >= self.retry_attempts:
                    raise
                await self._report(
                    f"{self.name} attempt {attempt} failed ({exc}); retrying in {delay:.1f}s",
                )
                await asyncio.sleep(delay)
                delay *= 2
        # Unreachable but keeps type-checkers happy
        raise last_exc if last_exc else RuntimeError("retry loop exited unexpectedly")

    async def execute(self, ctx: AgentContext) -> AgentResult:
        start = time.perf_counter()
        result = AgentResult(status=AgentStatus.RUNNING)

        # BATS: short-circuit before doing any work if the session pool is
        # already drained, and expose the live snapshot to subclasses via
        # ctx.metadata so prompt-building code can splice the directive in.
        from shared.budget import (
            BudgetExhaustedError,
            consume_tool_call_from_current,
            current_budget,
        )
        tracker = current_budget()
        if tracker is not None:
            ctx.metadata["budget_status"] = tracker.snapshot()
            try:
                tracker.assert_not_exhausted()
            except BudgetExhaustedError as exc:
                result.status = AgentStatus.FAILED
                result.error = str(exc)
                result.add_step(
                    action="budget_exhausted",
                    output_summary=str(exc),
                    severity=Severity.WARNING,
                )
                result.duration_ms = (time.perf_counter() - start) * 1000
                return result

        try:
            await self._report(f"Starting {self.name}…")
            result = await asyncio.wait_for(
                self._run_with_retry(ctx, result),
                timeout=ctx.timeout_seconds,
            )
            if result.status == AgentStatus.RUNNING:
                result.status = AgentStatus.SUCCESS
        except asyncio.TimeoutError:
            result.status = AgentStatus.FAILED
            result.error = (
                f"{self.name} exceeded its {ctx.timeout_seconds}s timeout"
            )
            result.add_step(
                action="agent_timeout",
                output_summary=result.error,
                severity=Severity.ERROR,
            )
        except Exception as exc:
            result.status = AgentStatus.FAILED
            result.error = str(exc)
            result.add_step(
                action="agent_error",
                output_summary=str(exc),
                severity=Severity.ERROR,
            )
        finally:
            duration_s = time.perf_counter() - start
            result.duration_ms = duration_s * 1000
            try:
                from shared.observability import AGENT_DURATION  # late import to avoid cycle
                AGENT_DURATION.labels(agent=self.name).observe(duration_s)
            except Exception as obs_exc:
                # Observability must never mask an agent result. Log
                # so a metric outage is visible without breaking the
                # agent's caller.
                import logging
                logging.getLogger("aura.agents.base").debug(
                    "agent duration observability failed: %s", obs_exc,
                )
            # BATS: count this agent invocation as one tool-call against the
            # session pool. Done in finally so failed/timeout agents still
            # bill — the orchestrator's "single-path" search shouldn't get
            # free retries by failing.
            try:
                consume_tool_call_from_current(1)
            except Exception as budget_exc:
                import logging
                logging.getLogger("aura.agents.base").debug(
                    "budget consume failed: %s", budget_exc,
                )
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
