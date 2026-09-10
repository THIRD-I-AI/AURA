# AURA Data Suite Completion Roadmap

AURA's primary goal (per the user, 2026-09-10) is a full data suite that does the
jobs of a **data analyst**, a **data engineer**, and a **data scientist**. The
financial/forensic audit engine is one component of that suite, not the product's
core identity. This doc tracks the gap between what an exhaustive code read
(2026-09-10, `docs/BUG_REGISTRY.md`-adjacent — see memory
`project_aura_product_understanding.md`) found actually wired and live versus
what's built but disconnected, and turns it into a worked backlog.

Same discipline as `docs/BUG_REGISTRY.md`: one item, one branch, one PR. Status
`open` until picked up, `in-progress` while a branch is active, `done` with the
PR link once merged. Items are grouped by which of the three roles they serve.

## Data Engineer

- **DSR-001** — `open` — ETL engine's Postgres and Kafka source loaders do
  per-row `INSERT` loops instead of batched inserts (`pipeline/engine.py`),
  violating the repo's own documented bulk-write rule.
- **DSR-002** — `open` — Streaming pipeline API (`pipeline/streaming/streaming_api.py::start_pipeline`)
  never passes the kwargs that would activate triggers/watermarks/barrier-alignment/
  backpressure — a substantial, correct implementation sits fully unreachable from
  the live HTTP API.
- **DSR-003** — `open` — DuckDB streaming sink's schema field name (`connection`)
  doesn't match what the sink code actually reads (`path`) — a pipeline built via
  the documented API schema silently loses all data to `:memory:`.
- **DSR-004** — `open` — Streaming filter-transform operator (`>=`/`<=`) is
  advertised by the API schema but is a silent no-op in the engine.
- **DSR-005** — `open` — `scheduler_service/` (a fully-built distributed scheduler
  with leader election) has no gateway router mounted — unreachable from the
  frontend, which instead talks to a simpler in-process scheduler. Needs a
  decision: mount it for real, or remove the dead surface/panel referencing it.
- **DSR-006** — `open` — File-watcher streaming source claims CSV/JSON/Parquet
  support; the parser silently returns `[]` for Parquet (and marks the file
  "seen" so it's never retried).

## Data Scientist

- **DSR-007** — `open` — `uasr/causal_rl_evaluator.py` advertises a doubly-robust
  DR-Learner estimator in its docstring; the actual confidence interval is a
  hardcoded heuristic (`abs(improvement)*0.2 + 0.05`), and its
  `conformal_calibration` constructor param is accepted but never read.
- **DSR-008** — `open` — `/counterfactual/info` advertises `double_ml` as fully
  doubly-robust regardless of whether `econml` is actually installed; when it
  isn't, `double_ml` silently falls back to plain linear regression with no
  signal to the caller that the requested method degraded.
- **DSR-009** — `open` — `uasr/conformal_martingale.py` (a fully-worked
  anytime-valid drift statistic) has no caller anywhere in the UASR wiring —
  implemented, never wired in.

## Data Analyst / platform coherence

- **DSR-010** — `open` — Three coexisting agent-orchestration engines
  (Planner+DAGExecutor, LangGraph orchestrator, Commander) with unreconciled,
  partly-contradictory claims about which is authoritative. `commander.py`'s
  own docstring falsely claims it replaces the LangGraph DAG that's actually
  driving `POST /chat`. Needs a decision on which is canonical before further
  work builds on any of them.
- **DSR-011** — `open` — `agents/planner.py`'s LLM-facing agent roster only
  knows 7 of the 12 agents `DAGExecutor` can run — 5 agent types can never be
  targeted by a generated plan.
- **DSR-012** — `open` — DPC (independent pandas cross-check) SQL verification
  is off by default on the chat path (`AURA_DPC_CHAT_ENABLED=0`) despite the
  orchestrator graph having a permanent `verify_run` node — ordinary chat
  answers ship with no cross-check unless an operator explicitly opts in.

## Security/coherence (cross-cutting, affects trust in the above)

- **DSR-013** — `open` — Three audit-ledger read endpoints
  (`audit_ledger_verify`, `audit_ledger_proof`, `audit_ledger_subject_history`)
  take an unauthenticated `tenant_id` query param with no JWT cross-check —
  unguarded cross-tenant read.
- **DSR-014** — `open` — `PIIMaskingMiddleware` is correctly implemented but not
  wired into `shared/service_factory.py::create_service()`, the single place
  every microservice's middleware stack is built.
- **DSR-015** — `open` — `UASR_RISK_TIERED` defaults `false`: validated self-heal
  shims auto-deploy unconditionally by default; human approval is opt-in, not
  the default posture. Needs a product decision (default-safe vs default-fast),
  not just a mechanical fix.

## Execution order (first pass)

Mechanical, low-risk, no architectural judgment call needed — safe for an
autonomous loop to pick up immediately: **DSR-013, DSR-014, DSR-004, DSR-003,
DSR-006, DSR-001**.

Needs a product/architecture decision before code changes (flag for human
input, do not silently pick a side): **DSR-005, DSR-010, DSR-015**.

Larger, worth an ultracode workflow each: **DSR-002 (streaming wiring),
DSR-007/DSR-009 (causal estimator correctness), DSR-011 (planner roster)**.
