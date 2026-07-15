# AURA Commander Core — Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a working `POST /chat/stream` that, on any LLM backend incl. Ollama, answers a natural-language data question by calling a guarded `run_sql` tool in a model-in-a-loop, streams typed progress events over SSE, and never returns a silent empty response — all behind `AURA_COMMANDER_ENABLED`, with the existing `POST /chat` DAG path untouched.

**Architecture:** A one-turn provider seam (`complete_with_tools` on `LLMProvider`, ReAct/JSON base that works on every backend) + a sync loop in the app (`agents/commander.py`) that owns orchestration, guardrails, tenancy, and streaming + a commander-specific tool registry (`agents/commander_tools.py`) whose `run_sql` reuses the canonical `SQLSafetyValidator`. A FastAPI streaming endpoint bridges the blocking loop onto async SSE via a worker thread + `asyncio.Queue`.

**Tech Stack:** Python 3.11/3.12, FastAPI, pydantic, DuckDB, pytest; React + Vite + Vitest (frontend). LLM via existing `shared/llm_provider.py`.

**Spec:** `docs/superpowers/specs/2026-06-24-aura-commander-core-subsystem-a-design.md`

## Global Constraints

- **Coexistence:** `POST /chat` (DAG) is NOT modified except for adding the new endpoint in the same router module. Its tests stay green. (spec: Migration strategy)
- **Flag:** new behaviour is gated by `AURA_COMMANDER_ENABLED`, default `false`; flag off ⇒ `POST /chat/stream` returns HTTP 404. (spec: Migration strategy)
- **Tenancy is never model-supplied:** `tenant` and the DuckDB `con` come from the verified request context; tools receive them as injected kwargs. (spec: The loop)
- **Every terminal path emits a typed event** (`DoneEvent` or `ErrorEvent`) — no code path returns nothing. (spec: Error handling)
- **`run_sql` guardrail = `SQLSafetyValidator`** from `safety/` (the same validator the live `/queries` path uses), SELECT-only + safety LIMIT. Do NOT couple to `mcp_servers` (optional `mcp` dep). (plan correction to spec)
- **Registry lives in a NEW module `agents/commander_tools.py`** — `agents/tool_registry.py` is already occupied by an unrelated async `Tool`/`ToolRegistry`. Do not modify or import that file. (plan correction to spec)
- **Tools never raise to the loop:** `CommanderToolRegistry.execute` returns `ToolOutcome(ok=False, ...)` for unknown tool / bad args / handler error. (spec: Tool registry)
- **Test runner:** from `aurabackend/`, use the repo-root venv: `../.venv/Scripts/python.exe -m pytest <file> -v`. (CLAUDE.md / memory)
- **Ruff before commit:** from `aurabackend/`: `../.venv/Scripts/python.exe -m ruff check --fix . --ignore E501,E402,F401,W191,W291,W293,F841,E701,E712,F823`.
- **Commits:** Conventional Commits; co-author `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Feature branch (already on `feature/chat-commander-audit`); do not push to main.

---

## File Structure

| File | Responsibility |
|---|---|
| `aurabackend/shared/llm_provider.py` (modify) | Add `ToolCall`, `AssistantTurn` dataclasses + `complete_with_tools` ReAct base + JSON-action parsing onto `LLMProvider`. |
| `aurabackend/agents/commander_tools.py` (create) | `ToolOutcome`, `CommanderTool`, `CommanderToolRegistry` (`specs()`/`execute()`), `_run_sql_handler`, `build_default_registry()`. |
| `aurabackend/agents/commander.py` (create) | `CommanderEvent` union (+ `.to_sse()`), `build_system_prompt`, `run_commander` loop. |
| `aurabackend/shared/config.py` (modify) | Add `commander_enabled` field. |
| `aurabackend/api_gateway/routers/chat.py` (modify) | Add `POST /chat/stream` endpoint + blocking→async bridge; `_build_commander_session` helper. |
| `aurabackend/tests/test_commander_provider.py` (create) | Task 1 tests. |
| `aurabackend/tests/test_commander_tools.py` (create) | Task 2 tests. |
| `aurabackend/tests/test_commander_loop.py` (create) | Task 3 tests. |
| `aurabackend/tests/test_commander_endpoint.py` (create) | Task 4 tests. |
| `frontend/src/services/api.ts` (modify) | `chatService.streamMessage` (fetch-stream SSE parser). |
| `frontend/src/services/__tests__/streamMessage.test.ts` (create) | Task 5 SSE-parser test. |
| `frontend/src/components/ChatInterface.tsx` (modify) | Render the event stream behind the flag. |

---

### Task 1: Provider seam — `complete_with_tools` (ReAct base)

**Files:**
- Modify: `aurabackend/shared/llm_provider.py` (add near the `LLMProvider` ABC, after `_build_messages`)
- Test: `aurabackend/tests/test_commander_provider.py`

**Interfaces:**
- Consumes: existing `LLMProvider.generate(prompt: Union[str, List[str]], **kwargs) -> Optional[str]`.
- Produces:
  - `ToolCall(id: str, name: str, arguments: Dict[str, Any])`
  - `AssistantTurn(text: Optional[str], tool_calls: List[ToolCall], finish_reason: str)` — `finish_reason ∈ {"stop","tool_calls","error"}`
  - `LLMProvider.complete_with_tools(messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], **kwargs) -> AssistantTurn`

- [ ] **Step 1: Write the failing test**

```python
# aurabackend/tests/test_commander_provider.py
from __future__ import annotations

import os
import sys
from typing import Any, List, Optional, Union

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.llm_provider import AssistantTurn, LLMProvider, ToolCall


class ScriptedProvider(LLMProvider):
    """Returns canned raw strings so we can test the ReAct parser deterministically."""
    provider_name = "scripted"

    def __init__(self, reply: str) -> None:
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
    class NoneProvider(ScriptedProvider):
        def generate(self, prompt, **kwargs):  # type: ignore[override]
            return None
    turn = NoneProvider("x").complete_with_tools([{"role": "user", "content": "hi"}], _TOOLS)
    assert turn.finish_reason == "error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_commander_provider.py -v`
Expected: FAIL — `ImportError: cannot import name 'AssistantTurn'`.

- [ ] **Step 3: Write minimal implementation**

In `shared/llm_provider.py`, add at module top (after the existing imports, near `LLMRateLimitError`):

```python
import re as _re
import uuid as _uuid
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class AssistantTurn:
    text: Optional[str]
    tool_calls: List["ToolCall"] = field(default_factory=list)
    finish_reason: str = "stop"


def _extract_first_json_object(text: str) -> Optional[str]:
    """Return the first balanced {...} substring, or None. Tolerates prose/fences."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        if s.endswith("```"):
            s = s.rsplit("```", 1)[0]
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None
```

Then add these methods to the `LLMProvider` ABC (non-abstract; concrete base methods):

```python
    _REACT_CONTRACT = (
        "You are AURA's data commander. Use the available tools to answer.\n"
        "Reply with EXACTLY ONE JSON object and nothing else, in one of two forms:\n"
        '  {"action": "tool", "tool": "<tool_name>", "arguments": { ... }}\n'
        '  {"action": "final", "text": "<your answer to the user>"}\n'
        "Do not wrap the JSON in markdown. Do not add commentary."
    )

    def _render_react_prompt(self, messages: List[Dict[str, Any]],
                             tools: List[Dict[str, Any]]) -> List[str]:
        catalog_lines = []
        for t in tools:
            catalog_lines.append(
                f"- {t['name']}: {t.get('description', '')} "
                f"params={json.dumps(t.get('parameters', {}))}"
            )
        system = "\n".join([self._REACT_CONTRACT, "", "TOOLS:", *catalog_lines])
        convo_lines = []
        for m in messages:
            role = m.get("role", "user")
            convo_lines.append(f"[{role}] {m.get('content', '')}")
        return [system, "\n".join(convo_lines)]

    def complete_with_tools(self, messages: List[Dict[str, Any]],
                            tools: List[Dict[str, Any]], **kwargs: Any) -> AssistantTurn:
        """One reasoning turn. Default = ReAct/JSON over generate(); native
        function-calling providers may override. Never raises for a bad reply —
        returns finish_reason='error' so the loop can surface it as an event."""
        prompt = self._render_react_prompt(messages, tools)
        kwargs.setdefault("temperature", 0)
        raw = self.generate(prompt, **kwargs)
        if not raw:
            return AssistantTurn(text=None, tool_calls=[], finish_reason="error")
        blob = _extract_first_json_object(raw)
        if blob is None:
            return AssistantTurn(text=None, tool_calls=[], finish_reason="error")
        try:
            obj = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            return AssistantTurn(text=None, tool_calls=[], finish_reason="error")
        action = obj.get("action")
        if action == "final":
            return AssistantTurn(text=str(obj.get("text", "")), tool_calls=[], finish_reason="stop")
        if action == "tool" and obj.get("tool"):
            call = ToolCall(id=_uuid.uuid4().hex[:12], name=str(obj["tool"]),
                            arguments=obj.get("arguments") or {})
            return AssistantTurn(text=None, tool_calls=[call], finish_reason="tool_calls")
        return AssistantTurn(text=None, tool_calls=[], finish_reason="error")
```

(`_re` import is unused by this step — drop it if ruff flags; kept only if a later regex is added. Prefer removing it now to stay clean.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_commander_provider.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Lint + commit**

```bash
cd aurabackend && ../.venv/Scripts/python.exe -m ruff check --fix shared/llm_provider.py tests/test_commander_provider.py --ignore E501,E402,F401,W191,W291,W293,F841,E701,E712,F823
git add aurabackend/shared/llm_provider.py aurabackend/tests/test_commander_provider.py
git commit -m "feat(commander): complete_with_tools ReAct provider seam"
```

---

### Task 2: Commander tool registry + `run_sql`

**Files:**
- Create: `aurabackend/agents/commander_tools.py`
- Test: `aurabackend/tests/test_commander_tools.py`

**Interfaces:**
- Consumes: `safety.SQLSafetyValidator` (`.validate(sql) -> ValidationResult(is_valid, errors, ...)`, `.add_safety_limit(sql) -> str`). A DuckDB `con` (already has tables loaded). Passed `tenant: str`.
- Produces:
  - `ToolOutcome(ok: bool, value: Any = None, error: Optional[str] = None)`
  - `CommanderTool(name: str, description: str, parameters: Dict[str, Any], handler: Callable[..., ToolOutcome], mutating: bool = False)`
  - `CommanderToolRegistry` with `register(tool)`, `specs() -> List[Dict[str, Any]]`, `execute(name: str, arguments: Dict[str, Any], *, tenant: str, con: Any) -> ToolOutcome`
  - `build_default_registry() -> CommanderToolRegistry` (registers `run_sql`)

- [ ] **Step 1: Write the failing test**

```python
# aurabackend/tests/test_commander_tools.py
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.commander_tools import ToolOutcome, build_default_registry

duckdb = pytest.importorskip("duckdb")


def _con():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE sales (id INTEGER, amount INTEGER)")
    con.execute("INSERT INTO sales VALUES (1, 10), (2, 20), (3, 30)")
    return con


def test_run_sql_returns_rows():
    reg = build_default_registry()
    out = reg.execute("run_sql", {"sql": "SELECT amount FROM sales ORDER BY amount"},
                      tenant="t1", con=_con())
    assert isinstance(out, ToolOutcome)
    assert out.ok is True
    assert out.value["row_count"] == 3
    assert out.value["columns"] == ["amount"]
    assert out.value["rows"][0] == [10]


def test_run_sql_rejects_ddl():
    reg = build_default_registry()
    con = _con()
    out = reg.execute("run_sql", {"sql": "DROP TABLE sales"}, tenant="t1", con=con)
    assert out.ok is False
    assert out.error
    # the destructive statement did not run
    names = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert "sales" in names


def test_run_sql_rejects_multi_statement_injection():
    reg = build_default_registry()
    con = _con()
    out = reg.execute(
        "run_sql",
        {"sql": "SELECT 1; CREATE TABLE pwned AS SELECT 1"},
        tenant="t1", con=con,
    )
    assert out.ok is False
    names = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert "pwned" not in names


def test_run_sql_enforces_row_limit():
    reg = build_default_registry()
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE big AS SELECT * FROM range(5000) t(n)")
    out = reg.execute("run_sql", {"sql": "SELECT n FROM big"}, tenant="t1", con=con)
    assert out.ok is True
    assert out.value["row_count"] <= 1000


def test_unknown_tool_returns_error_not_raise():
    reg = build_default_registry()
    out = reg.execute("does_not_exist", {}, tenant="t1", con=_con())
    assert out.ok is False
    assert "does_not_exist" in out.error


def test_missing_required_arg_returns_error_not_raise():
    reg = build_default_registry()
    out = reg.execute("run_sql", {}, tenant="t1", con=_con())
    assert out.ok is False
    assert out.error


def test_specs_shape_is_model_ready():
    reg = build_default_registry()
    specs = reg.specs()
    assert any(s["name"] == "run_sql" for s in specs)
    spec = next(s for s in specs if s["name"] == "run_sql")
    assert spec["parameters"]["required"] == ["sql"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_commander_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.commander_tools'`.

- [ ] **Step 3: Write minimal implementation**

```python
# aurabackend/agents/commander_tools.py
"""
Commander Tool Registry
=======================
The reactive commander loop (agents/commander.py) calls AURA capabilities
through this registry. Distinct from agents/tool_registry.py (async, agent
executor): commander tools are SYNC, receive the verified tenant + a
tenant-scoped DuckDB connection as injected kwargs, validate their arguments,
and NEVER raise to the loop — every failure is a ToolOutcome(ok=False).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from safety import SQLSafetyValidator

_DEFAULT_ROW_LIMIT = 1000


@dataclass
class ToolOutcome:
    ok: bool
    value: Any = None
    error: Optional[str] = None


@dataclass
class CommanderTool:
    name: str
    description: str
    parameters: Dict[str, Any]            # JSON schema
    handler: Callable[..., ToolOutcome]   # handler(arguments, *, tenant, con) -> ToolOutcome
    mutating: bool = False


class CommanderToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, CommanderTool] = {}

    def register(self, tool: CommanderTool) -> None:
        self._tools[tool.name] = tool

    def specs(self) -> List[Dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in self._tools.values()
        ]

    def execute(self, name: str, arguments: Dict[str, Any], *, tenant: str, con: Any) -> ToolOutcome:
        tool = self._tools.get(name)
        if tool is None:
            return ToolOutcome(ok=False, error=f"unknown tool '{name}'")
        missing = [k for k in tool.parameters.get("required", []) if k not in (arguments or {})]
        if missing:
            return ToolOutcome(ok=False, error=f"missing required argument(s): {', '.join(missing)}")
        try:
            return tool.handler(arguments or {}, tenant=tenant, con=con)
        except Exception as exc:  # tools must never raise to the loop
            return ToolOutcome(ok=False, error=f"{name} failed: {exc}")


def _run_sql_handler(arguments: Dict[str, Any], *, tenant: str, con: Any) -> ToolOutcome:
    sql = str(arguments.get("sql", "")).strip()
    if not sql:
        return ToolOutcome(ok=False, error="sql must be a non-empty string")
    validator = SQLSafetyValidator()
    result = validator.validate(sql)
    if not result.is_valid:
        return ToolOutcome(ok=False, error="; ".join(result.errors) or "rejected by SQL safety validator")
    safe_sql = sql if "LIMIT" in sql.upper() else validator.add_safety_limit(sql)
    cur = con.execute(safe_sql)
    columns = [d[0] for d in (cur.description or [])]
    rows = [list(r) for r in cur.fetchmany(_DEFAULT_ROW_LIMIT)]
    return ToolOutcome(ok=True, value={"columns": columns, "rows": rows, "row_count": len(rows)})


_RUN_SQL = CommanderTool(
    name="run_sql",
    description=(
        "Run a single read-only SQL SELECT against the user's loaded datasets and "
        "return rows. Use the exact table/column names from the schema context. "
        "SELECT only — no DDL/DML."
    ),
    parameters={
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "a single SELECT statement"}},
        "required": ["sql"],
    },
    handler=_run_sql_handler,
    mutating=False,
)


def build_default_registry() -> CommanderToolRegistry:
    reg = CommanderToolRegistry()
    reg.register(_RUN_SQL)
    return reg
```

> **Verify before relying:** confirm `SQLSafetyValidator.add_safety_limit` exists and `validate(...).is_valid`/`.errors` are the real attribute names (seen in `api_gateway/routers/queries.py:249-250,462` and `safety/validator.py`). If `add_safety_limit` is named differently, append `f" LIMIT {_DEFAULT_ROW_LIMIT}"` to a wrapping subquery instead — but `fetchmany(_DEFAULT_ROW_LIMIT)` already caps returned rows regardless, so the LIMIT is defence-in-depth, not the only cap. The multi-statement test is the load-bearing safety assertion.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_commander_tools.py -v`
Expected: PASS (7 passed). If `test_run_sql_rejects_multi_statement_injection` fails (sentinel table created), STOP — the validator is not catching multi-statement; do not weaken the test, fix the guardrail (the validator must reject `;`-joined statements).

- [ ] **Step 5: Lint + commit**

```bash
cd aurabackend && ../.venv/Scripts/python.exe -m ruff check --fix agents/commander_tools.py tests/test_commander_tools.py --ignore E501,E402,F401,W191,W291,W293,F841,E701,E712,F823
git add aurabackend/agents/commander_tools.py aurabackend/tests/test_commander_tools.py
git commit -m "feat(commander): tool registry + guarded run_sql"
```

---

### Task 3: Commander events + `run_commander` loop

**Files:**
- Create: `aurabackend/agents/commander.py`
- Test: `aurabackend/tests/test_commander_loop.py`

**Interfaces:**
- Consumes: `LLMProvider.complete_with_tools` (Task 1), `CommanderToolRegistry.execute`/`.specs` + `ToolOutcome` (Task 2), `LLMRateLimitError` (existing).
- Produces:
  - Event dataclasses, each with `.event_name: str` and `.to_sse() -> str`:
    `ToolCallStartEvent(name, arguments)`, `ToolResultEvent(name, result)`, `ToolErrorEvent(name, message)`, `TextEvent(text)`, `ErrorEvent(kind, message)`, `DoneEvent(reason, elapsed_ms=0.0)`
  - `build_system_prompt(schema_context: str, registry: CommanderToolRegistry) -> str`
  - `run_commander(message: str, *, tenant: str, schema_context: str, registry, llm, con, max_iterations: int = 8) -> Iterator[CommanderEvent]`

- [ ] **Step 1: Write the failing test**

```python
# aurabackend/tests/test_commander_loop.py
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.commander import (
    DoneEvent,
    ErrorEvent,
    TextEvent,
    ToolCallStartEvent,
    ToolErrorEvent,
    ToolResultEvent,
    run_commander,
)
from agents.commander_tools import ToolOutcome
from shared.llm_provider import AssistantTurn, LLMRateLimitError, ToolCall


class FakeProvider:
    """Yields scripted AssistantTurns in order, ignoring the prompt."""
    def __init__(self, turns: List[AssistantTurn]) -> None:
        self._turns = turns
        self.model = "fake"

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
    def specs(self): return [{"name": "run_sql", "description": "x", "parameters": {}}]
    def execute(self, name, arguments, *, tenant, con):
        self.calls.append({"name": name, "arguments": arguments, "tenant": tenant})
        return self._outcome


def _types(events):
    return [type(e).__name__ for e in events]


def test_answer_without_tools():
    llm = FakeProvider([AssistantTurn(text="42", tool_calls=[], finish_reason="stop")])
    events = list(run_commander("q", tenant="t1", schema_context="", registry=FakeRegistry(ToolOutcome(True, {})), llm=llm, con=None))
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
    # always returns a tool call → never answers
    class Loop:
        model = "fake"
        def complete_with_tools(self, m, t, **k):
            return AssistantTurn(text=None, tool_calls=[ToolCall("c", "run_sql", {"sql": "SELECT 1"})], finish_reason="tool_calls")
    reg = FakeRegistry(ToolOutcome(True, {"row_count": 1}))
    events = list(run_commander("q", tenant="t1", schema_context="", registry=reg, llm=Loop(), con=None, max_iterations=2))
    assert isinstance(events[-1], DoneEvent)
    assert events[-1].reason == "max_iterations"


def test_rate_limit_becomes_error_event():
    events = list(run_commander("q", tenant="t1", schema_context="", registry=FakeRegistry(ToolOutcome(True, {})), llm=RaisingProvider(), con=None))
    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].kind == "rate_limit"


def test_unparseable_turn_becomes_error_event():
    llm = FakeProvider([AssistantTurn(text=None, tool_calls=[], finish_reason="error")])
    events = list(run_commander("q", tenant="t1", schema_context="", registry=FakeRegistry(ToolOutcome(True, {})), llm=llm, con=None))
    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].kind == "unparseable"


def test_to_sse_frames_are_well_formed():
    ev = ToolResultEvent(name="run_sql", result={"row_count": 1})
    frame = ev.to_sse()
    assert frame.startswith("event: tool_result\n")
    assert "data: " in frame
    assert frame.endswith("\n\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_commander_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.commander'`.

- [ ] **Step 3: Write minimal implementation**

```python
# aurabackend/agents/commander.py
"""
Commander Loop
==============
The reactive model-in-a-loop that replaces the IntentAgent + run_orchestrator
DAG. Owns orchestration, streaming, and tenancy; calls the provider for one
reasoning turn at a time and executes tools through the commander registry.
Every terminal path yields a typed event — there is no silent-empty path.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterator, List, Optional

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
            yield ErrorEvent(kind="rate_limit", message=str(exc)); return
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the stream
            yield ErrorEvent(kind="provider", message=str(exc)); return

        if turn.finish_reason == "error":
            yield ErrorEvent(kind="unparseable", message="model produced no valid action"); return

        if turn.text is not None and not turn.tool_calls:
            yield TextEvent(text=turn.text)
            yield DoneEvent(reason="stop", elapsed_ms=round((time.perf_counter() - t0) * 1000, 1)); return

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_commander_loop.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Lint + commit**

```bash
cd aurabackend && ../.venv/Scripts/python.exe -m ruff check --fix agents/commander.py tests/test_commander_loop.py --ignore E501,E402,F401,W191,W291,W293,F841,E701,E712,F823
git add aurabackend/agents/commander.py aurabackend/tests/test_commander_loop.py
git commit -m "feat(commander): event types + run_commander loop"
```

---

### Task 4: Config flag + `POST /chat/stream` SSE endpoint (blocking→async bridge)

**Files:**
- Modify: `aurabackend/shared/config.py` (add `commander_enabled`)
- Modify: `aurabackend/api_gateway/routers/chat.py` (add request model, helper, endpoint)
- Test: `aurabackend/tests/test_commander_endpoint.py`

**Interfaces:**
- Consumes: `settings.commander_enabled`; `run_commander` (Task 3); `build_default_registry` (Task 2); existing `new_connection`, `build_schema_context_cached`, `_request_tenant`, `get_llm`.
- Produces: `POST /chat/stream` returning `text/event-stream`; HTTP 404 when flag off.

- [ ] **Step 1: Write the failing test**

```python
# aurabackend/tests/test_commander_endpoint.py
from __future__ import annotations

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _app(monkeypatch, enabled: bool):
    monkeypatch.setenv("AURA_COMMANDER_ENABLED", "true" if enabled else "false")
    # rebuild settings so the env var is read
    import importlib

    import shared.config as cfg
    importlib.reload(cfg)
    import api_gateway.routers.chat as chat
    importlib.reload(chat)
    app = FastAPI()
    app.include_router(chat.router)
    return app, chat


def test_stream_404_when_flag_off(monkeypatch):
    app, _ = _app(monkeypatch, enabled=False)
    client = TestClient(app)
    r = client.post("/chat/stream", json={"message": "hi"})
    assert r.status_code == 404


def test_stream_emits_events_when_enabled(monkeypatch):
    app, chat = _app(monkeypatch, enabled=True)

    # Patch the heavy bits: schema build + provider, so the test is hermetic.
    async def _fake_session(http_request, req):
        import duckdb
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE sales (amount INTEGER)")
        con.execute("INSERT INTO sales VALUES (10), (20)")
        return con, "TABLE sales(amount)", "tenant1"

    from shared.llm_provider import AssistantTurn, ToolCall

    class FakeLLM:
        model = "fake"
        def __init__(self): self._n = 0
        def complete_with_tools(self, messages, tools, **kw):
            self._n += 1
            if self._n == 1:
                return AssistantTurn(None, [ToolCall("c1", "run_sql", {"sql": "SELECT amount FROM sales"})], "tool_calls")
            return AssistantTurn("Two rows.", [], "stop")

    monkeypatch.setattr(chat, "_build_commander_session", _fake_session)
    monkeypatch.setattr(chat, "get_llm", lambda *a, **k: FakeLLM())

    client = TestClient(app)
    with client.stream("POST", "/chat/stream", json={"message": "show sales"}) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        body = "".join(chunk for chunk in r.iter_text())
    assert "event: tool_call" in body
    assert "event: tool_result" in body
    assert "event: text" in body
    assert "event: done" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_commander_endpoint.py -v`
Expected: FAIL — `404` test may pass incidentally, but `test_stream_emits_events_when_enabled` fails (`_build_commander_session` / endpoint absent).

- [ ] **Step 3a: Add the config field**

In `shared/config.py`, in the `AuraSettings` class near the other feature flags (e.g. after `jwt_enabled`):

```python
    commander_enabled: bool = Field(False, alias="AURA_COMMANDER_ENABLED")
```

- [ ] **Step 3b: Add the endpoint to `chat.py`**

Add imports at the top of `api_gateway/routers/chat.py`:

```python
import asyncio
import threading
from fastapi.responses import StreamingResponse
from shared.config import settings
from shared.llm_provider import get_llm
from agents.commander import run_commander
from agents.commander_tools import build_default_registry

_COMMANDER_REGISTRY = build_default_registry()
_STREAM_SENTINEL = object()


class ChatStreamRequest(BaseModel):
    message: str
    context: Optional[str] = None
    session_id: Optional[str] = None
    uploaded_file: Optional[str] = None
```

Add a session-builder that reuses the DAG's connection + schema logic (factored minimal — only what the loop needs):

```python
async def _build_commander_session(http_request: Request, req: "ChatStreamRequest"):
    """Build a tenant-scoped DuckDB connection + schema-context text, the same
    way chat_endpoint does. Runs in the async endpoint (build_schema_context_cached
    is async); the returned con is then used ONLY by the worker thread."""
    tenant = _request_tenant(http_request)
    con = new_connection()
    schema_result = await build_schema_context_cached(con, tenant, use_llm=True)
    context_text = schema_result.get("context_text") or "No tables available."
    if req.context:
        context_text = f"{req.context}\n\n{context_text}"
    return con, context_text, (tenant or "default")
```

Add the endpoint:

```python
@router.post("/chat/stream")
async def chat_stream(req: ChatStreamRequest, http_request: Request) -> StreamingResponse:
    """Commander streaming chat (coexists with /chat). 404 unless the flag is on."""
    if not settings.commander_enabled:
        raise HTTPException(status_code=404, detail="commander disabled")
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    con, schema_context, tenant = await _build_commander_session(http_request, req)
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _worker() -> None:
        try:
            for ev in run_commander(
                message, tenant=tenant, schema_context=schema_context,
                registry=_COMMANDER_REGISTRY, llm=get_llm(), con=con,
            ):
                loop.call_soon_threadsafe(queue.put_nowait, ev)
        except Exception as exc:  # never lose the stream on an unexpected error
            from agents.commander import ErrorEvent
            loop.call_soon_threadsafe(queue.put_nowait, ErrorEvent(kind="internal", message=str(exc)))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _STREAM_SENTINEL)
            try:
                con.close()
            except Exception:
                pass

    async def _sse():
        worker = loop.run_in_executor(None, _worker)
        try:
            while True:
                ev = await queue.get()
                if ev is _STREAM_SENTINEL:
                    break
                if await http_request.is_disconnected():
                    break
                yield ev.to_sse()
        finally:
            worker.cancel()

    return StreamingResponse(
        _sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
```

> The `con` is built in the coroutine, then used only inside `_worker` — access is serialized (the coroutine finishes the schema build before the worker runs SQL), so the cross-thread DuckDB use is safe. The async generator never touches `con`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_commander_endpoint.py -v`
Expected: PASS (2 passed). Then confirm the DAG path is untouched:
Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/ -k "chat" -q`
Expected: existing chat tests still PASS.

- [ ] **Step 5: Lint + commit**

```bash
cd aurabackend && ../.venv/Scripts/python.exe -m ruff check --fix shared/config.py api_gateway/routers/chat.py tests/test_commander_endpoint.py --ignore E501,E402,F401,W191,W291,W293,F841,E701,E712,F823
git add aurabackend/shared/config.py aurabackend/api_gateway/routers/chat.py aurabackend/tests/test_commander_endpoint.py
git commit -m "feat(commander): POST /chat/stream SSE endpoint behind AURA_COMMANDER_ENABLED"
```

---

### Task 5: Frontend — `streamMessage` + minimal render

**Files:**
- Modify: `frontend/src/services/api.ts` (add `chatService.streamMessage`)
- Create: `frontend/src/services/__tests__/streamMessage.test.ts`
- Modify: `frontend/src/components/ChatInterface.tsx` (render events behind the flag)

**Interfaces:**
- Consumes: `POST /chat/stream` SSE (events `tool_call`, `tool_result`, `tool_error`, `text`, `error`, `done`).
- Produces: `chatService.streamMessage(message: string, opts: { onEvent: (e: { event: string; data: any }) => void; signal?: AbortSignal }): Promise<void>` and a `parseSSE(chunk: string)` helper.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/services/__tests__/streamMessage.test.ts
import { describe, expect, it } from 'vitest';
import { parseSSEBuffer } from '../api';

describe('parseSSEBuffer', () => {
  it('parses complete event frames and returns the remainder', () => {
    const buf =
      'event: tool_call\ndata: {"name":"run_sql"}\n\n' +
      'event: text\ndata: {"text":"hi"}\n\n' +
      'event: done\ndata: {"reason":"st';
    const { events, rest } = parseSSEBuffer(buf);
    expect(events).toEqual([
      { event: 'tool_call', data: { name: 'run_sql' } },
      { event: 'text', data: { text: 'hi' } },
    ]);
    expect(rest).toBe('event: done\ndata: {"reason":"st');
  });

  it('ignores heartbeat comment lines', () => {
    const { events } = parseSSEBuffer(': heartbeat\n\nevent: done\ndata: {"reason":"stop"}\n\n');
    expect(events).toEqual([{ event: 'done', data: { reason: 'stop' } }]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/services/__tests__/streamMessage.test.ts`
Expected: FAIL — `parseSSEBuffer` is not exported.

- [ ] **Step 3: Implement the parser + streamer**

In `frontend/src/services/api.ts`, add an exported helper and a `streamMessage` on `chatService`:

```ts
export function parseSSEBuffer(buffer: string): {
  events: { event: string; data: any }[];
  rest: string;
} {
  const events: { event: string; data: any }[] = [];
  const frames = buffer.split('\n\n');
  const rest = frames.pop() ?? '';
  for (const frame of frames) {
    const lines = frame.split('\n');
    let event = 'message';
    let dataRaw = '';
    for (const line of lines) {
      if (line.startsWith(':')) continue; // heartbeat/comment
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) dataRaw += line.slice(5).trim();
    }
    if (!dataRaw) continue;
    try {
      events.push({ event, data: JSON.parse(dataRaw) });
    } catch {
      /* incomplete/non-JSON frame — skip */
    }
  }
  return { events, rest };
}
```

```ts
// inside the chatService object (mirror the existing baseUrl/headers usage in this file):
async streamMessage(
  message: string,
  opts: { onEvent: (e: { event: string; data: any }) => void; signal?: AbortSignal; sessionId?: string },
): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeader() },
    body: JSON.stringify({ message, session_id: opts.sessionId }),
    signal: opts.signal,
  });
  if (res.status === 404) throw new Error('commander_disabled');
  if (!res.ok || !res.body) throw new Error(`stream failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseSSEBuffer(buffer);
    buffer = rest;
    for (const e of events) opts.onEvent(e);
  }
}
```

> Match `API_BASE_URL` and the auth-header helper to whatever this file already uses (e.g. the existing `client`/`apiClient` config and the `aura.authToken` localStorage header). Do NOT invent new names — read the top of `api.ts` first and reuse its exact base-URL constant and auth-header function.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/services/__tests__/streamMessage.test.ts`
Expected: PASS (2 passed).

- [ ] **Step 5: Wire a minimal render in `ChatInterface.tsx`**

Behind a flag check (read `import.meta.env.VITE_COMMANDER_ENABLED === 'true'`, or a feature prop). When enabled and the user sends a message, call `chatService.streamMessage` and append UI affordances per event: a "running `run_sql`…" chip on `tool_call`, a small result preview on `tool_result`, an inline error on `tool_error`/`error`, the answer text on `text`, and clear the chip on `done`. Keep the existing non-streaming `sendMessage` path as the default when the flag is off. (No new test required for the render in slice 1; the parser is the tested unit.)

- [ ] **Step 6: Verify frontend gates + commit**

```bash
cd frontend && npx tsc --noEmit && npx eslint src --max-warnings 0 && npx vitest run src/services/__tests__/streamMessage.test.ts
git add frontend/src/services/api.ts frontend/src/services/__tests__/streamMessage.test.ts frontend/src/components/ChatInterface.tsx
git commit -m "feat(commander): frontend SSE streamMessage + minimal render behind flag"
```

---

## Manual smoke (after Task 5, optional but recommended)

With a local Ollama running and a CSV uploaded:

```bash
# backend
cd aurabackend && AURA_COMMANDER_ENABLED=true ../.venv/Scripts/python.exe -m uvicorn api_gateway.main:app --port 8000
# in another shell
curl -N -X POST http://localhost:8000/chat/stream -H 'Content-Type: application/json' \
  -d '{"message":"what is the average amount in sales?"}'
```

Expected: a stream of `event: tool_call` → `event: tool_result` → `event: text` → `event: done`, faster than the DAG `/chat`, and never an empty body.

---

## Self-Review

**1. Spec coverage**
- `complete_with_tools` ReAct seam → Task 1. ✓
- Native override deferred → not in plan (spec says deferred). ✓
- ToolRegistry + run_sql guardrails → Task 2 (using `SQLSafetyValidator`, the corrected guardrail). ✓
- run_commander loop + every-path-emits-event + tool-error-fed-back → Task 3. ✓
- CommanderEvent + `.to_sse()` matching `StreamEvent` convention → Task 3. ✓
- Blocking→async SSE bridge + flag (404 off) → Task 4. ✓
- Silent-empty cure → Tasks 3 (loop) + 4 (endpoint catches worker exceptions). ✓
- Frontend streamMessage + render → Task 5. ✓
- DAG untouched → Global Constraints + Task 4 Step 4 regression check. ✓
- Deferred items (native FC, wider tools, fallback wrapper, token streaming, Data Cards, cockpit) → correctly absent. ✓

**2. Placeholder scan:** No TBD/TODO; every code step has complete code; the two "verify exact name" notes (SQLSafetyValidator API in Task 2, api.ts base-URL/auth in Task 5) point at named source locations to confirm, not vague hand-waving.

**3. Type consistency:** `AssistantTurn(text, tool_calls, finish_reason)`, `ToolCall(id, name, arguments)`, `ToolOutcome(ok, value, error)`, `CommanderToolRegistry.execute(name, arguments, *, tenant, con)`, `run_commander(message, *, tenant, schema_context, registry, llm, con, max_iterations)`, event `.to_sse()`/`event_name` — used identically across Tasks 1→4 and the tests. `_build_commander_session` returns `(con, schema_context, tenant)` consumed positionally in the endpoint. ✓

**Known follow-ups (out of slice 1, tracked for later A-slices):** native function-calling overrides + prompt-caching; tool-aware fallback wrapper (slice 1 lets a provider error become an `ErrorEvent`); token-level streaming; wider tool surface; Data Cards replacing the raw schema block; cockpit panels; deleting the DAG after cutover.
