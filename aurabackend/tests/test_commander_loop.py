"""Task 3 — the commander loop. Driven by a scripted fake provider so the
event sequence is deterministic with no network. The load-bearing property:
EVERY terminal path yields a typed event (DoneEvent or ErrorEvent) — there is
no silent-empty path (the structural cure for the old chat.py:605 bug)."""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.commander import (
    DoneEvent,
    ErrorEvent,
    TextEvent,
    ToolResultEvent,
    run_commander,
)
from agents.commander_tools import ToolOutcome
from shared.llm_provider import AssistantTurn, LLMRateLimitError, ToolCall


class FakeProvider:
    """Yields scripted AssistantTurns in order, ignoring the prompt."""
    model = "fake"

    def __init__(self, turns: List[AssistantTurn]) -> None:
        self._turns = turns

    def complete_with_tools(self, messages, tools, **kwargs) -> AssistantTurn:
        return self._turns.pop(0)


class RaisingProvider:
    model = "fake"

    def complete_with_tools(self, messages, tools, **kwargs):
        raise LLMRateLimitError("429 quota")


class FakeRegistry:
    def __init__(self, outcome: ToolOutcome) -> None:
        self._outcome = outcome
        self.calls: List[Dict[str, Any]] = []

    def specs(self):
        return [{"name": "run_sql", "description": "x", "parameters": {}}]

    def execute(self, name, arguments, *, tenant, con):
        self.calls.append({"name": name, "arguments": arguments, "tenant": tenant})
        return self._outcome


def _types(events):
    return [type(e).__name__ for e in events]


def test_answer_without_tools():
    llm = FakeProvider([AssistantTurn(text="42", tool_calls=[], finish_reason="stop")])
    events = list(run_commander("q", tenant="t1", schema_context="",
                                registry=FakeRegistry(ToolOutcome(True, {})), llm=llm, con=None))
    assert _types(events) == ["TextEvent", "DoneEvent"]
    assert events[0].text == "42"
    assert events[1].reason == "stop"


def test_one_tool_call_then_answer():
    turns = [
        AssistantTurn(text=None, tool_calls=[ToolCall("c1", "run_sql", {"sql": "SELECT 1"})], finish_reason="tool_calls"),
        AssistantTurn(text="done", tool_calls=[], finish_reason="stop"),
    ]
    reg = FakeRegistry(ToolOutcome(True, {"row_count": 1}))
    events = list(run_commander("q", tenant="t1", schema_context="", registry=reg, llm=FakeProvider(turns), con=None))
    assert _types(events) == ["ToolCallStartEvent", "ToolResultEvent", "TextEvent", "DoneEvent"]
    assert reg.calls[0]["tenant"] == "t1"          # tenant injected, not model-supplied


def test_tool_error_is_fed_back_then_answer():
    turns = [
        AssistantTurn(text=None, tool_calls=[ToolCall("c1", "run_sql", {"sql": "DROP"})], finish_reason="tool_calls"),
        AssistantTurn(text="recovered", tool_calls=[], finish_reason="stop"),
    ]
    reg = FakeRegistry(ToolOutcome(False, error="rejected"))
    events = list(run_commander("q", tenant="t1", schema_context="", registry=reg, llm=FakeProvider(turns), con=None))
    assert _types(events) == ["ToolCallStartEvent", "ToolErrorEvent", "TextEvent", "DoneEvent"]


def test_max_iterations_terminates_with_done():
    class Loop:
        model = "fake"

        def complete_with_tools(self, m, t, **k):
            return AssistantTurn(text=None, tool_calls=[ToolCall("c", "run_sql", {"sql": "SELECT 1"})], finish_reason="tool_calls")

    reg = FakeRegistry(ToolOutcome(True, {"row_count": 1}))
    events = list(run_commander("q", tenant="t1", schema_context="", registry=reg, llm=Loop(), con=None, max_iterations=2))
    assert isinstance(events[-1], DoneEvent)
    assert events[-1].reason == "max_iterations"


def test_rate_limit_becomes_error_event():
    events = list(run_commander("q", tenant="t1", schema_context="",
                                registry=FakeRegistry(ToolOutcome(True, {})), llm=RaisingProvider(), con=None))
    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].kind == "rate_limit"


def test_unparseable_turn_becomes_error_event():
    llm = FakeProvider([AssistantTurn(text=None, tool_calls=[], finish_reason="error")])
    events = list(run_commander("q", tenant="t1", schema_context="",
                                registry=FakeRegistry(ToolOutcome(True, {})), llm=llm, con=None))
    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].kind == "unparseable"


def test_to_sse_frames_are_well_formed():
    ev = ToolResultEvent(name="run_sql", result={"row_count": 1})
    frame = ev.to_sse()
    assert frame.startswith("event: tool_result\n")
    assert "data: " in frame
    assert frame.endswith("\n\n")


def test_text_event_to_sse_roundtrips_payload():
    import json
    frame = TextEvent(text="hello").to_sse()
    data_line = [ln for ln in frame.splitlines() if ln.startswith("data: ")][0]
    assert json.loads(data_line[len("data: "):]) == {"text": "hello"}
