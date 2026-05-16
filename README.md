<div align="center">

# AURA

### Auditable Causal Analytics Platform

**A deterministic, cryptographically-signed causal counterfactual engine wrapped in a 12-microservice analytics stack with self-healing data ingestion, multi-agent SQL generation, and a Yjs-collaborative front end.**

[![CI](https://github.com/THIRD-I-AI/AURA/actions/workflows/ci.yml/badge.svg)](https://github.com/THIRD-I-AI/AURA/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.9-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Coverage](https://img.shields.io/badge/backend-%E2%89%A560%25-brightgreen)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[Thesis](#thesis) · [Architecture](#architecture) · [Getting Started](#getting-started) · [Audit Engine](#audit-engine) · [Roadmap](#roadmap-s15s20) · [References](#references)

</div>

---

## Thesis

Most analytics platforms answer **descriptive** questions ("what happened?"). The current frontier of AI-driven analytics adds **predictive** ("what will happen?") and **diagnostic** ("why did it happen?") capability — but the produced answers are not *auditable* in the regulatory sense. An auditor reading a model's output six months after the fact cannot verify (a) that the answer would replay byte-identically given the same input, (b) that the cryptographic chain attesting to it is intact, or (c) that the statistical guarantees behind the confidence interval hold in their finite-sample regime.

AURA is built around a **causal counterfactual audit engine** that produces answers carrying all three properties simultaneously:

1. **Deterministic re-execution** — the same `(query, dataset)` pair produces a byte-identical `audit_record_hash` across runs, on different hardware, after any duration. (Sprint 11.)
2. **Cryptographic chain** — every artifact is ED25519-signed against canonical-JSON bytes produced by a shared `strip_for_hashing(artifact)` helper used by both the sign-time and verify-time paths. The verify path cannot drift from sign. (Sprint 13.)
3. **Calibration-aware honesty** — when the doubly-robust estimator's cross-fitted propensity distribution looks IPW-fragile, the engine emits a deterministic high-severity challenge that ships in the hash basis and surfaces in the operator UI as a red badge. (Sprint 14.)

The rest of the platform — multi-agent SQL generation, streaming pipelines, MAPE-K self-healing — exists to feed the audit engine clean, well-typed inputs and to surface its outputs to three audience tiers (operator, auditor, analyst).

---

## Architecture

Twelve independent FastAPI services, communicating via JSON/HTTP. The frontend connects only through the API Gateway; every service runs on its own uvicorn process.

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
                                            │  estimators (4) ×   │
                                            │  refuters (4) ×     │
                                            │  adversarial critic │
                                            │  + ED25519 signing  │
                                            └─────────────────────┘
```

Cross-cutting infrastructure: **TRAIGA** append-only JSONL audit log with SHA-256 hash chain; **BATS** per-session token budget bound to `contextvars`; **BAVT** budget-aware value-tree routing; **Aura Vault** for connector secrets; **streaming_manager** in-process pub/sub bus; **outbound webhook dispatcher** with HMAC signatures.

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
$env:AURA_SIGNING_PRIVATE_KEY_HEX = ("01" * 32)
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
npm test            # 113 Vitest tests including CounterfactualCard
npx tsc --noEmit    # strict mode
```

---

## Audit Engine

The engine at `aurabackend/counterfactual_service/` produces a single canonical artifact per query, then renders it for three audiences from the same persisted bytes.

### Query lifecycle

1. **Submit** — `POST /counterfactual/jobs` with `CounterfactualQuery` (treatment, outcome, DAG edges, dataset reference, audience).
2. **Fan out** — engine runs 4 estimators concurrently:
   - `linear_regression` — DoWhy backdoor adjustment with classical OLS
   - `ipw` — propensity-score weighting
   - `psm` — propensity-score matching
   - `double_ml` — **EconML LinearDRLearner** with `LogisticRegression(L2)` propensity + `LinearRegression` outcome, cross-fitted with seed-from-`request_hash` for byte-identical replay (Sprints 11–12)
3. **Refute** — 4 refuters: placebo treatment, random common cause, data-subset robustness, unobserved-confounder sensitivity (Cinelli–Hazlett style).
4. **Adversarial critique** — `AdversarialCriticAgent` (LLM, cached) emits structured challenges; the engine deterministically appends an `IPW-fragile propensity` challenge when the DR-Learner's cross-fitted propensity distribution puts > 10% of rows in `[<0.05, >0.95]` (Sprint 14).
5. **Confidence score** — pure function `0.5·refute_pass + 0.4·CI_overlap − 0.3·n_high_severity`.
6. **Seal** — canonical-JSON bytes via `strip_for_hashing(artifact)`, SHA-256 → `audit_record_hash`, ED25519 signature, persist to disk + TRAIGA audit log.
7. **Render** — three audience views: `operator` (chat card + propensity bar + sensitivity band), `auditor` (full estimates + refutations + signature status + PDF report via reportlab), `analyst` (raw artifact for the Python SDK).

### Replay & verification

- `GET /counterfactual/artifacts/{hash}` — byte-identical replay.
- `GET /counterfactual/artifacts/{hash}/verify` — ED25519 signature check against `strip_for_hashing(persisted_dict)`. **Sprint 13 fix:** sign and verify both go through the same helper so they cannot drift on Pydantic exclude-spec changes.
- `POST /counterfactual/replay/bulk` — auditor batch endpoint, NDJSON streaming, server-side dedup, 256-hash cap. SDK `Client.bulk_replay(hashes)` consumes the stream as an iterator.

### Statistical guarantees (today)

| Estimator | Bias guarantee | CI guarantee |
|-----------|----------------|--------------|
| `linear_regression` | Unbiased if confounders fully observed | Asymptotic normal |
| `ipw` | Consistent if propensity correctly specified | Asymptotic normal (bootstrap option) |
| `psm` | Consistent if propensity correctly specified | Bootstrap |
| `double_ml` (LinearDRLearner) | **Doubly-robust:** consistent if EITHER propensity OR outcome correctly specified | Asymptotic normal via statsmodels |

Statistical-guarantee deepening (S16: Conformal CIs, S17: TMLE asymptotic efficiency) is on the roadmap below.

### Determinism contract

The eval-gate's Layer 10 is the contractual definition. Two engine invocations on the same `(CounterfactualQuery, DataFrame)` pair, on the same hardware, must produce identical `audit_record_hash`. This requires:

- Per-method numpy seed derived from `sha256(request_hash + method)[:4]`.
- Sequential estimator + refuter fan-out (concurrent fan-out trampling numpy global RNG).
- `_HASH_EXCLUDE_FIELDS` strips wallclock fields (`elapsed_ms`) and per-run identifiers (`record_id`, `audit_record_hash`, signatures) from the hash basis via Pydantic nested exclude semantics.

Re-execution byte-identity is currently enforced on **Linux** in CI's eval-gate (mock) job; **Windows** local runs are byte-identical in our experience but not under contract.

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

## Other capabilities

### Multi-agent SQL generation

`agents/specialists/sql_generator_agent.py` wraps the Code Generation service with sqlglot-validated emission. `agents/langgraph_orchestrator.py` runs a fixed `plan → sql → execute → visualize` StateGraph; `agents/executor.py` runs arbitrary planner-emitted DAGs. The Orchestration service's `TinyRecursiveCoordinator` adds a generator/critic loop on top.

### Multi-LLM fallback

`shared/llm_provider.py` auto-detects every configured provider and chains them: **Groq → Gemini → Ollama → OpenAI**. The fallback fires on rate-limit, payload-size, and connection errors transparently. Each provider's tokens-per-call and tool-call counts are tracked in `shared/llm_token_usage.py` and surfaced on the LLM Cost page.

### Universal data connectivity

`connectors/` ships working drivers for **PostgreSQL, MySQL, BigQuery, DuckDB** with a unified `ConnectorConfig` shape. The Aura Vault layer in `shared/vault_client.py` stores credentials encrypted at rest and serves them on-demand to the Execution Sandbox.

### Self-healing pipelines (UASR)

`uasr/mapek_worker.py` implements the five MAPE-K phases as worker methods. The worker polls Kafka, batches into Parquet, atomically loads into DuckDB, and runs the `DriftDetector` + `RecoveryLoop`. When drift is detected, the consumer is gated on an `asyncio.Event` (no offset loss) while the recovery loop deploys a shim; on resume, future batches pass through `loop.apply_shims` before re-checking drift.

### Streaming pipelines

`pipeline/engine.py` is a DuckDB-backed transform engine that consumes from multiple source types (Kafka, file watchers, CDC, WebSockets, simulated) and writes to multiple sinks. Stateful windowing (tumbling / sliding / session). Backpressure-aware.

### Scheduler

`scheduler_service/` schedules cron + interval jobs with retry, dependency chains (`depends_on`), and multi-channel notifications (SMTP, Slack webhook, generic webhook). Jobs persist in the metadata store with a worker process that polls due jobs.

### Real-time collaboration

`collab/agent_peer.py` makes server-side AURA agents into Yjs CRDT peers that participate in the same y-websocket protocol as browser clients. The agent's phase (`idle | thinking | composing`) surfaces in the same cursor/presence UI humans use for each other. No frontend change required.

### Auditability everywhere

`shared/audit_log.py` (TRAIGA) writes every prompt/response that crosses the LLM boundary to an append-only JSONL file with a SHA-256 hash chain; a verifier can replay the chain to detect any inserted, removed, or edited line. Daily rotation, intended for WORM-backed PVC for hardware-level immutability. Roadmap S19 replaces the linear chain with a Merkle tree + Signed Tree Head for cross-org inclusion proofs.

### Budget control

`shared/budget.py` (BATS) is a per-session token + tool-call pool bound to `contextvars`. Every BaseAgent execution receives a fresh `BudgetStatus` snapshot. At 70% consumption, the pivot signal flips and the Planner prefers narrower task graphs. `shared/bavt.py` (BAVT) hardens the pivot: when remaining budget can't cover an optional node's projected cost, the orchestrator drops the node and emits a structured `skipped: BAVT pivot` record.

---

## Frontend

13 lazy-loaded pages via the App's `currentPage` switch. Strict TypeScript, Vite dev server, 113 Vitest tests, type-check clean.

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
| **Counterfactual** | The Sprint 8–14 wedge — operator chat card with propensity bar, sensitivity band, confidence badge, "see the debate" toggle |

---

## Roadmap (S15–S20)

Each upcoming sprint is anchored to a specific methodological paper, not just a feature bucket.

| Sprint | Title | Anchor paper(s) | Ships |
|--------|-------|-----------------|-------|
| **S15** | ForestDRLearner + Heterogeneous-Effect Surfacing | Wager & Athey (2018); Athey, Tibshirani & Wager (2019) | Non-parametric DR estimator + CATE histogram + Eval-Gate Layer 12 |
| **S16** | Conformal CATE Intervals | Lei & Candès (2021); Alaa et al. (NeurIPS 2023) | Distribution-free finite-sample CIs replacing the asymptotic statsmodels sandwich |
| **S17** | TMLE with Cross-Validated Targeting | van der Laan & Rose (2011); Zheng & van der Laan (2011) | Asymptotically efficient estimator slot + auto-challenge on estimator-class disagreement |
| **S18** | E-Value Sensitivity + Tipping-Point | VanderWeele & Ding (Annals 2017); Cinelli & Hazlett (JRSS-B 2020) | Per-estimate E-value and robustness-value; auditor PDF section |
| **S19** | Verifiable Audit Log via Merkle Commitments | RFC 6962 Certificate Transparency; Cobbe, Veale & Singh (FAccT '23) | Merkle tree per-UTC-day; Signed Tree Head endpoint; SDK inclusion proof verifier; Helm CronJob with S3 Object Lock |
| **S20** | Causal-RL Self-Healing (UASR Upgrade) | Kallus & Uehara (JMLR 2020); Murphy (JRSS-B 2003); Bareinboim et al. (NeurIPS '15) | UASR's RecoveryLoop becomes an off-policy DR evaluator; shim deployments produce audit-engine artifacts |

After S20, the counterfactual machinery built across S8–S18 is no longer a single feature — it's the runtime mechanism the rest of the platform uses to reason about its own self-healing decisions, with the same statistical guarantees, the same audit chain, and the same byte-identical replay contract.

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
  counterfactual_service/    # 8012 — audit engine (S8–S14)
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
  tests/                     # 736+ backend tests
frontend/
  src/pages/           # 13 lazy-loaded pages
  src/components/      # 30+ components, CounterfactualCard.tsx is S14
sdk/
  src/aura_counterfactual/
  tests/               # 48 SDK tests
deploy/
  helm/aura/
docs/
```

---

## References

Selected methods that already ship in production (S8–S14):

- **Doubly-Robust Estimation:** Robins, J. M., Rotnitzky, A., & Zhao, L. P. (1994). *Estimation of regression coefficients when some regressors are not always observed.* JASA 89.
- **DR-Learner / Cross-Fitting:** Chernozhukov et al. (2018). *Double/debiased machine learning for treatment and structural parameters.* Econometrics Journal 21(1).
- **DoWhy Identification:** Sharma, A. & Kiciman, E. (2020). *DoWhy: An End-to-End Library for Causal Inference.* arXiv:2011.04216.
- **Propensity Calibration:** Niculescu-Mizil & Caruana (2005). *Predicting Good Probabilities With Supervised Learning.* ICML.
- **Canonical JSON (RFC 8785):** Erdtman, S. (2020). *JSON Canonicalization Scheme.* — for byte-identical hash basis.
- **ED25519:** Bernstein et al. (2012). *High-speed high-security signatures.* — for artifact signing.

Forthcoming (S15–S20) — see Roadmap table above.

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
