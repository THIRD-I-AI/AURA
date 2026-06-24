"""Task 1 — the provider seam: complete_with_tools (ReAct/JSON base).

These drive the base LLMProvider.complete_with_tools through a scripted
provider that returns canned raw strings, so the JSON-action parser is tested
deterministically with no network. The contract: one reasoning turn in →
one AssistantTurn out; a bad/empty reply NEVER raises, it returns
finish_reason='error' so the loop can surface it as an event."""
from __future__ import annotations

import os
import sys
from typing import Any, List, Optional, Union

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.llm_provider import AssistantTurn, LLMProvider, ToolCall


class ScriptedProvider(LLMProvider):
    """Returns a canned raw string so we can test the ReAct parser directly."""
    provider_name = "scripted"

    def __init__(self, reply: Optional[str]) -> None:
        super().__init__(model="scripted-1")
        self._reply = reply

    def generate(self, prompt: Union[str, List[str]], **kwargs: Any) -> Optional[str]:
        return self._reply

    def is_available(self) -> bool:
        return True


_TOOLS = [{
    "name": "run_sql",
    "description": "run a select",
    "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
}]


def test_final_action_parses_to_text_turn():
    p = ScriptedProvider('{"action": "final", "text": "The answer is 42."}')
    turn = p.complete_with_tools([{"role": "user", "content": "hi"}], _TOOLS)
    assert isinstance(turn, AssistantTurn)
    assert turn.text == "The answer is 42."
    assert turn.tool_calls == []
    assert turn.finish_reason == "stop"


def test_tool_action_parses_to_tool_call():
    p = ScriptedProvider('{"action": "tool", "tool": "run_sql", "arguments": {"sql": "SELECT 1"}}')
    turn = p.complete_with_tools([{"role": "user", "content": "hi"}], _TOOLS)
    assert turn.text is None
    assert len(turn.tool_calls) == 1
    call = turn.tool_calls[0]
    assert isinstance(call, ToolCall)
    assert call.name == "run_sql"
    assert call.arguments == {"sql": "SELECT 1"}
    assert call.id  # synthesised, non-empty
    assert turn.finish_reason == "tool_calls"


def test_fenced_json_is_tolerated():
    p = ScriptedProvider('```json\n{"action": "final", "text": "ok"}\n```')
    turn = p.complete_with_tools([{"role": "user", "content": "hi"}], _TOOLS)
    assert turn.text == "ok"


def test_json_embedded_in_prose_is_extracted():
    p = ScriptedProvider('Sure! Here is my action: {"action": "tool", "tool": "run_sql", "arguments": {"sql": "SELECT 2"}} done.')
    turn = p.complete_with_tools([{"role": "user", "content": "hi"}], _TOOLS)
    assert turn.tool_calls[0].arguments == {"sql": "SELECT 2"}


def test_unparseable_reply_yields_error_finish_reason():
    p = ScriptedProvider("I cannot produce JSON, sorry.")
    turn = p.complete_with_tools([{"role": "user", "content": "hi"}], _TOOLS)
    assert turn.finish_reason == "error"
    assert turn.tool_calls == []


def test_none_reply_yields_error():
    turn = ScriptedProvider(None).complete_with_tools([{"role": "user", "content": "hi"}], _TOOLS)
    assert turn.finish_reason == "error"
