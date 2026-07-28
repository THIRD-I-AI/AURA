<!-- Generated: 2026-07-28 | Files scanned: 45 | Token estimate: ~800 -->

# AURA — Dependencies Codemap

## External services

| Dependency | Role | Required? |
|---|---|---|
| PostgreSQL | `aura_vault` primary DB (prod); local box points at `192.168.1.92:5432/aura_vault` | required in prod; SQLite fallback in dev |
| DuckDB | per-tenant analytics lake + query execution (embedded, no server) | always (embedded lib) |
| Kafka | streaming ingestion spine — `aiokafka`; local compose uses **Redpanda** (Kafka-compatible, `docker-compose.yml` port 9092/29092) | optional — streaming/UASR-Kafka-worker paths only |
| Redis | rate-limit backend + caching (`AURA_REDIS_URL`) | optional — falls back to in-process backend |
| Prometheus | `/metrics` via `prometheus-fastapi-instrumentator` on every service (chassis) | auto, no-op if scraping unused |
| OpenTelemetry | tracing, `AURA_OTLP_ENDPOINT`; local compose runs **Jaeger** (port 16686) as the OTLP backend | optional, ImportError-tolerant |
| Sentry | error tracking, no-op unless `AURA_SENTRY_DSN` set | optional |
| S3 / MinIO | object storage backend (S45), `AURA_STORAGE_BACKEND=s3` | optional |

## LLM providers (`shared/llm_provider`)

Auto-detected fallback chain: **Groq** (default, `GROQ_API_KEY`,
`llama-3.3-70b-versatile`) → **Gemini** (`GEMINI_API_KEY`,
`gemini-2.5-flash`) → **Ollama** (local, `OLLAMA_HOST`, offline-capable) →
**OpenAI** (`OPENAI_API_KEY`, `gpt-4o-mini`). `AURA_DEPLOYMENT_PROFILE=onprem`
hard-fails at boot if any external key (Groq/Gemini/OpenAI) is set — air-gapped
profile must use Ollama only (`shared/config.py::_enforce_deployment_profile`).

## Key Python dependencies (`aurabackend/requirements*.txt`)

**Base** — `fastapi>=0.115,<0.137` (capped: 0.137 breaks in-process router
mount resolution), `uvicorn[standard]`, `pydantic>=2.7`, `pydantic-settings`,
`sqlalchemy>=2.0`, `alembic>=1.13`, `asyncpg`, `psycopg2-binary`, `aiomysql`,
`aiosqlite`, `duckdb>=0.10`, `pyarrow`, `langgraph>=0.2,<0.4` (Planner→SQLGen→
Exec→Viz state machine), `mcp[cli]` (stdio/SSE MCP server), `sqlglot`
(SQL parsing/lineage/DDL-safety), `PyJWT`, `bcrypt`, `cryptography>=42`
(ED25519 signing), `aiokafka>=0.10`, `boto3`+`moto` (S3), `redis[hiredis]`,
`prometheus-fastapi-instrumentator`, `sentry-sdk[fastapi]`,
`opentelemetry-distro`/`-exporter-otlp`/`-instrumentation-fastapi`,
`google-generativeai`, `groq`.

**`requirements-causal.txt`** (causal_service pod only, ~150MB C-ext cost
kept out of base): `dowhy>=0.13,<0.16` (GCM root-cause, floor bumped for
networkx 3.x d_separated removal), `networkx>=3.0`, `econml>=0.15,<0.17`
(doubly-robust ATE), `pingouin`, `statsmodels`, `reportlab` (PDF certs, 503
if missing).

**`requirements-streaming.txt` / `-multimodal.txt`** — streaming + FAISS/
spatial connector extras (not read in full this pass).

## Key npm dependencies (`frontend/package.json`)

**Framework**: `react@19.1`, `react-dom@19.1`, `react-router-dom@7.16`,
`vite@8.0`, `typescript@~5.9.3`. **Design system**: `tailwindcss@4.3` +
`@tailwindcss/vite`, `radix-ui@1.6` (shadcn primitives), `class-variance-authority`,
`clsx` + `tailwind-merge` (the `cn()` helper), `lucide-react` (icons),
`@fontsource/*` (Inter, JetBrains Mono, Instrument Sans, Space Grotesk).
**Cockpit**: `dockview-react@6.6` (Terminal panel grid), `@xyflow/react@12.11`
+ `d3-force@3.0` (Constellation lineage graph), `recharts@3.7` (charts),
`motion@12.42` (animation). **Networking**: `axios`, `socket.io-client`.
**Observability**: `@sentry/react@9.0`. **Codegen**: `openapi-typescript`
(`npm run generate:api` → `src/types/api.generated.ts`).
**Test**: `vitest@4.1`, `@testing-library/react@16.3`, `@playwright/test@1.49`,
`jsdom`, `eslint@9.36` + `typescript-eslint@8.45`.

## SDK / codegen

`sdk/` — hand-written `aura-counterfactual` Python client. `sdk_clients/` —
11 auto-generated per-service typed clients (`scripts/generate_sdk.py`,
OpenAPI → Pydantic v2), held byte-stable by a CI codegen-sync gate.
