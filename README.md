<div align="center">

# AURA

### Auditable Causal Analytics Platform for Global Enterprise

**A microservices platform that lets autonomous AI agents operate over mission-critical data systems — with cryptographically-signed audit chains, deterministic re-execution, distribution-free statistical guarantees, and self-healing pipelines that survive upstream drift.**

[![CI](https://github.com/THIRD-I-AI/AURA/actions/workflows/ci.yml/badge.svg)](https://github.com/THIRD-I-AI/AURA/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.9-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Coverage](https://img.shields.io/badge/backend-%E2%89%A560%25-brightgreen)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[Enterprise Solution](#enterprise-scale-solution) · [Architecture](#architecture) · [Getting Started](#getting-started) · [Audit Engine](#audit-engine) · [Roadmap](#roadmap-s17s21) · [Enterprise Deployment Guide](./ENTERPRISE.md)

</div>

---

## Why AURA Exists

Traditional "AI agent platforms" treat AI as a synchronous chat wrapper. That model fails under the throughput, safety, reliability, and governance demands of global mission-critical data systems for four predictable reasons:

1. **Their pipelines are brittle to upstream drift** — one rename and the corporate metrics go stale until on-call triages.
2. **Their data fabric forces silo duplication** — SQL stores, vector stores, spatial telemetry each get their own pipeline.
3. **Their LLM calls are unauditable** — regulators can't reconstruct which AI decision touched which record.
4. **Their concurrency model deadlocks on long-running work** — heavy queries freeze the chat thread.

AURA is architected for the production shape that defeats those wrappers. It solves five distinct global-scale industry challenges through five engineered subsystems — each independently deployable, observable, and auditable.

---

## Enterprise-Scale Solution

### 1. Autonomous Runtime Self-Healing (UASR)

**The global industry problem.** Modern enterprises lose billions in operational velocity to schema and semantic data drift. When an upstream data model modifies an identifier or introduces a syntax edge case (an unquoted ampersand, a regional date format, an unexpected unicode codepoint), downstream cloud-scale pipelines crash automatically, breaking corporate metrics and requiring manual on-call engineer triage.

**AURA's response.** The **Universal Agentic Semantic Recovery (UASR)** module introduces an automated, closed-loop **MAPE-K** (Monitor-Analyze-Plan-Execute-Knowledge) recovery runtime. A continuous metrics poller captures the degradation vector and streams it; specialist agents dynamically reason through a fix, rewrite the execution query, patch the transaction state, and keep the global data engine online without human intervention.

> Implementation: `aurabackend/uasr/mapek_worker.py`. Five-phase worker, Kafka-fed, DuckDB-sinked. Shim deployment is reversible via `POST /uasr/rollback`. Pause-without-offset-loss via `asyncio.Event`-gated polling.

### 2. Unified Multi-Modal Data Fabric

**The global industry problem.** Multi-national companies are drowning in heterogeneous data ecosystems. They are forced to duplicate cloud infrastructure spend by running separate standalone silos for relational tables (SQL), high-dimensional AI model arrays (embeddings), vector spaces, and multi-dimensional tracking metrics (4D virtual-reality spatial telemetry).

**AURA's response.** A **Unified Multi-Modal Fabric** mediated by a decoupled **Connector Service** maps every specialised data adapter into a single cognitive abstraction layer. One multi-agent prompt executes database lookups, vector cosine similarities, and spatial transformations concurrently against the same query plan. Heavy multi-modal transformation and deep-learning matrix execution are containerised inside a protected **Execution Sandbox** — failures inside the sandbox cannot crash the API gateway or block other tenants.

> Implementation: `aurabackend/connectors/` (PostgreSQL, MySQL, BigQuery, DuckDB shipping today; vector + spatial connectors land in Sprint 17). Sandbox at `aurabackend/execution_sandbox/`. Credential storage in `aurabackend/shared/vault_client.py` (Aura Vault).

### 3. Mitigating Nondeterministic LLM Execution Risk

**The global industry problem.** Global financial, medical, and technology institutions refuse to grant autonomous AI agents direct write or execution access to live production databases out of fear of hallucinations, data loss, and un-auditable query routing.

**AURA's response.** AURA embeds deterministic safety fences into the microservice fabric. Every agent-generated request passes through an immutable **TRAIGA AuditLog** middleware that logs cryptographically secure, hash-chained records directly onto isolated network volumes (compliant with the Texas Responsible AI Governance Act traceability standard). Context visibility is preserved across geo-distributed nodes by dynamically binding and injecting an **`X-Request-ID`** token into intercepting middleware layers, enabling real-time global observability.

The counterfactual audit engine layered on top adds **ED25519-signed artifacts**, **byte-identical re-execution** (Sprint 11 Layer 10), **shared sign/verify payload helper** so the verify path can't drift from sign (Sprint 13), and **distribution-free conformal CIs** so coverage holds in finite samples regardless of nuisance-model misspecification (Sprint 16 Layer 13).

> Implementation: `aurabackend/shared/audit_log.py` (TRAIGA chain), `aurabackend/shared/middleware.py` (request-ID injection), `aurabackend/counterfactual_service/` (audit engine + signing).

### 4. Overcoming Blocking Bottlenecks and Concurrent Task Deadlocks

**The global industry problem.** Traditional workflow platforms execute multi-agent instructions synchronously inside the network transaction loop. If a query requests heavy long-running data cleaning or machine-learning model operations, the thread blocks, socket timeouts cascade, and systemic freezing follows under enterprise workloads.

**AURA's response.** By decoupling ingestion and transformation tasks into a background **Scheduler Service**, AURA ensures the core network gateway remains entirely non-blocking. The orchestrator dynamically resolves unstructured multi-agent task descriptions through a custom topological-sorting algorithm (`_resolve_execution_order`) that maps complex parent-dependency layers into parallel execution **"waves"**, using bounded `asyncio.Semaphore` to prevent resource contention or deadlocks, while streaming real-time task state via **Server-Sent Events (SSE)**.

> Implementation: `aurabackend/scheduler_service/` (cron + interval + dependency-graph jobs), `aurabackend/agents/executor.py::_resolve_execution_order` (parallel-wave Kahn's algorithm), `aurabackend/shared/streaming_manager.py` (pub/sub bus), `aurabackend/api_gateway/routers/stream.py` (SSE endpoint).

### 5. Suppressing Software Sprawl via a Standardised Chassis

**The global industry problem.** As microservice architectures expand globally across distributed teams, services fall victim to extreme tech debt. Different groups roll out independent error handlers, irregular CORS properties, and fragmented security configurations — exposing the system to vulnerabilities through inconsistency and code duplication.

**AURA's response.** AURA introduces architectural discipline via its centralised **Service Factory** (`create_service()`). Every distinct domain microservice instantiates through this core module. Any service deployed instantly inherits sliding-window IP rate limiting, uniform JWT bearer authentication, Prometheus telemetry hooks, request-ID middleware, and global exception-to-JSON routing schemas out of the box — drastically maximising developer scaling velocity and making per-service security review unnecessary.

> Implementation: `aurabackend/shared/service_factory.py::create_service`. Every one of the 12 microservices in this repo instantiates through this single call.

---

For deep-dive enterprise material — compliance posture, sizing guidance, deployment topology, FAQ — see **[ENTERPRISE.md](./ENTERPRISE.md)**.

---

## Architecture

Twelve independent FastAPI services, each on its own port, communicating via JSON/HTTP. The frontend connects only through the API Gateway; every service runs on its own uvicorn process.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          USER (Browser / SDK / curl)                    │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                ┌────────────────▼─────────────────┐
                │   Frontend (React 19 + Vite)     │  http://localhost:5173
                └────────────────┬─────────────────┘
                                 │
                ┌────────────────▼─────────────────┐
                │   API Gateway   (8000, 108 routes)│  ◀── JWT auth, rate-limit,
                └────────────────┬─────────────────┘      collab WebSocket relay
                                 │
        ┌────────────────────────┼────────────────────────────┬───────────────┐
        │                        │                            │               │
┌───────▼──────┐ ┌──────▼──────┐ ┌▼──────────┐ ┌──────▼──────┐ ┌──────▼──────┐
│ Code-Gen     │ │Orchestration│ │ Insights  │ │  Execution  │ │ Metadata    │
│   (8001)     │ │  TinyRecur. │ │  (8005)   │ │  Sandbox    │ │  Store      │
│   LLM→SQL    │ │  Gen/Critic │ │ analytics │ │  (8003)     │ │  (8007)     │
└──────────────┘ │   (8006)    │ └───────────┘ │ safety val. │ │ pg/sqlite   │
                 └─────────────┘                └─────────────┘ └─────────────┘
        │                        │                            │               │
        ▼                        ▼                            ▼               ▼
┌──────────────┐ ┌─────────────┐ ┌───────────┐ ┌─────────────┐ ┌─────────────┐
│ Connectors   │ │ Scheduler   │ │ UASR      │ │  Causal     │ │     DAR     │
│   (8002)     │ │   (8004)    │ │ MAPE-K    │ │   (8010)    │ │   (8011)    │
│ PG/MySQL/    │ │ cron jobs   │ │  (8009)   │ │ DoWhy GCM   │ │ auto-research│
│ BQ/DuckDB    │ │             │ │ self-heal │ │ root-cause  │ │ daemon       │
└──────────────┘ └─────────────┘ └───────────┘ └─────────────┘ └──────────────┘
                                                       │
                                                       ▼
                                            ┌─────────────────────┐
                                            │  Counterfactual     │
                                            │  Audit Engine (8012)│
                                            │                     │
                                            │  5 estimators ×     │
                                            │  4 refuters ×       │
                                            │  adversarial critic │
                                            │  + ED25519 signing  │
                                            │  + conformal CI     │
                                            └─────────────────────┘
```

**Cross-cutting infrastructure:** TRAIGA append-only audit log; BATS per-session token budget; BAVT budget-aware value-tree routing; Aura Vault for connector secrets; streaming_manager in-process pub/sub bus; outbound webhook dispatcher with HMAC signatures; Service Factory standardising every microservice.

---

## Getting Started

### Prerequisites

- Python 3.11+ (3.12 for the eval-gate to run end-to-end)
- Node 18+
- (Optional) Docker for containerised runs
- At least one LLM provider key — Groq, Gemini, OpenAI — OR a local Ollama at `http://localhost:11434`

### Install

```bash
git clone https://github.com/THIRD-I-AI/AURA.git
cd AURA

# Backend (base — most services)
cd aurabackend
python -m venv .venv
.venv\Scripts\activate    # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt

# Backend (causal stack — required for counterfactual_service + causal_service)
pip install -r requirements-causal.txt        # adds dowhy>=0.13, econml>=0.15, reportlab, statsmodels

# Frontend
cd ../frontend
npm install

# SDK (optional)
cd ../sdk
pip install -e .
```

### Configure

```bash
cp aurabackend/.env.example aurabackend/.env
```

Required (one):
```
GROQ_API_KEY=...           # fastest free-tier
GEMINI_API_KEY=...
OPENAI_API_KEY=...
OLLAMA_HOST=http://localhost:11434
```

Recommended for the audit engine:
```
AURA_SIGNING_PRIVATE_KEY_HEX=<64-hex-chars>   # else key is auto-generated per process
AURA_AUDIT_DIR=/var/log/aura/audit
AURA_ARTIFACT_DIR=/var/lib/aura/artifacts
AURA_CRITIC_CACHE_DIR=/var/cache/aura/critic
```

### Run

PowerShell (Windows):
```powershell
cd aurabackend
.\start_all.ps1          # 9 services in named windows
# Then in a new terminal — Sprint 8+ counterfactual engine (not in start_all)
$env:PYTHONPATH = (Get-Location).Path
# Generate an Ed25519 signing key for the audit engine.
# DO NOT reuse this key across environments and DO NOT commit it.
# In production, generate once with `python -c "import secrets; print(secrets.token_hex(32))"`
# and store in your secrets manager.
$env:AURA_SIGNING_PRIVATE_KEY_HEX = (python -c "import secrets; print(secrets.token_hex(32))")
python -m uvicorn counterfactual_service.main:app --port 8012 --reload
```

POSIX:
```bash
cd aurabackend
bash start_all.sh
python -m uvicorn counterfactual_service.main:app --port 8012 --reload &
```

Frontend (separate terminal):
```bash
cd frontend
npm run dev          # http://localhost:5173
```

### Verify

```bash
# Backend health
for p in 8000 8001 8002 8003 8004 8005 8006 8007 8009 8012 ; do
  curl -s http://localhost:$p/health | jq -r '"\(.service): \(.status)"'
done

# Counterfactual engine capability check
curl -s http://localhost:8012/counterfactual/info | jq

# Full counterfactual subsystem self-test (requires requirements-causal.txt)
cd aurabackend
python -m pytest tests/test_counterfactual_*.py -v --tb=short
```

Frontend self-test:
```bash
cd frontend
npm test            # 121 Vitest tests including CounterfactualCard
npx tsc --noEmit    # strict mode
```

---

## Audit Engine

The engine at `aurabackend/counterfactual_service/` produces a single canonical artifact per query, then renders it for three audiences from the same persisted bytes.

### Query lifecycle

1. **Submit** — `POST /counterfactual/jobs` with `CounterfactualQuery` (treatment, outcome, DAG edges, dataset reference, audience).
2. **Fan out** — engine runs the default 4 estimators concurrently, with `forest_dr` available as an opt-in fifth:
   - `linear_regression` — DoWhy backdoor adjustment with classical OLS
   - `ipw` — propensity-score weighting
   - `psm` — propensity-score matching
   - `double_ml` — **EconML LinearDRLearner** with `LogisticRegression(L2)` propensity + `LinearRegression` outcome, cross-fitted with seed-from-`request_hash` for byte-identical replay (Sprints 11-12)
   - `forest_dr` *(opt-in via `methods=[..., "forest_dr"]`)* — **EconML ForestDRLearner** (Wager-Athey honest forest) with CalibratedClassifierCV propensity, per-row CATE quantiles surfaced as heterogeneity visualisation (Sprint 15)
3. **Refute** — 4 refuters: placebo treatment, random common cause, data-subset robustness, unobserved-confounder sensitivity (Cinelli-Hazlett style).
4. **Adversarial critique** — `AdversarialCriticAgent` (LLM, cached) emits structured challenges; the engine deterministically appends an `IPW-fragile propensity` challenge when the DR-Learner's cross-fitted propensity distribution puts > 10% of rows in `[<0.05, >0.95]` (Sprint 14).
5. **Confidence score** — pure function `0.5·refute_pass + 0.4·CI_overlap − 0.3·n_high_severity`.
6. **Seal** — canonical-JSON bytes via `strip_for_hashing(artifact)`, SHA-256 → `audit_record_hash`, ED25519 signature, persist to disk + TRAIGA audit log.
7. **Render** — three audience views: `operator` (chat card + propensity bar + sensitivity band + CATE histogram), `auditor` (full estimates + refutations + signature status + PDF report via reportlab), `analyst` (raw artifact for the Python SDK).

### Replay & verification

- `GET /counterfactual/artifacts/{hash}` — byte-identical replay.
- `GET /counterfactual/artifacts/{hash}/verify` — ED25519 signature check against `strip_for_hashing(persisted_dict)`. **Sprint 13 fix:** sign and verify both go through the same helper so they cannot drift on Pydantic exclude-spec changes.
- `POST /counterfactual/replay/bulk` — auditor batch endpoint, NDJSON streaming, server-side dedup, 256-hash cap. SDK `Client.bulk_replay(hashes)` consumes the stream as an iterator.

### Statistical guarantees

| Estimator | Bias guarantee | CI guarantee | Conformal CI option |
|---|---|---|---|
| `linear_regression` | Unbiased if confounders fully observed | Asymptotic normal | — |
| `ipw` | Consistent if propensity correctly specified | Asymptotic normal (bootstrap option) | — |
| `psm` | Consistent if propensity correctly specified | Bootstrap | — |
| `double_ml` (LinearDR) | **Doubly-robust:** consistent if EITHER propensity OR outcome correctly specified | Asymptotic normal via statsmodels | **Yes** (Sprint 16, opt-in) |
| `forest_dr` (ForestDR) | Doubly-robust + non-parametric CATE recovery | Bootstrap-of-Little-Bags | **Yes** (Sprint 16, opt-in) |

**Sprint 16 conformal-CI mode** (Lei & Candès JRSS-B 2021): pass `conformal_calibration=True` to `run_estimators`. The DR estimator does an additional split-conformal pass on AIPW pseudo-outcomes and returns a **finite-sample distribution-free** CI whose coverage holds at the stated `1-α` level regardless of nuisance-model misspecification. Operator card shows a green `conformal` badge next to the CI bracket.

### Determinism contract

The eval-gate's Layer 10 is the contractual definition. Two engine invocations on the same `(CounterfactualQuery, DataFrame)` pair, on the same hardware, must produce identical `audit_record_hash`. This requires:

- Per-method numpy seed derived from `sha256(request_hash + method)[:4]`.
- Sequential estimator + refuter fan-out (concurrent fan-out trampling numpy global RNG).
- `_HASH_EXCLUDE_FIELDS` strips wallclock fields (`elapsed_ms`) and per-run identifiers (`record_id`, `audit_record_hash`, signatures) from the hash basis via Pydantic nested exclude semantics.
- Sprint 15+ adds `cate_distribution` rounded to 6 decimals; Sprint 16 adds `ci_method`. Both ship in the hash basis.

Re-execution byte-identity is enforced on **Linux** in CI's eval-gate (mock) job and exercised by Sprint 13's `tests/test_counterfactual_*.py` glob — every Sprint 11+ change must keep Layer 10 green.

---

## Operator & SDK quickstart

### Submit a counterfactual job (curl)

```bash
curl -X POST http://localhost:8012/counterfactual/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What if treatment had been zero?",
    "treatment": {"column": "treatment", "actual": 1.0, "counterfactual": 0.0},
    "outcome":   {"column": "outcome", "agg": "sum",
                   "window": ["2025-01-01","2025-12-31"]},
    "dag":       {"edges": [["seasonality","outcome"],
                             ["seasonality","treatment"],
                             ["treatment","outcome"]]},
    "dataset":   {"source_id": "uploaded_file:my.csv"},
    "audience":  "operator"
  }'
```

### Python SDK

```python
from aura_counterfactual import Client

with Client(base_url="http://localhost:8012", prefix="") as c:
    info = c.info()
    print(info.estimators)         # ['linear_regression','ipw','psm','double_ml']

    artifact = c.run({...})         # blocks to completion
    print(artifact.confidence)      # 'low' | 'medium' | 'high'

    # Replay later
    again = c.replay(artifact.audit_record_hash)

    # Verify the signature
    v = c.verify(artifact.audit_record_hash)
    assert v.verified

    # Batch verify many
    for row in c.bulk_replay([h1, h2, h3]):
        print(row["record_hash"], row["status"])    # ok / not_found / verify_failed
```

### Jupyter

The SDK's `CounterfactualArtifact` has a rich `_repr_html_` — paste a `replay(hash)` into a notebook cell and you get the confidence badge, estimator table, challenges, and provenance footer rendered inline.

### CLI

```bash
aura-counterfactual info
aura-counterfactual replay 0xabc...
aura-counterfactual verify 0xabc...
aura-counterfactual bulk-replay --hashes-file ./audit_sweep.txt
aura-counterfactual report 0xabc... -o ./audit.pdf
```

Exit codes are structured for CI gating: `0=ok`, `2=job_failed`, `3=timeout`, `4=not_found`, `5=verify_failed`, `6=feature_unavailable`.

---

## Frontend

13 lazy-loaded pages via the App's `currentPage` switch. Strict TypeScript, Vite dev server, 121 Vitest tests, type-check clean.

| Page | What it does |
|------|--------------|
| Dashboard | System health, recent activity, query stats |
| Chat | NL→SQL chat with agent presence and live result rendering |
| Files & Data | File upload + connector catalog + table profiling |
| Query History | Searchable archive of prior queries |
| Library | Saved queries + dashboards |
| Dashboards | Multi-chart dashboard composer |
| Lineage | Data lineage graph |
| LLM Cost | Per-provider token usage + per-day cost roll-up |
| Agent | Direct agent invocation with input/output inspection |
| ETL Pipelines | Pipeline definition + monitoring |
| Streaming | Live streaming pipeline status |
| Webhooks | Inbound + outbound webhook configuration |
| **Counterfactual** | The Sprint 8-16 wedge — operator chat card with propensity bar, sensitivity band, CATE histogram, conformal-CI badge, confidence badge, "see the debate" toggle |

---

## Roadmap (S17-S21)

The S17-S21 sequence is reorganised around the five enterprise pillars. Each sprint produces (a) a shippable feature, (b) one new eval-gate Layer, (c) a memory file documenting the non-obvious decisions, and (d) a CI-verified bundle commit.

| Sprint | Pillar | Title | Anchor |
|--------|--------|-------|--------|
| **S17** | Pillar 2 | Multi-Modal Fabric: Vector + Spatial Connectors | FAISS / pgvector / geopandas |
| **S18** | Pillar 1 | Causal-RL Self-Healing: UASR meets the Audit Engine | Kallus & Uehara JMLR 2020; Murphy JRSS-B 2003 |
| **S19** | Pillar 3 | TRAIGA Federation: Merkle Audit Log + Signed Tree Head | RFC 6962 Certificate Transparency; Cobbe et al FAccT '23 |
| **S20** | Pillar 4 | Scheduler v2: Distributed Multi-Region Execution | Postgres `LISTEN/NOTIFY` work-queue pattern |
| **S21** | Pillar 5 | Service Factory v2: Auto-generated Typed SDK Clients | OpenAPI Generator + Pydantic v2 codegen |
| **S22** | Analytic depth | TMLE with Cross-Validated Targeting | van der Laan & Rose 2011 |
| **S23** | Analytic depth | E-Value Sensitivity + Tipping-Point | VanderWeele-Ding 2017; Cinelli-Hazlett 2020 |

After S21, the five enterprise pillars are deepened. The platform stops adding new audience-facing capability and starts compounding the analytical depth and operational rigour of the existing surfaces.

---

## Repository layout

```
aurabackend/
  api_gateway/         # 8000 — front door (14 routers)
  code_generation_service/   # 8001
  connectors/                # 8002 — PG, MySQL, BQ, DuckDB
  execution_sandbox/         # 8003
  scheduler_service/         # 8004
  insights/                  # 8005
  orchestration_service/     # 8006 — generator/critic TinyRecursive
  metadata_store/            # 8007
  uasr/                      # 8009 — MAPE-K self-healing
  causal_service/            # 8010 — DoWhy GCM root-cause
  dar_service/               # 8011 — autonomous research daemon
  counterfactual_service/    # 8012 — audit engine (S8-S16)
  agents/specialists/        # 15 BaseAgent subclasses
  agents/langgraph_orchestrator.py
  collab/                    # Yjs server-side peers
  evolution/                 # self-improvement loop
  knowledge_base/
  mcp_core/                  # MCP protocol primitives
  mcp_servers/aura_mcp_server.py
  pipeline/                  # ETL engine (DuckDB)
  safety/validator.py        # SQLSafetyValidator
  shared/                    # cross-cutting infra
  tests/                     # 800+ backend tests
frontend/
  src/pages/           # 13 lazy-loaded pages
  src/components/      # 30+ components, CounterfactualCard.tsx is S14/S15/S16
sdk/
  src/aura_counterfactual/
  tests/               # 48 SDK tests
deploy/
  helm/aura/
docs/
ENTERPRISE.md         # Deep-dive deployment guide for procurement / SRE
```

---

## References

Methods that already ship in production (Sprints 8-16):

- **Doubly-Robust Estimation:** Robins, Rotnitzky & Zhao (1994). JASA 89.
- **DR-Learner / Cross-Fitting:** Chernozhukov et al. (2018). Econometrics Journal 21(1).
- **Generalized Random Forests:** Athey, Tibshirani & Wager (2019). Annals of Statistics 47(2). [Sprint 15]
- **Heterogeneous Treatment Effects via Random Forests:** Wager & Athey (2018). JASA 113(523). [Sprint 15]
- **Conformal Inference of Counterfactuals:** Lei & Candès (2021). JRSS-B 83(5). [Sprint 16]
- **Conformal Prediction Under Covariate Shift:** Tibshirani, Barber, Candès & Ramdas (2019). NeurIPS 32. [Sprint 16 helper]
- **Propensity Calibration:** Niculescu-Mizil & Caruana (2005). ICML.
- **DoWhy Identification:** Sharma & Kiciman (2020). arXiv:2011.04216.
- **Canonical JSON (RFC 8785):** Erdtman (2020) — for byte-identical hash basis.
- **ED25519:** Bernstein et al. (2012) — for artifact signing.
- **TRAIGA Standard:** Texas Responsible AI Governance Act — informs the AuditLog design (`shared/audit_log.py`).

Forthcoming methods (Sprints 17-23) — see the Roadmap table above and `ENTERPRISE.md` for the deepening per pillar.

---

## Contributing

Pull requests welcome. Before pushing:

```bash
cd aurabackend
python -m ruff check . --ignore E501,E402,F401,W191,W291,W293,F841,E701,E712,F823
python -m pytest tests/ --tb=short

cd ../frontend
npm test
npx tsc --noEmit
```

The CI workflow at `.github/workflows/ci.yml` enforces all of the above plus the eval-gate (mock + real LLM). The eval-gate globs `tests/test_counterfactual_*.py` so any new test file with a `pytest.mark.skipif(not dowhy_available())` marker automatically enrols — *do not* put dowhy-gated tests in `backend-test`, they will silently skip.

---

## License

MIT — see [LICENSE](LICENSE).
