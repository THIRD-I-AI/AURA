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

- **DSR-001** — `done` — ETL engine's Postgres and Kafka source loaders did
  per-row `INSERT` loops instead of batched inserts (`pipeline/engine.py`),
  violating the repo's own documented bulk-write rule. Fixed: both
  `_load_db_source` and `_load_kafka_source` now build all row values and
  issue a single `conn.executemany()` call. PR #356.
- **DSR-002** — `open` — Streaming pipeline API (`pipeline/streaming/streaming_api.py::start_pipeline`)
  never passes the kwargs that would activate triggers/watermarks/barrier-alignment/
  backpressure — a substantial, correct implementation sits fully unreachable from
  the live HTTP API.
- **DSR-003** — `done` — DuckDB streaming sink's schema field name (`connection`)
  didn't match what the sink code actually read (`path`) — a pipeline built via
  the documented API schema silently lost all data to `:memory:`. Fixed by
  renaming the schema field to `path`, matching every other sink's convention
  of schema-key-equals-config-key. PR #352.
- **DSR-004** — `done` — Streaming filter-transform operator (`>=`/`<=`) was
  advertised by the API schema but was a silent no-op in the engine. Fixed:
  added the two missing branches to `_apply_transforms`'s FILTER handling,
  matching the existing `>`/`<` style. PR #350.
- **DSR-005** — `wontfix` — claimed `scheduler_service/` had no gateway router
  mounted. False by the time this was investigated (2026-09-10): the full
  route set has been proxied via `api_gateway/routers/pipelines.py:711-829`
  since PR #281 (2026-09-01) — jobs CRUD, pause/resume/execute/run,
  executions, admin cleanup, all reachable today. The frontend's
  `SchedulerPanel.tsx` deliberately shows only the simpler in-process
  saved-query scheduler (its own comment, written 2026-08-24 before the
  proxy existed, is now stale but that's a docs nit, not a gap). Building a
  UI for the distributed scheduler_service was considered and explicitly
  declined by the user — no further action.
- **DSR-006** — `done` — File-watcher streaming source claimed CSV/JSON/Parquet
  support; the parser silently returned `[]` for Parquet (and marked the file
  "seen" so it was never retried). Fixed: added `_parse_parquet` using
  `pyarrow.parquet`, matching the existing `_parse_csv`/`_parse_json` style.
  PR #354.

## Data Scientist

- **DSR-007** — split into staged sub-items (2026-09-11) — `uasr/causal_rl_evaluator.py`
  advertises a doubly-robust DR-Learner estimator in its docstring; the
  actual confidence interval is a hardcoded heuristic
  (`abs(improvement)*0.2 + 0.05`), and its `conformal_calibration`
  constructor param is accepted but never read (nobody in the codebase ever
  passes `True`). Investigated 2026-09-10: `counterfactual_service.engine
  .run_estimators` (the real DR-Learner path) requires a `treatment`,
  `outcome`, and causal `dag` — none of which exist as a concept in shim
  selection today (candidates are compared by before/after drift score, not
  a treatment/outcome/DAG spec). Further investigated 2026-09-11: real
  per-row treatment variation doesn't exist anywhere in the current flow —
  a candidate shim is applied to a whole batch or not at all. The one place
  genuine treatment variation exists is `ShimRouter` (S18.1c), which routes
  **whole batches** to one version at a time over a sequence of batches
  (not rows within a batch) — so a real causal estimate needs a *history*
  of many realized batches accumulated via canary promotion, turning shim
  selection from a single-batch comparison into an online/incremental
  decision. User chose to commit to this properly rather than defer or
  fail-loudly, and approved a 3-stage breakdown:
  - **DSR-007a** — `done` — plumbing only, no causal math, no behavior
    change. Added `OutcomeRecord` + `record_outcome()`/`outcome_history()`
    (bounded, per-source) to `ShimRouter`, wired into `mapek_worker.py`'s
    `_run_forever` right after `ShimRouter.apply()` and drift detection.
    Outcome scalar is `drift_detected` (0.0/1.0) — the one signal both the
    classical and martingale detector paths always populate. PR #366.
  - **DSR-007b** — `open` — build the treatment/outcome/DAG mapping from
    that history and call `run_estimators` periodically (e.g. at each
    `promote_canary` check). Needs a design proposal before implementation.
  - **DSR-007c** — `open` — replace/augment `promote_canary`'s current
    simple-average-threshold rule with the causal estimate + CI, and decide
    how `CausalRLEvaluator.select_winner` relates to it (cold-start
    fallback vs. full replacement).
- **DSR-008** — `done` — `/counterfactual/info` advertised `double_ml` as
  fully doubly-robust regardless of whether `econml` was actually installed;
  when it wasn't, `double_ml` silently fell back to plain linear regression
  with no signal to the caller that the requested method degraded. Fixed:
  added `CounterfactualEstimate.degraded` (True iff the DoWhy fallback ran),
  excluded from the audit-artifact hash basis for backward compat with
  pre-existing signed artifacts, and `econml_available()` added to
  `/counterfactual/info`. PR #368.
- **DSR-009** — `done` — `uasr/conformal_martingale.py` (a fully-worked
  anytime-valid drift statistic) had no caller anywhere in the UASR wiring —
  implemented, never wired in. Investigated 2026-09-11: the detector it
  would replace, `WassersteinMartingaleDetector` (`martingale.py`), was
  itself already opt-in and off by default (`UASR_USE_MARTINGALE_DETECTOR`)
  — AND was a complete dead no-op even when turned on, since nothing in the
  codebase ever called its `register_baseline()` (`mapek_worker.py` never
  called it, and `/uasr/baseline` in `service.py` only registered baselines
  for the classical detector and semantic gateway). Fixed: added a new
  `ConformalMartingaleRegistry` adapter (`conformal_martingale.py`) exposing
  the same public interface `WassersteinMartingaleDetector` had, so
  `mapek_worker.py`'s `_analyze_martingale` call sites needed no changes —
  only the constructor. Also wired real baseline registration into both
  places the classical detector re-baselines and into `POST /uasr/baseline`,
  so the replacement actually fires rather than shipping a differently-
  shaped dead detector. PR #363.

## Data Analyst / platform coherence

- **DSR-010** — `done` — originally framed as "three coexisting
  agent-orchestration engines with unreconciled, contradictory claims about
  which is authoritative, needs a decision." Investigated 2026-09-10: that
  framing didn't hold up. Each engine serves a different job — LangGraph
  orchestrator is already canonical for `POST /chat` (always on by default);
  Commander is a deliberate opt-in alternative for `POST /chat/stream`,
  gated behind `AURA_COMMANDER_ENABLED` (defaults `False` — its own router
  docstring is honest about this); Planner+DAGExecutor powers an unrelated
  feature, webhook-triggered automation in `inbound_hooks.py`. The one real
  bug was `commander.py`'s own module docstring falsely claiming present-tense
  it "replaces" the LangGraph DAG. Fixed: corrected the docstring to state
  the actual relationship. No canonical-engine decision was needed. PR #361.
- **DSR-011** — `done` — `agents/planner.py`'s LLM-facing agent roster only
  knew 7 of the 12 agents `DAGExecutor` can run — 5 agent types could never
  be targeted by a generated plan. Fixed: added `IntentAgent`,
  `ExecutionAgent`, `AnalysisAgent`, `VisualizationAgent`, `MonitorAgent` to
  `AGENT_ROSTER`, with a regression test asserting the roster and
  `DAGExecutor`'s `AGENT_MAP` stay in sync in both directions. PR #358.
- **DSR-012** — `open` — DPC (independent pandas cross-check) SQL verification
  is off by default on the chat path (`AURA_DPC_CHAT_ENABLED=0`) despite the
  orchestrator graph having a permanent `verify_run` node — ordinary chat
  answers ship with no cross-check unless an operator explicitly opts in.

## Security/coherence (cross-cutting, affects trust in the above)

- **DSR-013** — `false-positive` — the exhaustive code-read workflow claimed the
  three audit-ledger read endpoints (`audit_ledger_verify`, `audit_ledger_proof`,
  `audit_ledger_subject_history`) take an unauthenticated `tenant_id` query
  param. Personally verified 2026-09-10: `api_gateway/routers/counterfactual.py:202-226`
  already derives `tenant` from `Depends(require_tenant)` (the verified JWT) —
  the service-layer function's `tenant_id` arg is fine, it's an internal helper
  below the trust boundary, and the router docstrings explain exactly why. The
  workflow read the service-layer signature without confirming how its caller
  actually supplies the argument. Lesson: verify every finding against current
  code before acting on it, even from a large, well-cited automated sweep.
- **DSR-014** — `false-positive` — the exhaustive-read workflow flagged
  `PIIMaskingMiddleware` as "implemented but not wired into the shared service
  factory," framed as a gap. Attempted the fix 2026-09-10: wiring it globally
  into `create_service()` broke authentication platform-wide (`test_auth.py`,
  `test_e2e_chat.py` — 3 failures) because `email` is in `PII_KEYS` and every
  login/register payload carries `email` as a required credential, not PII to
  strip. Investigated further: `ingestion_service/main.py:36-38` **already**
  wires this middleware itself, correctly scoped to just that service — the
  one place genuine ERP/employee PII actually flows through inbound JSON
  bodies. The "gap" was a deliberate, correct per-service opt-in design, not
  an oversight. Reverted the global-wiring attempt (branch abandoned locally,
  never pushed). Lesson (second time, after DSR-013): a "not wired into X"
  finding proves absence, not that presence would be correct — verify the
  fix actually works before trusting the framing, not just the citation.
- **DSR-015** — `done` — `UASR_RISK_TIERED` defaulted `false`: validated
  self-heal shims auto-deployed unconditionally by default; human approval
  was opt-in, not the default posture. User decided: default-safe. Fixed:
  flipped the default to `true` in both `uasr/service.py` and
  `deployment_summary()`'s reporting default, so `/health` doesn't
  misreport the active mode. PR #360.

## Execution order (first pass)

Mechanical, low-risk, no architectural judgment call needed — safe for an
autonomous loop to pick up immediately (all personally re-verified against
current `main` on 2026-09-10, not just taken from the exhaustive-read
workflow's output): **DSR-004, DSR-003, DSR-006, DSR-001, DSR-011** (DSR-014
dropped after attempting it revealed it was already correct as-is — see
above). All five are `done` as of 2026-09-10 — this mechanical pass is
complete.

Decisions requested from the user 2026-09-10 and resolved: DSR-005
(`wontfix` — already backend-resolved, frontend UI declined), DSR-010
(`done` — turned out to be a docstring bug, not an architecture decision),
DSR-015 (`done` — default-safe). DSR-009 (`done` — wired in + fixed baseline
registration) and DSR-007 (split into DSR-007a/b/c, in progress — design the
causal spec properly, staged) were decided and are tracked above.

Still needs a product/architecture decision before code changes (flag for
human input, do not silently pick a side): **DSR-002, DSR-012**. DSR-002
turned out deeper than a wiring fix on inspection (no API schema exists at
all for the settings in question, not just a missed pass-through) —
scoping was deferred pending further investigation.
