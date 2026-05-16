# AURA — Enterprise Deployment Guide

**For procurement, security review, and platform-engineering teams evaluating AURA against global enterprise mission-critical workloads.**

---

## Executive Summary

Most "AI agent platforms" available today are synchronous chat wrappers built on a single LLM provider, a single database driver, and a single deployment shape. They fail under the throughput, safety, reliability, and governance demands of global mission-critical data systems for four predictable reasons: their data pipelines are brittle to upstream drift, their data fabric forces silo duplication, their LLM calls are unauditable, and their concurrency model deadlocks on long-running work.

AURA is architected for the production shape that defeats those wrappers. It addresses five distinct global-scale industry challenges through five engineered subsystems, each of which is independently deployable, independently observable, and independently auditable. This document describes each pillar in production-deployment language: what the problem is at enterprise scale, what AURA ships to solve it, which modules implement it, what the operational characteristics look like, and where the deepening roadmap is taking each pillar next.

---

## Pillar 1 — Autonomous Runtime Self-Healing (UASR)

### The Global Industry Problem

Modern enterprises lose billions in operational velocity to **schema and semantic data drift**. When an upstream data model modifies an identifier, introduces a syntax edge case (unquoted special characters, ampersands, four-byte unicode, regional date formats), or shifts the distribution of a column the downstream pipeline depends on, traditional pipelines crash with a fatal exception. Corporate metrics go stale; on-call data engineers triage manually; mean-time-to-recovery is measured in hours; the business loses the decision window the data was supposed to inform.

### AURA's Solution

The **Universal Agentic Semantic Recovery (UASR)** module (`aurabackend/uasr/`) introduces an automated, closed-loop **MAPE-K** runtime — Monitor, Analyze, Plan, Execute, Knowledge — that turns drift into a self-healed event rather than a page.

**The five phases as production methods (`uasr/mapek_worker.py`):**

| Phase | Method | What it does |
|---|---|---|
| **Monitor** | `_monitor_pull_batch` | Polls the upstream Kafka topic, builds a `BatchPayload` |
| **Analyze** | `_analyze_detect_drift` | Runs `DriftDetector` (IQR-based statistical drift on columns + a semantic-similarity gate on string columns) |
| **Plan** | `_plan_recovery` | Invokes `RecoveryLoop` — the specialist-agent reasoning that proposes a corrective shim |
| **Execute** | `_execute_persist` / `_execute_recovery` | Either persists the batch (no drift) or pauses the consumer, deploys the shim, and replays the batch through the now-corrected path |
| **Knowledge** | `_knowledge_update` | Updates the baseline reference and the healing-metric tracker |

### Operational characteristics

- **Pause without offset loss.** The "pause consumer → run LLM recovery → restart" requirement is satisfied by `asyncio.Event`-gated polling: when `_paused` is set, the worker stops calling `getmany` but does **not** close the consumer. Kafka offsets are preserved across the recovery window.
- **Shim deployment is reversible.** `RecoveryLoop._deployed_shims` carries every active shim with a deploy timestamp and a confidence score. `POST /uasr/rollback` removes a shim and replays the affected batch through the unshimmed path.
- **Semantic gating.** `POST /uasr/gate/check` runs an embedding-similarity check against the reference baseline before a batch is admitted — even when statistical drift is below threshold.
- **Full observability.** `GET /uasr/metrics` exposes the **healing-event rate**, **recovery-success ratio**, **drift-detection frequency**, and the **per-source Hᵤ entropy score**. Alerts fire via `GET /uasr/metrics/alerts` on threshold violations.

### Production deployment

Helm chart at `deploy/helm/aura/values.yaml` ships the UASR service pinned to **1 replica** (the worker owns next-tick state in process memory). The Kafka consumer-group is configurable via `UASR_KAFKA_GROUP`; deploying multiple replicas requires partitioning the source-id space across consumer groups. The DuckDB sink path is configurable via `UASR_DUCKDB_PATH` and points at a `ReadWriteOnce` PVC in production.

### Deepening roadmap

Sprint 20 (planned) reframes the `RecoveryLoop` as a **counterfactual off-policy evaluator** using the audit-engine machinery from Sprints 8-16. Every candidate shim gets evaluated via the LinearDR/ForestDR estimator against the recent drift batch (treatment = "shim applied", outcome = post-shim drift score). The engine picks the shim with the highest counterfactual expected improvement; the decision and the supporting artifact are written to the same TRAIGA audit log used by the analyst-facing audit engine. Auditors can ask "show me every self-healing decision in the last 24 hours" and get the same byte-identical replay guarantee.

---

## Pillar 2 — Unified Multi-Modal Data Fabric

### The Global Industry Problem

Multi-national companies are drowning in **heterogeneous data ecosystems**. The same business question — "what's the lifetime value of customer cohort X?" — touches relational tables in a warehouse, embeddings in a vector store, time-series telemetry in a streaming sink, and increasingly, multi-dimensional tracking metrics (4D virtual-reality spatial telemetry, IoT sensor lattices, geospatial trajectories). Each store requires its own driver, its own credential vault, its own query language, and its own access-audit pipeline. Cloud infrastructure spend duplicates by 3-5x. Cross-store queries become engineering projects rather than analyst questions.

### AURA's Solution

A **Unified Multi-Modal Fabric** mediated by the decoupled **Connector Service** (`aurabackend/connectors/`, port 8002) maps every adapter — relational, vector, spatial, streaming — into a single cognitive abstraction layer. Multi-agent prompts execute database lookups, vector cosine similarities, and spatial transformations concurrently against the same query plan.

**Currently shipping connectors:**

| Type | Driver | Capability |
|---|---|---|
| Relational | PostgreSQL | Full SQL via SQLAlchemy + asyncpg |
| Relational | MySQL | Full SQL via SQLAlchemy + aiomysql |
| Cloud DW | BigQuery | Federated query via `google-cloud-bigquery` |
| Embedded | DuckDB | Columnar analytics, the MCP DuckDB tool's backend |
| Vector / Multi-modal | *(Sprint 17 — planned)* | Vector cosine, MIPS, hybrid retrieval |
| Spatial / 4D | *(Sprint 17 — planned)* | Multi-dimensional tensor operations, spatial joins |

### Risk containment

Heavy multi-modal transformation and deep-learning matrix execution run inside the **Execution Sandbox** (`execution_sandbox/`, port 8003) — a process-isolated subprocess pool with a `SQLSafetyValidator` (`safety/validator.py`) that blocks DDL/DML, regex-flags injection patterns, and auto-injects `LIMIT` clauses. Failures inside the sandbox cannot crash the API gateway or block other tenants.

**Credential storage** lives in `shared/vault_client.py` — the Aura Vault hybrid storage layer that encrypts at rest, decrypts on-demand to the Execution Sandbox, and never exposes credentials to the agent prompt or the LLM. The credential schema is in `metadata_store/models.py`.

### Operational characteristics

- **One agent prompt = N concurrent connector calls.** The `agents/langgraph_orchestrator.py` StateGraph fans out connector requests in parallel; the Scheduler Service (Pillar 4) prevents resource contention.
- **Per-connector circuit breaker.** `shared/circuit_breaker.py` wraps every outbound connector call; persistent failures trip the breaker and fall back to a cached schema view, preventing cascading failures across upstream stores.
- **Schema discovery is on-demand.** The MCP server (`mcp_servers/aura_mcp_server.py`) exposes `metadata.search_columns` and `duckdb.describe_table` as MCP tools the agent calls only for the slice it needs — keeping prompt size under `AURA_MAX_TOKENS_PER_REQUEST` even with 70+ column schemas.

### Deepening roadmap

**Sprint 17 (next)** ships the Multi-Modal Fabric proper: a vector connector (FAISS / Postgres+pgvector / Pinecone) plus a spatial connector that handles 4D telemetry (timestamp + lat + lon + altitude). Both go through the same `ConnectorConfig` shape so the agent layer doesn't need to know the difference. The fabric layer adds **cross-connector query planning** — when an agent asks for "customers similar to row X who also live within Y km of facility Z", the planner emits a single execution DAG that joins vector retrieval with spatial filtering and relational enrichment in a single pass.

---

## Pillar 3 — Enterprise Governance (TRAIGA)

### The Global Industry Problem

Global financial, medical, and technology institutions refuse to grant autonomous AI agents direct write or execution access to live production databases. The fear is rational: hallucinations, data loss, un-auditable query routing, and the inability to prove during a regulator visit which AI decision touched which record and why. The default response is to gate every agent call behind a human-in-the-loop approval, which destroys the throughput advantage AI was supposed to deliver.

### AURA's Solution

AURA embeds **deterministic safety fences** into the microservice fabric. Every agent-generated request passes through the **TRAIGA AuditLog** (`shared/audit_log.py`) — an append-only JSONL writer compliant with the **Texas Responsible AI Governance Act** traceability requirements.

**The hash-chain design:**

```
record N:   { ts, request_id, agent, prompt, response,
              prev_hash: SHA256(record N-1),
              record_hash: SHA256(stable_fields_of_record_N) }
```

- **Append-only**: every record is one `json.dumps(...) + "\n"` write with `fsync` after each line. No update / delete code paths exist in the module — the file format IS the contract.
- **Hash chain**: tampering with any record (insert, delete, edit) breaks the chain at that point. A verifier walks the file from record 0 forward and emits a precise tamper offset.
- **Daily rotation** keyed on UTC date. One file per day per service.
- **WORM-backed storage assumption**: `AURA_AUDIT_DIR` is a `ReadWriteOnce` PVC in the Helm chart. Operators mark the underlying StorageClass `WORM` (S3 Object Lock, Azure Immutable Blob, GCS Bucket Lock) for hardware-level immutability — this module guarantees *logical* immutability; the storage layer guarantees *physical*.

### Distributed observability

Context visibility is preserved across geo-distributed nodes by dynamically binding and injecting an **`X-Request-ID`** token into the intercepting middleware layer (`shared/middleware.py`). Every outbound HTTP call to another microservice propagates the same `X-Request-ID`; every log line includes it; every TRAIGA record includes it. A single regulator question — "what did this request touch?" — resolves into a single `grep` across the audit log.

### Cryptographic guarantees on the audit-engine output

The counterfactual audit engine (`counterfactual_service/`, port 8012) extends the TRAIGA chain with **ED25519 signatures** on every sealed artifact. The signing key lives in `AURA_SIGNING_PRIVATE_KEY_HEX`; verification works against the public key without privileged access. Sprint 13's `strip_for_hashing(artifact)` helper guarantees that the sign-time and verify-time payloads cannot drift on Pydantic schema changes — the same source of truth runs through both paths.

### Compliance posture

| Standard / Requirement | AURA's Mechanism |
|---|---|
| TRAIGA traceability | Hash-chained JSONL audit log, daily rotation, WORM PVC |
| GDPR right-of-access | Per-`request_id` log slicing via single `grep` across the chain |
| HIPAA audit controls | Append-only writes with `fsync`; no record-deletion path exists |
| SOC 2 logging | `shared/observability.py` exposes Prometheus counters per service |
| PCI DSS log integrity | SHA-256 hash chain + ED25519 signatures on the audit-engine artifacts |
| ISO 27001 access control | Aura Vault for credentials; JWT bearer tokens on every service |

### Deepening roadmap

**Sprint 19 (planned)** replaces the linear hash chain with a **Merkle tree** of records per UTC day. New endpoint `GET /counterfactual/audit/sth` returns a **Signed Tree Head** `{tree_size, root_hash, timestamp, signature}` based on the Certificate Transparency (RFC 6962) design. SDK gains `Client.verify_inclusion(record_hash)` that rebuilds the root from `(record_hash, proof)` and checks against the latest STH. Two organisations running AURA can prove "this artifact was sealed at time T, in a tree of N records, signed by the engine's public key" without disclosing the artifact body — cross-org verifiable analytics without data leakage. Daily STH publication to S3 Object Lock via a Helm CronJob.

---

## Pillar 4 — Non-Blocking Concurrent Execution

### The Global Industry Problem

Traditional workflow platforms execute multi-agent instructions **synchronously inside the network transaction loop**. When a query requests heavy long-running work — data cleaning over millions of rows, model retraining, embedding generation across a corpus — the thread blocks. Socket timeouts cascade. Other tenants see freeze. The platform fails the enterprise SLA exactly when the analyst needs it most.

### AURA's Solution

AURA decouples ingestion and transformation from the network gateway via the **Scheduler Service** (`scheduler_service/`, port 8004). The core API gateway remains entirely non-blocking; long-running work runs in background workers; clients track progress over **Server-Sent Events (SSE)**.

### The topological sorting algorithm

The orchestrator (`agents/executor.py`'s `_resolve_execution_order`) dynamically resolves unstructured multi-agent task descriptions by:

1. **Building a task DAG** from declared `depends_on` relationships per agent step.
2. **Running Kahn's algorithm** for topological sort, but emitting tasks in **parallel "waves"** rather than a linear sequence. Wave N contains all tasks whose dependencies are exhausted by waves 0..N-1.
3. **Bounding concurrency per wave** with `asyncio.Semaphore(AURA_MAX_PARALLEL_AGENTS)` (default 8) to prevent resource contention.
4. **Failing fast on cycle detection** — Kahn returns `None` if the task graph contains a cycle, and the orchestrator emits a structured error instead of deadlocking.

### Streaming progress

The `shared/streaming_manager.py` in-process pub/sub bus publishes task state transitions as `StreamEvent` objects. The API gateway's `/api/v1/stream/{session_id}` SSE endpoint subscribes and forwards events to the frontend. The frontend's `useSSE` hook renders live progress in the chat card. End-to-end latency from "wave-N task done" to "browser UI updated" is sub-100ms on a healthy LAN.

### Resource isolation

| Resource | Protection mechanism |
|---|---|
| LLM provider rate limits | `shared/circuit_breaker.py` per provider, with fallback chain (Groq → Gemini → Ollama → OpenAI) in `shared/llm_provider.py` |
| Token budget per session | `shared/budget.py` (BATS — Budget-Aware Test-time Scaling) bound to `contextvars`. Pivot signal at 70% triggers planner to drop optional nodes |
| Tool-call budget per session | Same BATS tracker counts tool calls alongside tokens |
| Hard-stop on exhaustion | `BudgetStatus.is_exhausted` flips True; orchestrator emits `skipped: BAVT pivot` records instead of stalling |
| Per-source connection pools | Connector Service maintains separate connection pools per `source_id`; one pool exhausting doesn't affect others |
| Per-tenant workspace isolation | `metadata_store/models.py` workspace IDs gate every data access; cross-tenant reads require explicit permission grants |

### Deepening roadmap

**Sprint 20 (planned)** extends the Scheduler with **distributed execution across regions**. Today the scheduler is single-replica because the worker owns dispatch state in process memory. Sprint 20 moves the work-queue into a Postgres-backed `LISTEN/NOTIFY` channel and allows N scheduler workers to share the queue, with one designated leader for cron evaluation. This unlocks multi-region active-active deployments where a job submitted in the EU cluster executes in whichever region has spare capacity at that moment.

---

## Pillar 5 — Standardized Microservice Chassis (Service Factory)

### The Global Industry Problem

As microservice architectures expand globally across distributed teams, services fall victim to **extreme tech debt**. Different groups roll out independent error handlers, irregular CORS properties, divergent retry strategies, and fragmented security configurations. The result is **security vulnerabilities through inconsistency** (one service rejects malformed JWTs, another doesn't), **code duplication** (every service re-implements rate limiting), and **observability gaps** (metrics, logging, and tracing don't agree on field names across services). Onboarding a new microservice becomes a multi-week process of copying the previous service's boilerplate and inevitably missing one piece.

### AURA's Solution

Every distinct domain microservice instantiates through a **centralised Service Factory** (`shared/service_factory.py`):

```python
from shared.service_factory import create_service

app = create_service(
    name="Counterfactual Audit Engine",
    service_tag="counterfactual_service",
    description="Causal counterfactual estimation with hash-sealed audit artifacts.",
)
```

That single call returns a fully-configured FastAPI app with every cross-cutting concern wired:

| Concern | Implementation |
|---|---|
| Sliding-window IP rate limiting | `shared/rate_limit.py` — token bucket per `(IP, route)`, configurable via env |
| JWT bearer authentication | `shared/auth.py` — HS256 default, RS256-ready for federated scenarios; injected as a FastAPI `Depends(require_user)` |
| Prometheus telemetry | `shared/observability.py` — request count, duration, status-code distribution; standard `/metrics` endpoint |
| Global exception → JSON | `shared/middleware.py::GlobalExceptionMiddleware` routes every uncaught exception to `{detail, request_id, status}` |
| Request-ID propagation | `shared/middleware.py::RequestIDMiddleware` reads or generates `X-Request-ID`, binds to a `contextvar`, includes in logs |
| Access log with masked PII | `shared/middleware.py::RequestLoggingMiddleware` writes one line per request with structured fields |
| Health endpoints | `/health` (basic), `/ready` (deep — includes DB connectivity, optional dep availability) |
| CORS | Standard middleware with env-configured allow-origins; defaults to localhost in dev, explicit list in prod |
| OpenAPI | Auto-generated with `/docs` (Swagger) and `/openapi.json` |
| Graceful shutdown | Lifespan context that drains in-flight requests on SIGTERM before exiting |

### Developer velocity impact

A new microservice now costs **one `create_service()` call + one router declaration** to ship — every other concern is inherited. A new team joining the platform doesn't need to read fifteen specs; they read the factory's docstring once and implement domain logic only. The factory is the **architectural discipline** that keeps the 12-microservice platform from sprawling.

### Compliance & governance impact

Because every service inherits the same authentication, rate limiting, and audit-log integration, **security review is per-factory, not per-service**. Approving the factory once approves every service that uses it. Adding a new compliance requirement (e.g., a header-level data-residency tag) becomes a one-line change in the factory rather than a 12-PR rollout.

### Deepening roadmap

**Sprint 21 (planned)** ships **Service Factory v2**: an auto-generated typed SDK client per service derived from the same `create_service()` registration. A new service that registers via the factory automatically gets a corresponding `aura_<service>_client` Python package generated at build time, complete with type hints, retry policy, and OpenAPI-aligned Pydantic models. The eval-gate ensures any registered service has a matching client; missing or out-of-sync clients fail CI. This eliminates the manual SDK-maintenance burden that today blocks a service from being consumed by sibling services without bespoke `httpx` calls.

---

## Reference Deployment Topology

### Single-region production

```
                                                      ┌─────────────────┐
                                                      │  Object Lock S3 │
                                                      │   (WORM PVC for │
                                                      │  TRAIGA + STH)  │
                                                      └────────▲────────┘
                                                               │ daily
                                                      ┌────────┴────────┐
                                                      │  CronJob: STH   │
                                                      │  publication    │
                                                      └─────────────────┘
                                                               │
┌──────────┐    HTTPS    ┌─────────────────┐   ┌──────────────┴──────────────┐
│ Browser  │────────────▶│  Ingress (HAProxy/│  │     Kubernetes Cluster      │
│ / SDK    │             │   nginx / ALB)    │──▶│  (Helm chart, 12 svcs)      │
└──────────┘             └─────────────────┘   │                              │
                                               │   API Gateway (2 replicas)   │
                                               │   Code Gen, Insights         │
                                               │   Connectors, Sandbox        │
                                               │   Metadata Store (3 repls,   │
                                               │      Postgres backed)        │
                                               │   Scheduler (1 leader +      │
                                               │      N workers — S20+)       │
                                               │   UASR (1 replica per Kafka  │
                                               │      consumer group)         │
                                               │   Causal / DAR / Counterfactual│
                                               │     (1 replica each — in-     │
                                               │      process job state)       │
                                               └──────────────────────────────┘
                                                               │
                                       ┌───────────────────────┼───────────────────────┐
                                       │                       │                       │
                              ┌────────▼────────┐   ┌──────────▼────────┐   ┌──────────▼────────┐
                              │   PostgreSQL    │   │      Kafka        │   │   LLM Providers   │
                              │   (metadata)    │   │   (UASR + ETL)    │   │ Groq / Gemini /   │
                              │   3-node HA     │   │   3-broker        │   │ Ollama / OpenAI   │
                              └─────────────────┘   └───────────────────┘   └───────────────────┘
```

### Multi-region active-active (Sprint 20+)

- **Database tier**: CockroachDB or YugabyteDB for the metadata store (Postgres-wire-compatible, multi-region by design). Sprint 20 unblocks this by removing the scheduler's single-leader constraint.
- **Audit log tier**: per-region WORM PVC + cross-region S3 Object Lock replication. STH publication includes a region tag.
- **Counterfactual audit chain**: artifacts are content-addressed (Sprint 9), so regions can replicate the artifact directory without conflict. Replay against a region's local copy verifies the same signature globally.
- **LLM provider routing**: regional preference (Groq EU first for EU traffic, etc.) with fallback to global providers via `shared/llm_provider.py`.

### Sizing guidance

| Workload shape | Suggested topology |
|---|---|
| < 100 analyst seats, < 10 GB data | Single-region, 2-replica gateway, t3.medium-class nodes, ~$2k/mo |
| 100-1k seats, < 100 GB data, single region | 3-replica gateway, c5.xlarge analyst pool, dedicated UASR worker per Kafka consumer group, ~$8k/mo |
| 1k-10k seats, multi-region active-active | CockroachDB metadata, multi-region Helm chart per region, cross-region audit replication, S20+ distributed scheduler, ~$50k/mo |
| > 10k seats, regulated industry | Above + dedicated WORM storage class (S3 Object Lock with retention period), per-tenant Aura Vault sharding, hardware HSM for the ED25519 signing key |

---

## Sprint Roadmap Tied to the Five Pillars

The S17-S21 roadmap is reorganised around the five pillars so each sprint deepens a specific enterprise capability. Research anchors are retained but framed as "supporting evidence" for each enterprise outcome.

| Sprint | Pillar | Title | Anchor |
|---|---|---|---|
| **S17** | Pillar 2 | Multi-Modal Fabric: Vector + Spatial Connectors | FAISS / pgvector / `geopandas` |
| **S18** | Pillar 1 | Causal-RL Self-Healing: UASR meets the Audit Engine | Kallus & Uehara JMLR 2020; Murphy JRSS-B 2003 |
| **S19** | Pillar 3 | TRAIGA Federation: Merkle Audit Log + Signed Tree Head | RFC 6962 Certificate Transparency; Cobbe et al FAccT '23 |
| **S20** | Pillar 4 | Scheduler v2: Distributed Multi-Region Execution | Postgres `LISTEN/NOTIFY` work-queue pattern |
| **S21** | Pillar 5 | Service Factory v2: Auto-generated Typed SDK Clients | OpenAPI Generator + Pydantic v2 codegen |
| **S22** | Analytic depth | TMLE with Cross-Validated Targeting | van der Laan & Rose 2011; Zheng & van der Laan 2011 |
| **S23** | Analytic depth | E-Value Sensitivity + Tipping-Point | VanderWeele & Ding Annals 2017; Cinelli & Hazlett JRSS-B 2020 |

Each sprint produces a (a) shippable feature behind a feature flag, (b) one new eval-gate Layer, (c) a memory file documenting the non-obvious decisions, and (d) a CI-verified bundle commit.

---

## FAQ

**Q: Why not use [LangChain / LlamaIndex / commercial RAG platform]?**
Those tools are excellent for prototyping a chat-over-documents experience. They are not architected for autonomous *write/execute* access to production databases with the audit chain, deterministic replay, and statistical-guarantee CI that mission-critical enterprise governance requires. AURA's audit engine alone (Sprints 8-16) has no equivalent in the LLM-framework ecosystem — those frameworks treat the LLM call itself as the atomic unit; AURA treats the causal counterfactual artifact as the atomic unit and provides cryptographic guarantees on top of it.

**Q: How do you handle LLM provider outages?**
The `shared/llm_provider.py` fallback chain (Groq → Gemini → Ollama → OpenAI) fires transparently on rate-limit, payload-size, and connection errors. Local Ollama as the third hop means the platform degrades gracefully even when both Groq and Gemini are down. The `shared/circuit_breaker.py` wraps each provider call so a single provider's slowness doesn't propagate into latency on healthy paths.

**Q: How are secrets rotated?**
`AURA_SIGNING_PRIVATE_KEY_HEX` and the credential store secrets are designed for rotation via Kubernetes secret refresh + service rolling restart. The audit-chain integrity survives key rotation because each artifact records `signing_key_source` (env / file / hardware) and `signing_key_fingerprint` (truncated public-key hash). Verifying an artifact under a rotated key requires retaining the public key (not the private key) for the artifact's retention horizon.

**Q: What's the disaster-recovery plan for the TRAIGA audit log?**
WORM-backed storage (S3 Object Lock, Azure Immutable Blob, GCS Bucket Lock) prevents accidental and malicious deletion. Cross-region replication of the WORM tier gives geographic redundancy. The hash chain means partial recovery is detectable: a verifier reads the chain forward from record 0 and emits the precise offset where the chain breaks. From there, the operator restores from the most recent intact backup.

**Q: How do we evaluate AURA against our existing data platform?**
The recommended evaluation flow:
1. Deploy AURA in a non-production region against a snapshot of production data (24-48 hours).
2. Pick three real analyst questions your team commonly asks. Run them through both your current platform and AURA.
3. Compare on: time-to-answer, query auditability (can you reconstruct the same answer in 90 days?), and uncertainty surfacing (does the platform tell you when the answer is fragile?).
4. The Sprint 13 bulk-replay endpoint (`POST /counterfactual/replay/bulk`) lets you audit a batch of historical AI-generated decisions in one call — useful for an after-the-fact audit pilot.

**Q: What's the licensing posture?**
MIT-licensed core. Optional enterprise support, on-premise hardening, and audit-log forensic services available separately.

---

## Contact

This deployment guide is generated from the architecture as it lives on `main`. For enterprise procurement questions, security review materials, or custom-deployment scoping, file an issue at https://github.com/THIRD-I-AI/AURA/issues with the `enterprise` label.
