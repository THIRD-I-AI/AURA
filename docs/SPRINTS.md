# AURA — Sprint Registry

The public sprint registry. **Both developers update this file** when
they ship a sprint, claim an in-flight one, or want to reserve an
upcoming one. Replaces the local-only `MEMORY.md` files each Claude
maintained pre-2026-05-19.

See `CLAUDE.md` for the sprint-numbering convention and commit style.

## Sprint flow + dependencies

```mermaid
graph TD
    %% Foundation
    S7([S7: Foundation<br/>TRAIGA + LangGraph + MCP])

    %% Counterfactual audit engine — Sprints 8 through 16
    subgraph CF [Counterfactual Audit Engine]
        direction TB
        S8([S8: Operator card<br/>4-est × 4-ref fanout])
        S9([S9: Auditor PDF + ED25519])
        S10([S10: aura-counterfactual SDK])
        S11([S11: Determinism + Layer 10])
        S12([S12: EconML LinearDR])
        S13([S13: Bulk replay + propensity])
        S14([S14: Auto-challenge + viz])
        S15([S15: ForestDR + CATE])
        S16([S16: Conformal CATE intervals])
        S8 --> S9 --> S10 --> S11 --> S12 --> S13 --> S14 --> S15 --> S16
    end

    %% Five enterprise pillars
    subgraph P2 [Pillar 2 · Multi-Modal Fabric]
        S17([S17: FAISS + DuckDB spatial])
    end
    subgraph P1 [Pillar 1 · Self-Healing UASR]
        S18([S18: Causal-RL primitives])
        S18_1([S18.1: Martingale into worker])
        S18_1b([S18.1b: CausalRL into recovery])
        S18_1c([S18.1c: ShimRouter canary])
    end
    subgraph P3 [Pillar 3 · TRAIGA Governance]
        S19([S19: Merkle + STH + SDK verify])
    end
    subgraph P4 [Pillar 4 · Distributed Streaming Fabric]
        direction TB
        S20a([S20a: ABS + Watermarks + Triggers + PID])
        S20b([S20b: LISTEN/NOTIFY + advisory lock])
        S20_1([S20.1: Primitives into live engine])
        S20_2([S20.2: Worker wiring])
    end
    subgraph P5 [Pillar 5 · Service Factory]
        direction TB
        S21a([S21a: OpenAPI → Pydantic models])
        S21b([S21b: Sync Client · 101 methods])
        S21c([S21c: AsyncClient mirror])
        S21d([S21d: Multi-service clients])
        S21a --> S21b --> S21c
    end

    %% Performance audit burn-down
    subgraph PERF [Performance Audit Burn-Down]
        direction TB
        SEC([Security: 12 Dependabot → 0])
        P1d([P-1: In-memory → SQLAlchemy<br/>findings #1 + #7])
        P2a([P-2a: File metadata cache<br/>finding #2])
        P2b{{P-2b: Schema context cache<br/>finding #5}}
        P2c{{P-2c: Lineage materialised view<br/>finding #8}}
        P3_audit{{P-3: sqlglot + pooling<br/>findings #3/#4/#6}}
        SEC --> P1d --> P2a --> P2b --> P2c --> P3_audit
    end

    %% Analytic depth
    subgraph AD [Analytic Depth]
        S22([S22: TMLE 6th estimator])
        S23([S23: E-value sensitivity])
        S24([S24: Disagreement auto-challenge])
    end

    %% Operational readiness
    subgraph OPS [Operational Readiness]
        direction TB
        S25([S25: Code health hygiene])
        S26([S26: OpenTelemetry])
        S27([S27: Docker per-tier images])
        S28([S28: CI coverage expansion])
        S29([S29: Integration testing])
        S30([S30: CI/CD GHCR pipeline])
        S25 --> S26 --> S27 --> S28 --> S29 --> S30
    end

    %% Chronological backbone
    S7 --> S8
    S16 --> S17 --> S18 --> S19 --> S20a --> S20b --> S21a
    S21c --> SEC
    P2a --> S22 --> S23 --> S24
    S24 --> S25

    %% Integration wiring (was deferred, now shipped)
    S18 --> S18_1 --> S18_1b --> S18_1c
    S20a --> S20_1
    S20b --> S20_2
    S21c --> S21d

    %% Status legend via fill colors:
    %% rounded green = shipped, hex blue = in flight,
    %% hex yellow = backlog open, trapezoid grey = deferred
    classDef done fill:#d5e8d4,stroke:#82b366,color:#000
    classDef flight fill:#dae8fc,stroke:#6c8ebf,color:#000
    classDef backlog fill:#fff2cc,stroke:#d6b656,color:#000
    classDef deferred fill:#f5f5f5,stroke:#999,color:#666,stroke-dasharray:5 5
    class S7,S8,S9,S10,S11,S12,S13,S14,S15,S16,S17,S18,S18_1,S18_1b,S18_1c,S19,S20a,S20b,S20_1,S20_2,S21a,S21b,S21c,S21d,SEC,P1d,P2a,P2b,P2c,P3_audit,S22,S23,S24,S25,S26,S27,S28,S29,S30 done
```

**Legend**
- **🟢 Rounded** (`S7`, `S8`, `S17`, …) — shipped to `main` with CI green
- **🔵 Hexagon, blue** (none currently) — currently in flight on a feature branch
- **🟡 Hexagon, yellow** (none currently) — in the backlog, ready to start
- **⬜ Trapezoid, dashed** (none currently) — deferred or future

**Dependencies**
- **Solid arrow** — chronological order or hard prerequisite (e.g., S12 builds on S11's determinism contract)
- **Dashed arrow** — "this will wire into that later" (e.g., S18 primitives → S18.1 integration into the live MAPEK worker)

## Current status

All 5 enterprise pillars are feature-complete. All deferred integration
sprints (S18.1b, S18.1c, S20.1, S20.2) are shipped. Operational
readiness sprints S25-S30 landed 2026-05-26 covering code health,
observability, Docker tiering, CI expansion, integration testing, and
CI/CD pipeline. Next sprint is S31 (analytic depth — TBD).


## In flight (active)

| Sprint | Owner | Branch | Started | Goal |
|---|---|---|---|---|
| _(no active feature branches)_ | — | — | — | — |

## Completed (newest first)

| Sprint | Bundle (+ hotfix) | Subsystem | What it ships |
|---|---|---|---|
| **S30** | `ea83cdd` | shared | **CI/CD Pipeline (Build & Push to GHCR):** Created `.github/workflows/cd.yml` to automatically build and push Docker images to the GitHub Container Registry. Implemented a Docker Buildx matrix to concurrently build all three optimized tiers (`base-runtime`, `causal-runtime`, and `streaming-runtime`) from our new multi-stage `Dockerfile`. Configured dynamic metadata tagging so that pushes to `main` are automatically tagged as `latest`, and GitHub Releases are tagged with their respective Semantic Versioning numbers (`v1.x.y`). |
| **S29** | `ea83cdd` | shared | **Integration Testing (Schemathesis + Compose):** Added a new `contract-test` job to `ci.yml` that boots the API Gateway locally and uses `schemathesis` against the `/openapi.json` spec to ensure the running API strictly conforms to the advertised schema. Created `.github/workflows/nightly-e2e.yml` which runs on a cron schedule (`0 3 * * *`) to boot the entire microservice stack using `docker-compose up -d`, waits for health checks, and then runs a new E2E test suite (`tests_e2e/test_compose.py`) that pings every single service across the `localhost:8000-8009` port range using `httpx`. |
| **S28** | `ea83cdd` | shared | **CI Test Coverage Expansion:** Closed the coverage blind spots without bloating the base test lane time. Added a new `causal-test` CI job that installs `dowhy` and `econml`, specifically targeting `causal_service` and `counterfactual_service`. Added a new `streaming-test` CI job that installs `aiokafka`, specifically targeting the `uasr` and `pipeline` components. Removed redundant dowhy-gated test steps from the `eval-gate` job. All three testing lanes now successfully enforce the project's 60% base coverage threshold. |
| **S27** | `ea83cdd` | shared | **Docker Restructuring (per-tier images):** Decomposed the monolithic `aurabackend/Dockerfile` into an optimized multi-stage build emitting three distinct runtime tiers: `base-runtime` (slim API services), `causal-runtime` (+DoWhy/EconML), and `streaming-runtime` (+Kafka handlers). Separated `aiokafka` and `websockets` into a new `requirements-streaming.txt` to avoid installing heavy Kafka clients where not needed. Updated the `docker-compose.yml` build context so the shared anchor targets `base-runtime` while `uasr_service` explicitly overrides to target `streaming-runtime`. |
| **S26** | `ea83cdd` | shared | **Observability (OpenTelemetry):** Enabled out-of-the-box OpenTelemetry distributed tracing across all microservices. Added `opentelemetry-distro`, `opentelemetry-exporter-otlp`, and `opentelemetry-instrumentation-fastapi` to `requirements.txt`. Intercepted the shared `create_service()` factory to auto-instrument every API router, deliberately ignoring high-noise `/health` and `/metrics` routes. Added a local `jaeger` all-in-one collector to `docker-compose.yml` (exposing the UI on port 16686) and set the `AURA_OTLP_ENDPOINT` environment variable in the shared compose block. |
| **S25** | `ea83cdd` | many | **Code Health Hygiene:** Mass converted 316 frontend files from scattered 2-space to unified 4-space indentation for absolute consistency. Dropped unused `_legacy/` frontend components and `.bak` backups to reduce repository cruft. Fixed 15 unused/undefined variables (like `F841`) across Python services and patched 5 fragile test cases (`test_rate_limit.py`, `test_streaming.py`, `test_sprint7_smoke.py`) that were relying on dangling variable assignments. Wired `ruff check --fix` and `ruff format` directly into `.pre-commit-config.yaml` to permanently enforce the linting standard. |
| **S18.1c** | squash-merge `079da0c` (PR #28) | uasr | Wires S18 `shim_router` (Kramer-Magee canary routing) into `uasr/mapek_worker.py`, replacing the pause/resume pattern with traffic-splitting canary deploys. |
| **S18.1b** | squash-merge `4a7b862` (PR #27) | uasr | Wires S18 `CausalRLEvaluator` into `uasr/recovery_loop.py` for off-policy shim selection during MAPE-K recovery. |
| **S24 + Sec-4.1** | `fec3487` + hotfix `8d3b96f` → squash-merge `9cdc7ee` (PR #26) | counterfactual_service + shared/middleware | TMLE-vs-ForestDR disagreement auto-challenge: emits high-severity `AdversarialChallenge` when point estimates diverge by >2× the larger CI half-width. Same shape as S14's propensity warning. Sec-4.1: narrowed overbroad `except Exception` in `RateLimitMiddleware.__init__` to `(ImportError, AttributeError)`; exposed `SecurityHeadersMiddleware.apply_to()` + wired `_apply_security_headers` into both exception handlers so 4xx/5xx responses also get defensive headers. |
| **Sec-4** | `fa09e4f` + hotfix `5684044` + hotfix `9b7e114` → squash-merge `5ae3171` (PR #25) | shared (config + middleware + service_factory) + README + 3 test files | Five real findings from a Copilot audit triage (other findings either stale per Sec-3, wrong threat model for header-auth APIs, or maturity recommendations rather than vulnerabilities). (1) auth-mode prod gate — new `@field_validator` rejects `AURA_AUTH_MODE=open` when `ENVIRONMENT=production`, mirroring the existing SECRET_KEY validator. (2) CORS `http://` prod gate — promoted `warnings.warn` to `raise ValueError`, parallel to the existing wildcard `*` rejection. (3) X-Forwarded-For opt-in — new `AURA_TRUST_FORWARDED_FOR` env flag (default `False`); `RateLimitMiddleware._client_ip` honours XFF only when set, mitigating header-spoofed rate-limit bypass. (4) `SecurityHeadersMiddleware` (new) — sets `X-Content-Type-Options: nosniff` + `X-Frame-Options: DENY` + `Referrer-Policy: no-referrer` on every response; HSTS in production only (preserves http://localhost dev flow). (5) README signing-key example — replaced `("01" * 32)` with `python -c "import secrets; print(secrets.token_hex(32))"` + DON'T-USE-IN-PROD callout. Two hotfixes needed: (a) existing prod-mode test fixtures (`test_is_production_property`, `test_production_rejects_default_secret_key`, `test_cors_wildcard_rejected_in_production`) cascade-failed on the new validators because they instantiated `AuraSettings(env=production)` without overriding the default localhost CORS or default `auth_mode=open` — supplied explicit valid auth_mode + HTTPS CORS in every prod-mode fixture; (b) rewrote `test_x_forwarded_for_ignored_by_default` from a flaky TestClient route handler that instantiated a second `RateLimitMiddleware` inside the request (returned HTTP 422) to a pure unit test against a `SimpleNamespace` duck-typed Request — `-x` flag in CI made the single XFF failure cascade into the coverage gate too. Lesson: when adding any new production-mode validator on a Settings field, every existing prod-mode test fixture must be updated in the same PR to supply a complete valid config, otherwise CI cascades. |
| **Sec-3** | `489cdf4` → squash-merge `f842136` (PR #23) | api_gateway + database + counterfactual_service | Closes 4 new CodeQL alerts on `main`. (#49 HIGH `py/weak-sensitive-data-hashing`) drops `password` from `_pg_pool_key` dict — pool identity is (host, port, database, username); password adds no uniqueness. (#42 HIGH `py/sql-injection`) inline barrier-guard before `text(query)` in `connection_manager.execute_query`: string-prefix whitelist (SELECT/WITH/SHOW/DESCRIBE/DESC/EXPLAIN) + sqlglot AST deny-list (Drop/Delete/Insert/Update/Alter/Create/TruncateTable/Merge/Commit/Rollback) covering both top-level and nested statements. Same intraprocedural-sanitizer lesson as `feedback_codeql_path_injection_sanitizer`. (#24 MED `py/stack-trace-exposure`) bulk-verify endpoint strips exception to `type(exc).__name__`. (#18 MED) `etl.suggest_transforms` swaps `f"{e}"` for `sanitize_error()`. |
| **S23** | `a866784` + hotfix `3f73a91` → squash-merge `987b277` (PR #22) | counterfactual_service | E-value (VanderWeele-Ding 2017) + Cinelli-Hazlett 2020 robustness value attached to every `CounterfactualEstimate`. New `SensitivityReport` schema in hash basis → Layer 10 byte-identity extends to cover sensitivity drift. Pure NumPy + stdlib `sensitivity.py` with closed-form `compute_evalue` (Chinn 2000 continuous-outcome conversion `RR ≈ exp(0.91·|d|)` + `E = RR + sqrt(RR·(RR-1))`) and `compute_robustness_value` (`RV_q = 0.5·(sqrt(f⁴ + 4f²) - f²)` + 1x-benchmark extreme-scenario adjustment). Single `_attach_sensitivity` helper in `engine.run_estimators` post-fan-out — the eight existing estimator wrappers (LinearDR/ForestDR/TMLE/DoWhy×4) are byte-identical at the line level. Hotfix: relaxed one test from `adjusted == 0` (only triggered at pathological partial_r²→1) to `|adjusted| ≤ |point|` + sign-preservation invariants. SE backed out of CI under normal-sampling z=1.959964 (no scipy dep). Tier A contracts verified against `EValue` / `sensemakr` R packages: Darfur t=4.18,dof=783 → RV=0.139; d=0.5 → E=2.529. Layer 22 contract: strong-effect DGP RV=0.44 / E=5.41, null-effect DGP RV=0.028 / null_crossed=True. |
| **S20.1** | `4c529be` → squash-merge `9d3db43` (PR #18) | pipeline/streaming | Third and final deferred integration sprint — closes the arc S20.2 → S18.1 → **S20.1**. Wires all 5 S20a streaming primitives into the 3 live streaming modules in one bundled PR. `backpressure.py`: `BackpressureManager` gains `use_pid_control` + `PIDBackpressureController` lazy-init; new `compute_ingest_sleep_seconds(dt, max_sleep)` returns 0.0 in classical mode or `u(t)*max_sleep` in PID mode — ingest loop sleeps that long when buffer overshoots target (asymmetric clamp [0,1] — never speeds up ingest). `window_processor.py`: 4 new flags — composite `WatermarkTracker` (composite = min(per-upstream watermarks)), `WatermarkTrigger`/`TriggerContext` dispatch in `_fire_ready_windows`, parametric `late_data_policy_callable` (e.g. `remerge_within_allowed_lateness_policy`). `streaming_engine.py`: `BarrierAligner` lazy-constructed for snapshot-aligned checkpointing — `_inject_barrier` feeds every channel; checkpoint fires only when `AlignmentEvent.ALIGNED` (Chandy-Lamport exactly-once). Threads all flags through `__init__`. Every flag defaults OFF — 64 pre-S20.1 streaming tests still pass byte-identical. 16 new contract tests in `test_streaming_s20_1_integration.py` pin each opt-in path. CodeQL: 0 new alerts. Deferred follow-ups: S18.1b (CausalRLEvaluator into recovery loop), S18.1c (Kramer-Magee ShimRouter replacing pause/resume). |
| **P-3** | PR #17 | api_gateway + safety | sqlglot AST validator + connection pooling. Closes audit findings #3, #4, #6. `_estimate_query_cost` + `_check_performance_ast` use AST node counts; `QueryPlanner` join multiplier linearised; asyncpg pool registry replaces per-request connect/disconnect. 973 tests pass. |
| **P-2c** | `27c088b` | api_gateway | Lineage materialised view resolves audit #8. `gateway_lineage_edges` cache populated at create-time; `GET /lineage` is now two indexed SELECTs; FK CASCADE prunes on delete. |
| **P-2b** | `3a9d195` | api_gateway | Schema context cache resolves audit #5. `gateway_schema_context` table keyed by SHA-256 fingerprint; populate-on-upload + 60s refresh; queries.py switches to use_llm=False inline. |
| **Sec-2** | `39d8d58` + fix-ups `ad7e0f3` + `0194486` + `801b673` → squash-merge `4161ccc` (PR #16) | shared + api_gateway + database + counterfactual_service + evolution + .github | Closes all 42 open CodeQL alerts + 1 github-code-quality bot lint comment on PR #15. Four fix clusters: (a) `SQLSafetyValidator` wired into `connection_manager.execute_query` (HIGH alert #42, defense-in-depth); (b) new `shared/safe_paths.py` + inline `realpath + startswith + trailing-sep` sanitizer at FileResponse sinks in `etl.py`/`pipelines.py` (HIGH alerts #36-#41); (c) new `shared/error_handler.py::sanitize_error` helper + 25 callsite replacements removing `str(exc)`-into-response leaks (MEDIUM alerts #11-#35); (d) workflow-level `permissions: contents: read` block in `.github/workflows/ci.yml` (MEDIUM alerts #1-#10). Three fix-up commits needed to find the CodeQL-recognised sanitizer pattern: `Path.relative_to` (not modelled), then `commonpath` w/ intermediate variable (not modelled), then canonical `realpath + startswith(base + os.sep)` form (recognised). 20 contract tests in `test_sec2_helpers.py` pin the boundary attacks. |
| **S18.1** | `cae03b2` → squash-merge `5026ce0` (PR #15) | uasr | Wires S18 `WassersteinMartingaleDetector` into live `mapek_worker._analyze_detect_drift`. 4 new `MAPEKConfig` flags (default OFF for backwards-compat); `__init__` lazy-constructs the detector only when opted in. Dispatch tries martingale first → falls through to classical IQR on no-alarm / no-baseline (never silently drops a drift signal). DriftDetectionResult shape preserved across paths so recovery loop + audit log + frontend operator card need zero changes. Severity escalation flag (`martingale_alarm_severity_high`) lets operators bypass `pause_on_severity` gating per source. 9 contract tests pinning the Azuma-Hoeffding bound (≤ 3 false-positives in 30 batches at α=0.05) + 3σ drift detection within 20 batches. Bundle scope: detector swap only — Causal-RL shim selection (S18.1b) and Kramer-Magee canary router replacing pause/resume (S18.1c) deferred. Second of three deferred integration sprints. |
| **S20.2** | `bfb31fa` + hotfixes `4b0279e` + `5ce3b38` → squash-merge `fbe5556` (PR #14) | scheduler_service | Wires S20b distributed primitives into live scheduler worker. Auto-detects Postgres → LISTEN/NOTIFY wake + pg_advisory_lock cron-evaluator leader election; SQLite → pure polling fallback (backward-compat unchanged). `scheduler.replicas > 1` finally unblocked. p99 job-start latency drops from 60s (polling) to sub-second (NOTIFY hop). Two hotfixes needed for tz-naive vs tz-aware datetime mismatch: SQLite stores datetimes as strings and is forgiving; Postgres+asyncpg is strict and rejected tz-aware parameters against tz-naive columns. Fix: `.replace(tzinfo=None)` in worker.\_evaluate\_and\_execute + every model `default=` lambda. Lesson: when a Postgres schema uses tz-naive columns, EVERY producer of values (worker reads, model defaults, test fixtures) must strip tzinfo. CI lane runs both test_scheduler_distributed.py (S20b) and test_scheduler_worker_integration.py (S20.2) against postgres:16. |
| **S21d** | `1e3c929` + hotfix `a52c3af` → squash-merge `817b75e` (PR #13) | scripts + sdk_clients + 11 service openapi.json | Multi-service SDK codegen — 11 typed clients (causal, code_generation, connectors, dar, database, execution_sandbox, gateway, insights, knowledge_base, metadata_store, orchestration, scheduler) auto-generated from each service's OpenAPI schema. 162 typed methods total. `scripts/regen_all_sdks.py` orchestrator with subprocess-per-service isolation. CI lane regenerates + diffs schemas AND clients. Pillar 5 vision complete. |
| **S22** | `07794d2` + hotfix `e3d4d2a` → squash-merge `f76db51` (PR #12) | counterfactual_service | Cross-fitted TMLE as 6th estimator slot. Pure NumPy + sklearn (no econml). Closed-form ε targeting via van der Laan & Rubin 2006 identity-link linear submodel; influence-curve CI from the efficient gradient. Layer 19 contract proven: TRUE_EFFECT recovered within MAE ~0.01 on synthetic DGP (target was MAE 0.20). 16 contract tests gated on `pytest.importorskip("sklearn")` so the eval-gate lane runs them via the `test_counterfactual_*.py` glob. First sprint shipped under the two-developer protocol via feature branch + PR. |
| **P-2a** | `ab25f71` | api_gateway | File metadata cache resolves audit #2. `gateway_file_metadata` table + populate-on-upload + 60s background refresh; `/dashboard/stats` becomes a single SELECT. ~100-1000× p99 dashboard-latency improvement. |
| **P-1** | `5a03f16` + `9ffd91c` | api_gateway | Migrated `_query_history_store + _saved_queries_store + _share_tokens_store` to SQLAlchemy. Resolves audit #1 + #7. **Lazy-init via `session_scope()`** is the test-friendly pattern; don't break it. |
| **Security** | `5ccaa15` | frontend | 12 Dependabot alerts → 0. axios `^1.16.1` + `overrides` block for 9 transitive deps. |
| **S21c** | `725c562` | sdk_clients | AsyncClient mirror — `async def` methods, `__aenter__/__aexit__`, asyncio.sleep in retry loop. Both Client and AsyncClient share APIError hierarchy. |
| **S21b** | `b1b532a` | sdk_clients | Typed sync Client with 101 methods (one per OpenAPI operation). Type narrows on `$ref` responses; falls back to `Dict[str, Any]` for the 93/101 untyped. |
| **S21a** | `aaa1f99` + `f63017f` | scripts + sdk_clients | OpenAPI 3 → Pydantic v2 codegen. 47 models from gateway openapi.json. New CI lane `SDK Codegen Sync` runs `git diff --exit-code` after regen. |
| **S20b** | `00bf93e` + `aa4c111` | scheduler_service | Distributed scheduler primitives: `compute_lock_id`, `NotifyPayload`, `ExponentialBackoff`, `AdvisoryLockHolder`, `DistributedQueue`. Dedicated postgres CI lane caught a `NOTIFY $1` parameter-binding bug pre-merge. |
| **S20a** | `19859bf` | pipeline/streaming | 5 streaming primitives — Carbone ABS `BarrierAligner`, Akidau composite-watermark `WatermarkTracker`, Dataflow triggers, late-data policies, Hellerstein-Diao PID. 56 contract tests; existing streaming engine UNTOUCHED. |
| **S19** | `61f8711` + `98d2c7e` | shared + counterfactual_service + sdk | TRAIGA federation: RFC 6962 Merkle audit log + Signed Tree Head + cross-org-verifiable inclusion proofs. SDK `verify_inclusion` anchors recomputed root against STH (not proof's self-attested root). |
| **S18** | `eae19e9` | uasr | Causal-RL primitives: Wasserstein-Martingale drift detector (Bifet-Gavalda + Azuma-Hoeffding), Kramer-Magee `shim_router` (no pause/resume), DR-Learner shim evaluator with TRAIGA-shaped audit artifact. |
| **S17** | `1532518` + `53dcc38` + `20c4c25` | connectors | Multi-Modal Fabric — FAISS in-process vector connector + DuckDB spatial extension. `requirements-multimodal.txt` for opt-in faiss-cpu. |
| **S16** | `12b8669` | counterfactual_service | Conformal CATE intervals (Vovk-Petej + Tibshirani-Barber). Layer 13 distribution-free coverage guarantee. |
| **S15** | `16a2845` + `0df609f` | counterfactual_service | ForestDRLearner + CATE quantile histogram. Wager-Athey honest forest with `CalibratedClassifierCV(GBC)` propensity. |
| **S14** | `06f8428` | counterfactual_service | Propensity auto-challenge + operator card visualisation (sensitivity bands + propensity quantile bars). |
| **S13** | `8a4f0b6` + `95982ed` | counterfactual_service + sdk | Bulk replay (NDJSON streaming) + propensity diagnostics in hash basis + verify endpoint shared `strip_for_hashing`. |
| **S12** | `905177f` | counterfactual_service | Real EconML LinearDRLearner replaces stub. Eval-gate de-skip: eval-gate lane now globs `test_counterfactual_*.py`. |
| **S11** | `e83865e` + `c27edcb` | counterfactual_service | Engine determinism: per-method `seed_for(request_hash, name)` + sequential fan-out. Layer 10 byte-identical replay. |
| **S10** | `0a1edc2` | sdk | aura-counterfactual Python package — sync + async clients, click CLI, Jupyter rich-repr. |
| **S9** | `81732c3` | counterfactual_service | Auditor PDF + replay endpoint + ED25519 signing. Layer 10 contract. |
| **S8** | `ba4a6f3` | counterfactual_service | Operator chat card with debate toggle + adversarial critic + 4-estimator/4-refuter fan-out. |
| **S7** | `f2976e6` | many | TRAIGA audit log, BATS/BAVT budgets, LangGraph orchestrator, MCP server. The foundation. |

S1-S6 pre-dated this registry; see commit `157b293` and earlier for that history.

## Backlog (next 5 in priority order)

| Sprint | Pillar | Owner | Description |
|---|---|---|---|
| ~~**P-2b**~~ | ~~Audit~~ | ~~Collaborator~~ | ~~Schema context cache — DONE, see `3a9d195`~~ |
| ~~**P-2c**~~ | ~~Audit~~ | ~~Collaborator~~ | ~~Lineage materialised view — DONE, see PR #17~~ |
| ~~**P-3**~~ | ~~Audit~~ | ~~Collaborator~~ | ~~sqlglot AST validator + connection pooling — DONE, see PR #17~~ |
| ~~**S23**~~ | ~~Analytic depth~~ | ~~Mouni~~ | ~~E-value sensitivity + Cinelli-Hazlett robustness — DONE, see PR #22~~ |
| ~~**S24**~~ | ~~Analytic depth~~ | ~~Mouni~~ | ~~TMLE-vs-ForestDR disagreement auto-challenge — DONE, see PR #26~~ |
| ~~**S25**~~ | ~~Ops~~ | ~~Mouni~~ | ~~Code Health Hygiene (Lint, unused vars, frontend cruft)~~ |
| ~~**S26**~~ | ~~Ops~~ | ~~Mouni~~ | ~~Observability (OpenTelemetry)~~ |
| ~~**S27**~~ | ~~Ops~~ | ~~Mouni~~ | ~~Docker Restructuring (per-tier images)~~ |
| ~~**S28**~~ | ~~Ops~~ | ~~Mouni~~ | ~~CI Test Coverage Expansion (Causal/Streaming lanes)~~ |
| ~~**S29**~~ | ~~Ops~~ | ~~Mouni~~ | ~~Integration Testing (Schemathesis + Compose)~~ |
| ~~**S30**~~ | ~~Ops~~ | ~~Mouni~~ | ~~CI/CD Pipeline (Build & Push to GHCR)~~ |
| **S31** | Analytic depth | TBD | Next slot: IV (instrumental variables) estimator, T-/X-learner outcome models for TMLE. |

Deferred indefinitely:
- **S20.2.1** — schema migration to move `ScheduledJob.next_execution_time` (+ other DateTime columns) to `DateTime(timezone=True)` so the `.replace(tzinfo=None)` dance can be dropped.
- **S21e** — roll the api_gateway client into `regen_all_sdks.py` (currently kept on its own pipeline for historical reasons).

## How to update this file

When you start a sprint:
1. Add a row to **In flight** with your handle + branch name + date.
2. Open a GitHub issue titled `Sprint <id>: <one-line goal>` and self-assign.

When you ship a sprint:
1. Move the row from **In flight** to **Completed** (newest first).
2. Add the commit SHA (+ hotfix SHA if any).
3. Summarise what shipped in one line.

When you reserve a future sprint:
- Add a row to **Backlog** with proposed owner — `TBD` if not claimed.

Update the date at the bottom when you make a material change.

Last updated 2026-05-26 — **S25-S30** (operational readiness) + **S18.1b/c** (UASR integration) all shipped. 30 sprints completed. Next: S31 (analytic depth — IV estimator or T-/X-learner outcome models).
