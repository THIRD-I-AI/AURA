---
description: Backend architecture invariants — persistence lazy-init, SQL identifier quoting, in-process engines, async safety
paths:
  - "aurabackend/**"
---

# Backend Rules

## Persistence layer

- `aurabackend/api_gateway/persistence.py` owns the gateway's SQLAlchemy models,
  async engine, and session factory.
- **Lazy-init via `session_scope()`** — schema is created on first session use,
  NOT at import time and NOT solely from the FastAPI lifespan. Tests that import
  a router directly, without driving the lifespan, still get working tables.
  Do not break this; it is the fix for the P-1 hotfix bug.
- **Cross-router reads** of persistence-backed state go through the repository
  functions in `persistence.py`, never legacy module-attribute imports.
- **Do not drive the ASGI lifespan in tests.** `TestClient` inside a `with` block
  starts it, and the lifespan leaves non-daemon `aiosqlite` threads that block
  `threading._shutdown` — pytest then finishes its tests and hangs forever on
  exit. This cost several 35-minute CI timeouts.

## Async safety

- The deployment runs **one uvicorn worker**. Anything blocking inside an async
  handler freezes every concurrent request for every tenant until it returns.
- Offload blocking work with `asyncio.to_thread` — synchronous LLM calls, DuckDB
  query execution, PDF rendering. `asyncio.wait_for` cannot save you: the loop
  cannot service its own timer while stuck inside a blocking sync call.
- A background task must not write through an engine bound to a different event
  loop. The write fails silently, and so does the error handler if it writes to
  the same database.

## SQL identifiers and literals

- There is **one** quoter: `shared/sql_identifiers.py` (`quote_identifier`,
  `quote_literal`). It rejects a NUL byte, which can truncate a statement in some
  drivers. Import it — never hand-roll a local `_q()`.
- This matters because LLM-generated column names get spliced into raw DDL. Three
  divergent copies once existed and only one had the NUL guard, leaving 68 call
  sites unprotected.

## Service topology

- The counterfactual / financial-audit engine is **NOT** a separate service — it
  runs in-process inside the gateway. Do not add a network hop to reach it.
- FastAPI matches routes in **declaration order**: literal paths must be declared
  before parameterized ones, or `/recovery/pending` is swallowed by
  `/recovery/{id}` and 404s.
- Proxy helpers must forward the caller's `Authorization` header and preserve the
  upstream status code. Returning `resp.json()` while dropping `resp.status_code`
  turns an upstream 401 into a browser-visible HTTP 200 with an error body.
