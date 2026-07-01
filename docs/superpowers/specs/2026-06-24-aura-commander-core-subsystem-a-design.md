# Subsystem A — Commander Core (provider-agnostic tool-loop)

**Status:** Detailed design. Parent: [AURA Commander Platform umbrella](./2026-06-24-aura-commander-platform-umbrella-design.md), Subsystem A.
**Date:** 2026-06-24
**Author:** Mounith + Claude (Opus 4.8)

---

## Goal

Replace the rigid `IntentAgent` + `run_orchestrator` LangGraph DAG (5–6 serial
LLM calls = the measured ~41 s chat latency) with **one provider-agnostic
model-in-a-loop** that calls AURA's own capabilities as tools, streams its
progress, runs on local **or** cloud models, and never returns a silent empty
response.

This document specifies the spine: the provider seam, the loop, the tool
registry, the streaming protocol, the guardrails, error handling, the first
`run_sql`-on-Ollama vertical slice, and the migration strategy. The wider tool
surface, Data Cards (B), and the standing-work engine (E) build on this; they
are out of scope here.

---

## Migration strategy (decision)

**Coexistence-then-cutover.** A new streaming endpoint `POST /chat/stream`
runs the commander loop *alongside* the untouched `POST /chat` (DAG) path,
behind `AURA_COMMANDER_ENABLED` (default `false`). The old path keeps working
for every existing client and test. Once the loop is proven on real traffic,
flip the default; delete the DAG in a later, separate cleanup once nothing
calls it.

Rationale: the DAG is load-bearing and has green tests; a hard replace would
put correctness and latency risk in the same change. Coexistence lets us prove
the loop in production behind a flag and roll back instantly by clearing the
flag. This is a reversible decision — revisit at the spec-review gate if you
want a hard cutover instead.

---

## Architecture: who owns what

The cleanest separation puts **one turn** in the provider and **the loop** in
the app:

```
 ┌──────────────────────────────────────────────────────────────┐
 │  api_gateway/routers/chat.py                                   │
 │    POST /chat/stream  → StreamingResponse(text/event-stream)   │
 │    bridges the (blocking) commander loop onto async SSE        │
 └───────────────────────────┬──────────────────────────────────┘
                             │ iterates CommanderEvent
 ┌───────────────────────────▼──────────────────────────────────┐
 │  agents/commander.py        run_commander(...)                │
 │    • owns the loop, max_iterations, the message transcript    │
 │    • streams typed events                                     │
 │    • executes tools through the registry (guardrails+tenant)  │
 └──────────┬───────────────────────────────────┬───────────────┘
            │ complete_with_tools(messages,tools)│ execute(name,args,tenant)
 ┌──────────▼─────────────────┐    ┌─────────────▼────────────────┐
 │ shared/llm_provider.py     │    │ agents/tool_registry.py       │
 │   LLMProvider              │    │   ToolRegistry / Tool         │
 │   .complete_with_tools()   │    │   run_sql (slice 1)           │
 │   → AssistantTurn          │    │   guardrails at the boundary  │
 │   (1 reasoning turn only)  │    └──────────────────────────────┘
 └────────────────────────────┘
```

**Why the provider only does one turn.** If the provider executed app tools, the
LLM layer would import DuckDB, tenancy, and the registry — exactly the coupling
`llm_provider.py` exists to avoid. Instead the provider answers a narrow
question: *"given this conversation and these tool specs, what is the assistant's
next turn — final text, or a request to call tools?"* The app owns
orchestration, guardrails, tenancy, and streaming. The loop is then testable
with a scripted fake provider and no network.

---

## The provider seam: `complete_with_tools`

Added to the `LLMProvider` ABC in `shared/llm_provider.py`. New value types live
beside it (kept tiny and provider-neutral):

```python
@dataclass
class ToolCall:
    id: str                      # correlation id (provider-supplied or synthesised)
    name: str
    arguments: Dict[str, Any]    # already JSON-parsed

@dataclass
class AssistantTurn:
    text: Optional[str]          # assistant prose, if any
    tool_calls: List[ToolCall]   # empty when the model is answering, not calling
    finish_reason: str           # "stop" | "tool_calls" | "length" | "error"
```

```python
class LLMProvider(ABC):
    ...
    def complete_with_tools(
        self,
        messages: List[Dict[str, Any]],   # OpenAI-style transcript
        tools: List[Dict[str, Any]],      # JSON-schema tool specs
        **kwargs: Any,
    ) -> AssistantTurn:
        """One reasoning turn. Default = ReAct/JSON fallback over generate();
        native-function-calling providers override."""
        return self._complete_with_tools_react(messages, tools, **kwargs)
```

### Default implementation = ReAct/JSON fallback (works on every provider)

The base `_complete_with_tools_react` builds a system instruction describing the
tools and a strict JSON action contract, calls the existing **sync**
`self.generate(...)`, and parses the reply:

- The prompt instructs the model to reply with **either**
  `{"action": "final", "text": "..."}` **or**
  `{"action": "tool", "tool": "<name>", "arguments": {...}}`.
- Parsing reuses the same fence-stripping `generate_json` already does, plus a
  tolerant "first balanced JSON object" extractor for models that wrap JSON in
  prose.
- A reply that names a tool → `AssistantTurn(text=None, tool_calls=[ToolCall(...)],
  finish_reason="tool_calls")`. A `final` reply → `AssistantTurn(text=..., [],
  "stop")`. An unparseable reply → one bounded reformat retry, then
  `finish_reason="error"` (the loop surfaces this as an error event, never a
  silent empty).

This is the path the **air-gapped / Ollama** slice uses. It depends on no vendor
feature, so on-prem never regresses when a cloud provider changes its API.

### Native override (later, not slice 1)

`GroqProvider` / `OpenAIProvider` / `GeminiProvider` may override
`complete_with_tools` to use real function-calling (`tools=`/`tool_choice=`),
returning the same `AssistantTurn`. This is a per-provider optimisation behind
the identical contract; slice 1 ships only the base ReAct path so the loop is
proven once, provider-independently.

> Note: `complete_with_tools` is **not** routed through `_CachedProvider` /
> `_FallbackProvider` response caching — multi-turn tool transcripts are not
> safely cache-keyable the way single prompts are. The fallback *chain*
> (cascade on `LLMRateLimitError`) is preserved by having the commander catch
> `LLMRateLimitError` and call `get_llm(force_new=True)` against the next
> provider, or by a thin tool-aware fallback wrapper. Slice 1 uses a single
> resolved provider and treats rate-limit as an error event; the fallback
> wrapper is a fast-follow.

---

## The loop: `agents/commander.py`

```python
def run_commander(
    message: str,
    *,
    tenant: str,
    schema_context: Dict[str, Any],
    ready_results: Optional[Dict[str, Any]],   # from shared layer (B); None in slice 1
    registry: ToolRegistry,
    llm: LLMProvider,
    con: Any,                                   # tenant-scoped DuckDB connection
    max_iterations: int = 8,
) -> Iterator[CommanderEvent]:
    transcript = [
        {"role": "system", "content": build_system_prompt(schema_context, ready_results, registry)},
        {"role": "user", "content": message},
    ]
    for _ in range(max_iterations):
        try:
            turn = llm.complete_with_tools(transcript, registry.specs())
        except LLMRateLimitError as exc:
            yield ErrorEvent(kind="rate_limit", message=str(exc)); return
        except Exception as exc:
            yield ErrorEvent(kind="provider", message=str(exc)); return

        if turn.finish_reason == "error":
            yield ErrorEvent(kind="unparseable", message="model produced no valid action"); return

        if turn.text and not turn.tool_calls:
            yield TextEvent(text=turn.text)
            yield DoneEvent(reason="stop"); return

        transcript.append(_assistant_msg(turn))           # record the tool request
        for call in turn.tool_calls:
            yield ToolCallStartEvent(name=call.name, arguments=call.arguments)
            outcome = registry.execute(call.name, call.arguments, tenant=tenant, con=con)
            if outcome.ok:
                yield ToolResultEvent(name=call.name, result=outcome.value)
            else:
                yield ToolErrorEvent(name=call.name, message=outcome.error)
            transcript.append(_tool_result_msg(call.id, outcome))   # feed back for self-correction

    yield DoneEvent(reason="max_iterations")
```

Key properties:
- **Every exit path yields a terminal event** (`DoneEvent` or `ErrorEvent`).
  There is no code path that returns nothing — this is the structural cure for
  the `chat.py:605` silent-empty bug.
- **Tool errors are fed back, not fatal.** An invalid-SQL `ToolErrorEvent` is
  appended to the transcript so the model can self-correct on the next iteration,
  bounded by `max_iterations`.
- **Tenancy is passed in, never model-supplied.** `tenant` and `con` come from
  the verified request context; the model cannot name a tenant.

### Events (`CommanderEvent`)

A small typed union, each serialisable to one SSE frame:

| Event | Fields | SSE `event:` |
|---|---|---|
| `ToolCallStartEvent` | `name`, `arguments` | `tool_call` |
| `ToolResultEvent` | `name`, `result` (truncated preview + row_count) | `tool_result` |
| `ToolErrorEvent` | `name`, `message` | `tool_error` |
| `TextEvent` | `text` | `text` |
| `ErrorEvent` | `kind`, `message` | `error` |
| `DoneEvent` | `reason`, `elapsed_ms` | `done` |

Each carries `.to_sse()` producing `event: <name>\ndata: <json>\n\n`, matching
the existing `StreamEvent.to_sse()` convention in `shared/streaming_manager.py`
so the frontend's SSE parsing stays uniform.

> **Streaming granularity (slice 1):** events are emitted at **step**
> boundaries (tool call → result → final text block), not intra-token. Because
> `generate()` is non-streaming, the perceived-latency and transparency win
> comes from showing the loop's progress as it happens — the user sees
> "running SQL… got 42 rows… here's the answer" instead of a 41 s spinner.
> True token-level streaming (provider streaming APIs feeding `text_delta`
> events) is a deferred enhancement under the same event protocol.

---

## Bridging blocking → async SSE

`generate()` is blocking `httpx`; FastAPI's `StreamingResponse` needs an async
generator. Running the loop inline would block the event loop. The endpoint runs
the loop in a worker thread and drains an `asyncio.Queue`:

```python
@router.post("/chat/stream")
async def chat_stream(req: ChatStreamRequest, http_request: Request) -> StreamingResponse:
    if not settings.commander_enabled:
        raise HTTPException(404)                      # flag off → behave as if absent
    tenant = current_workspace_id(http_request)
    con, schema_context = _build_tenant_session(tenant, req)   # reuse existing builders
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _worker():
        try:
            for ev in run_commander(req.message, tenant=tenant, schema_context=schema_context,
                                    ready_results=None, registry=REGISTRY, llm=get_llm(), con=con):
                loop.call_soon_threadsafe(queue.put_nowait, ev)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)
            con.close()

    async def _sse():
        worker = loop.run_in_executor(None, _worker)   # schedule, do not await
        try:
            while True:
                ev = await queue.get()
                if ev is _SENTINEL: break
                if await http_request.is_disconnected(): break
                yield ev.to_sse()
        finally:
            worker.cancel()                            # best-effort on early disconnect

    return StreamingResponse(_sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

(The worker future is scheduled but not awaited; the async generator drains the
queue until the sentinel. Disconnect-checking and the sentinel mirror the
existing `stream.py` generator.) This keeps the gateway's event loop responsive
while the blocking LLM/tool work runs off-thread.

---

## Tool registry: `agents/tool_registry.py`

```python
@dataclass
class ToolOutcome:
    ok: bool
    value: Any = None
    error: Optional[str] = None

@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]            # JSON schema
    handler: Callable[..., ToolOutcome]   # (arguments, *, tenant, con) -> ToolOutcome
    mutating: bool = False

class ToolRegistry:
    def specs(self) -> List[Dict[str, Any]]: ...           # for the model
    def execute(self, name, arguments, *, tenant, con) -> ToolOutcome: ...
```

`execute` is the single choke point: it looks up the tool, validates `arguments`
against the tool's JSON schema, then calls the handler. An unknown tool name or
schema-invalid arguments returns `ToolOutcome(ok=False, ...)` — surfaced as a
`tool_error` and fed back to the model, never an exception that kills the stream.

### Slice 1 registers exactly one tool: `run_sql`

```python
run_sql = Tool(
    name="run_sql",
    description="Run a single read-only SQL SELECT against the user's loaded "
                "datasets and return rows. Use the table/column names from the "
                "schema context. SELECT only; no DDL/DML.",
    parameters={
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "a single SELECT statement"}},
        "required": ["sql"],
    },
    handler=_run_sql_handler,
    mutating=False,
)
```

`_run_sql_handler(args, *, tenant, con)`:
1. `_assert_select_only(args["sql"])` — reject anything but a single SELECT/CTE
   (reuses the sqlglot validator already in `mcp_servers/aura_mcp_server.py`).
   On rejection → `ToolOutcome(ok=False, error="Only a single SELECT is allowed: ...")`.
2. Enforce a hard row cap (wrap/append `LIMIT` per the existing query path).
3. Execute on the **tenant-scoped** `con` (built by the endpoint from the
   verified tenant; the model never supplies a connection or tenant).
4. Return `ToolOutcome(ok=True, value={"columns": [...], "rows": [...], "row_count": n})`.

The guardrails are deterministic and model-independent: a weaker local model
that emits `DROP TABLE` is stopped at the boundary exactly as a frontier model
would be.

---

## System prompt

`build_system_prompt(schema_context, ready_results, registry)` assembles:
- a short role line ("AURA's data commander; answer using the tools");
- the **schema context** (slice 1 reuses the existing `build_schema_context_cached`
  output already passed to the DAG — table/column names and types). In later
  slices this is replaced by Data Cards (B);
- ready results from the shared layer (None in slice 1);
- the JSON action contract (for the ReAct base path) — omitted automatically when
  a provider uses native function-calling.

The schema block is the stable, cacheable prefix (prompt-caching lands when
native providers are wired; the ReAct path still benefits from a stable prefix).

---

## Error handling (the silent-empty cure)

| Failure | Old DAG behaviour | Commander behaviour |
|---|---|---|
| Model returns nothing | `execution_result=None`, no message (`chat.py:605`) | `ErrorEvent(kind="unparseable")` after one reformat retry |
| Provider 429 / quota | swallowed → "empty SQL response" | `ErrorEvent(kind="rate_limit", message=...)` |
| Invalid SQL from model | pipeline error string | `ToolErrorEvent` fed back → model self-corrects, bounded |
| Model loops without answering | n/a | `DoneEvent(reason="max_iterations")` with partial text |

Every branch produces a frame the frontend can render. No path is silent.

---

## Frontend (slice 1, minimal)

- `frontend/src/services/api.ts`: add `chatService.streamMessage(message, {onEvent})`
  using `fetch` + `ReadableStream` SSE parsing (POST body needed → `EventSource`
  can't POST, so use fetch-stream). The existing `sendMessage` (single POST to
  `/chat`) is untouched.
- `ChatInterface.tsx`: when `AURA_COMMANDER_ENABLED`, render the event stream —
  a "running `run_sql`…" step chip, then the result table, then the answer.
  Behind the flag; the existing non-streaming render stays the default.

Frontend is in-scope per the umbrella but kept thin in slice 1: prove the stream
renders end-to-end; richer cockpit panels are Subsystem F.

---

## Testing

Tier A (no network, always-run):
1. **Loop with a `FakeToolProvider`** — scripted `AssistantTurn`s drive a
   deterministic `run_commander`: asserts the exact event sequence for
   (a) answer-without-tools, (b) one tool call then answer, (c) tool error then
   self-correct then answer, (d) max-iterations exhaustion.
2. **`_complete_with_tools_react` parsing** — fenced JSON, JSON-wrapped-in-prose,
   `final` vs `tool` actions, unparseable → one retry → `finish_reason="error"`.
3. **`run_sql` guardrails** — `DROP`/multi-statement/DDL rejected with
   `ToolOutcome.ok is False` and no side-effect table created (mirrors the
   pattern in `tests/test_sql_injection_hardening.py`); valid SELECT returns rows;
   row cap enforced.
4. **Registry** — unknown tool name and schema-invalid arguments both return
   `ok=False`, never raise.
5. **SSE endpoint** — `TestClient` POST to `/chat/stream` with a fake provider;
   assert the response is `text/event-stream` and the framed event order; assert
   `404` when `AURA_COMMANDER_ENABLED` is false.

Tier B (gated):
6. **Ollama smoke** — `skipif` no local Ollama: a real `run_sql`-answerable
   question over a tiny fixture returns a `done` event with a non-empty answer.

Existing `/chat` (DAG) tests stay green — that path is untouched.

---

## Slice boundary (what "Subsystem A, slice 1" delivers)

A working `POST /chat/stream` that, on **Ollama**, takes a natural-language
question about loaded data, calls `run_sql` under the existing guardrails,
streams `tool_call → tool_result → text → done`, and never returns a silent
empty — behind `AURA_COMMANDER_ENABLED`, with `POST /chat` unchanged.

**Explicitly deferred** to later A-slices / subsystems: native function-calling
overrides + prompt-caching; the wider tool surface (`create_pipeline`,
`attach_pipeline`, `run_audit`, `create_dashboard`, `get_data_card`,
`get_ready_result`); the tool-aware fallback wrapper; token-level streaming;
Data Cards (B) replacing the raw schema block; cockpit panels (F); deleting the
DAG.

---

## Risks

- **Blocking-in-async bridge** is the one genuinely fiddly piece; the threadpool
  + `call_soon_threadsafe` + sentinel pattern is specified above and mirrors
  `stream.py`. The loop test uses the fake provider so the bridge is tested
  separately from model behaviour.
- **Local-model JSON discipline** is weaker; the one reformat retry + the
  deterministic guardrails contain it. If a local model can't hold the action
  contract at all, that's a model-selection finding for Subsystem D, not a loop
  bug.
- **Coexistence drift** — two chat paths to maintain until cutover. Mitigated by
  the flag defaulting off and a tracked cleanup task to delete the DAG once the
  commander is the default.
