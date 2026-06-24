"""
Commander Loop
==============
The reactive model-in-a-loop that replaces the IntentAgent + run_orchestrator
DAG. Owns orchestration, streaming, and tenancy; calls the provider for one
reasoning turn at a time and executes tools through the commander registry.

Every terminal path yields a typed event (DoneEvent or ErrorEvent) — there is
no silent-empty path. This is the structural cure for the old chat.py behaviour
where an empty pipeline returned execution_result=None with no message.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List

from shared.llm_provider import AssistantTurn, LLMRateLimitError


def _frame(event_name: str, payload: Dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, default=str)}\n\n"


@dataclass
class ToolCallStartEvent:
    name: str
    arguments: Dict[str, Any]
    event_name: str = field(default="tool_call", init=False)

    def to_sse(self) -> str:
        return _frame(self.event_name, {"name": self.name, "arguments": self.arguments})


@dataclass
class ToolResultEvent:
    name: str
    result: Any
    event_name: str = field(default="tool_result", init=False)

    def to_sse(self) -> str:
        return _frame(self.event_name, {"name": self.name, "result": self.result})


@dataclass
class ToolErrorEvent:
    name: str
    message: str
    event_name: str = field(default="tool_error", init=False)

    def to_sse(self) -> str:
        return _frame(self.event_name, {"name": self.name, "message": self.message})


@dataclass
class TextEvent:
    text: str
    event_name: str = field(default="text", init=False)

    def to_sse(self) -> str:
        return _frame(self.event_name, {"text": self.text})


@dataclass
class ErrorEvent:
    kind: str
    message: str
    event_name: str = field(default="error", init=False)

    def to_sse(self) -> str:
        return _frame(self.event_name, {"kind": self.kind, "message": self.message})


@dataclass
class DoneEvent:
    reason: str
    elapsed_ms: float = 0.0
    event_name: str = field(default="done", init=False)

    def to_sse(self) -> str:
        return _frame(self.event_name, {"reason": self.reason, "elapsed_ms": self.elapsed_ms})


def build_system_prompt(schema_context: str, registry: Any) -> str:
    return (
        "You answer questions about the user's loaded datasets using the tools.\n\n"
        "SCHEMA CONTEXT (use these exact table/column names):\n"
        f"{schema_context or '(no datasets loaded)'}\n"
    )


def _assistant_msg(turn: AssistantTurn) -> Dict[str, Any]:
    call = turn.tool_calls[0]
    return {"role": "assistant", "content": json.dumps(
        {"action": "tool", "tool": call.name, "arguments": call.arguments})}


def _tool_result_msg(call_id: str, name: str, outcome: Any) -> Dict[str, Any]:
    body = json.dumps({"ok": outcome.ok, "value": outcome.value, "error": outcome.error}, default=str)
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": body[:4000]}


def run_commander(message: str, *, tenant: str, schema_context: str, registry: Any,
                  llm: Any, con: Any, max_iterations: int = 8) -> Iterator[Any]:
    t0 = time.perf_counter()
    transcript: List[Dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(schema_context, registry)},
        {"role": "user", "content": message},
    ]
    for _ in range(max_iterations):
        try:
            turn = llm.complete_with_tools(transcript, registry.specs())
        except LLMRateLimitError as exc:
            yield ErrorEvent(kind="rate_limit", message=str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — surface, never crash the stream
            yield ErrorEvent(kind="provider", message=str(exc))
            return

        if turn.finish_reason == "error":
            yield ErrorEvent(kind="unparseable", message="model produced no valid action")
            return

        if turn.text is not None and not turn.tool_calls:
            yield TextEvent(text=turn.text)
            yield DoneEvent(reason="stop", elapsed_ms=round((time.perf_counter() - t0) * 1000, 1))
            return

        transcript.append(_assistant_msg(turn))
        for call in turn.tool_calls:
            yield ToolCallStartEvent(name=call.name, arguments=call.arguments)
            outcome = registry.execute(call.name, call.arguments, tenant=tenant, con=con)
            if outcome.ok:
                yield ToolResultEvent(name=call.name, result=outcome.value)
            else:
                yield ToolErrorEvent(name=call.name, message=outcome.error or "tool failed")
            transcript.append(_tool_result_msg(call.id, call.name, outcome))

    yield DoneEvent(reason="max_iterations", elapsed_ms=round((time.perf_counter() - t0) * 1000, 1))
