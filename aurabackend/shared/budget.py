"""
BATS — Budget-Aware Test-time Scaling
======================================
Per-session pool of LLM tokens and agent-tool calls. Bound to a
``contextvars.ContextVar`` so anything inside the same asyncio task tree
(LangGraph nodes, BaseAgent.execute, the LLM provider boundary) sees the
same tracker without explicit threading.

Three forces shape an agent's behaviour through this module:

  1. **Awareness** — every ``BaseAgent`` invocation receives a fresh
     ``BudgetStatus`` snapshot in ``ctx.metadata["budget_status"]`` so the
     specialist (notably the Planner) can shorten its plan / drop
     optional steps when the pool is low.

  2. **Pivot signal** — once consumption crosses ``pivot_threshold``
     (default 70%), ``status.should_pivot`` flips ``True``. The Planner
     reads this and prefers narrower task graphs.

  3. **Hard stop** — when tokens or tool calls hit zero, ``is_exhausted``
     flips ``True`` and ``BaseAgent.execute`` short-circuits the next
     agent with ``BudgetExhaustedError``. The LangGraph router converts
     that into a routed-to-END terminal state.

Token consumption is auto-debited from inside ``_CachedProvider`` (every
LLM call). Tool-call consumption is debited once per ``BaseAgent.execute``
turn — i.e. one decrement per node in the LangGraph DAG.
"""
from __future__ import annotations

import contextvars
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger("aura.shared.budget")


# ── Snapshot exposed to agents (immutable view) ───────────────────────

@dataclass(frozen=True)
class BudgetStatus:
    session_id: str
    tokens_total: int
    tokens_remaining: int
    tool_calls_total: int
    tool_calls_remaining: int
    seconds_remaining: float
    should_pivot: bool
    is_exhausted: bool

    def directive(self) -> str:
        """Short prompt fragment agents can splice into LLM messages."""
        if self.is_exhausted:
            return (
                "[BUDGET EXHAUSTED] Stop exploring. Return the best answer you "
                "already have or a single-step fallback."
            )
        if self.should_pivot:
            return (
                f"[BUDGET LOW] {self.tokens_remaining} tokens / "
                f"{self.tool_calls_remaining} tool calls remain. "
                "Prefer a minimal plan (≤2 specialist agents). Drop "
                "optional steps such as quality_check / monitor."
            )
        return (
            f"[BUDGET OK] {self.tokens_remaining} tokens / "
            f"{self.tool_calls_remaining} tool calls remain."
        )


# ── Mutable tracker ──────────────────────────────────────────────────

class BudgetExhaustedError(RuntimeError):
    """Raised when an agent invocation would exceed the session budget."""


@dataclass
class BudgetTracker:
    session_id: str
    token_budget: int = field(default_factory=lambda: int(os.getenv("AURA_BATS_TOKEN_BUDGET", "32000")))
    tool_call_budget: int = field(default_factory=lambda: int(os.getenv("AURA_BATS_TOOL_BUDGET", "12")))
    wall_seconds: float = field(default_factory=lambda: float(os.getenv("AURA_BATS_WALL_SECONDS", "120")))
    pivot_threshold: float = field(default_factory=lambda: float(os.getenv("AURA_BATS_PIVOT_AT", "0.7")))

    tokens_consumed: int = 0
    tool_calls_consumed: int = 0
    started_monotonic: float = field(default_factory=time.monotonic)

    # ── Read accessors ────────────────────────────────────────────────

    @property
    def tokens_remaining(self) -> int:
        return max(0, self.token_budget - self.tokens_consumed)

    @property
    def tool_calls_remaining(self) -> int:
        return max(0, self.tool_call_budget - self.tool_calls_consumed)

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, self.wall_seconds - (time.monotonic() - self.started_monotonic))

    @property
    def is_exhausted(self) -> bool:
        return (
            self.tokens_remaining == 0
            or self.tool_calls_remaining == 0
            or self.seconds_remaining == 0.0
        )

    @property
    def should_pivot(self) -> bool:
        if self.is_exhausted:
            return True
        token_used_frac = self.tokens_consumed / max(self.token_budget, 1)
        tool_used_frac = self.tool_calls_consumed / max(self.tool_call_budget, 1)
        return max(token_used_frac, tool_used_frac) >= self.pivot_threshold

    def snapshot(self) -> BudgetStatus:
        return BudgetStatus(
            session_id=self.session_id,
            tokens_total=self.token_budget,
            tokens_remaining=self.tokens_remaining,
            tool_calls_total=self.tool_call_budget,
            tool_calls_remaining=self.tool_calls_remaining,
            seconds_remaining=self.seconds_remaining,
            should_pivot=self.should_pivot,
            is_exhausted=self.is_exhausted,
        )

    # ── Mutation ──────────────────────────────────────────────────────

    def consume_tokens(self, n: int) -> None:
        if n <= 0:
            return
        self.tokens_consumed += n
        if self.is_exhausted:
            logger.info(
                "BATS budget exhausted on tokens for session=%s (%d/%d)",
                self.session_id, self.tokens_consumed, self.token_budget,
            )

    def consume_tool_call(self, n: int = 1) -> None:
        if n <= 0:
            return
        self.tool_calls_consumed += n
        if self.is_exhausted:
            logger.info(
                "BATS budget exhausted on tool calls for session=%s (%d/%d)",
                self.session_id, self.tool_calls_consumed, self.tool_call_budget,
            )

    def assert_not_exhausted(self) -> None:
        if self.is_exhausted:
            raise BudgetExhaustedError(
                f"BATS budget exhausted for session={self.session_id}: "
                f"tokens={self.tokens_consumed}/{self.token_budget}, "
                f"tool_calls={self.tool_calls_consumed}/{self.tool_call_budget}, "
                f"seconds_remaining={self.seconds_remaining:.1f}"
            )

    def to_dict(self) -> dict:
        return asdict(self.snapshot())


# ── ContextVar plumbing ───────────────────────────────────────────────
# A ContextVar (not threading.local) so values flow into asyncio tasks
# spawned with ``asyncio.create_task`` — which LangGraph does internally.

_current_budget: contextvars.ContextVar[Optional[BudgetTracker]] = contextvars.ContextVar(
    "aura_current_budget", default=None
)


def set_current_budget(tracker: Optional[BudgetTracker]) -> contextvars.Token:
    """Bind ``tracker`` to the current async context. Returns a reset token."""
    return _current_budget.set(tracker)


def reset_current_budget(token: contextvars.Token) -> None:
    _current_budget.reset(token)


def current_budget() -> Optional[BudgetTracker]:
    """Return the active tracker, or ``None`` if BATS is disabled for this run."""
    return _current_budget.get()


def consume_tokens_from_current(n: int) -> None:
    """No-op when no tracker is bound. Called from the LLM provider boundary."""
    tracker = _current_budget.get()
    if tracker is not None:
        tracker.consume_tokens(n)


def consume_tool_call_from_current(n: int = 1) -> None:
    tracker = _current_budget.get()
    if tracker is not None:
        tracker.consume_tool_call(n)
