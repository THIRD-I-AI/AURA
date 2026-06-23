# AURA Repository Map

A navigation guide to the codebase. Start here if you're new.

AURA is two products in one repo: a **conversational data-analytics workspace**
and a **signed audit/compliance engine**. The backend is a set of Python
(FastAPI) microservices; the frontend is a React + Vite + TypeScript app.

## Top level

| Path | What it is |
|---|---|
| `frontend/` | React + Vite + TS single-page app (the whole UI). |
| `aurabackend/` | All Python backend services + shared libraries. |
| `docs/` | Project documentation (this file, architecture, sprints, deploy, specs). |
| `scripts/` | Dev tooling: `install-hooks.ps1`, `generate_sdk.py`, `dev/` manual scripts. |
| `deploy/`, `infrastructure/` | Helm charts / IaC for deployment. |
| `sdk/`, `sdk_clients/` | Hand-written + auto-generated API client SDKs. |
| `docker-compose.yml`, `docker-compose.prod.yml` | Full-stack container orchestration. |
| `Makefile` | Common commands (`make up`, `make dev`, `make test`, `make health`). |
| `README.md`, `ARCHITECTURE.md`, `CLAUDE.md` | Root docs (CLAUDE.md = shared dev conventions, read it first). |

## Backend services (`aurabackend/`)

Each is a FastAPI app with its own `main.py` and port. Launch them all with
`aurabackend/start_all.ps1` (Windows) / `start_all.sh` (POSIX), or `make up`
(Docker).

| Service | Port | Purpose |
|---|---|---|
| `api_gateway/` | 8000 | The front door. Routers proxy/aggregate the other services + own the gateway DB (`persistence.py`). |
| `code_generation_service/` | 8001 | LLM code generation. |
| `connectors/` | 8002 | Data-source connectors (Postgres, MySQL, BigQuery, DuckDB, Kafka). Also imported as a **library**. |
| `execution_sandbox_service/` | 8003 | Sandboxed code/query execution. |
| `scheduler_service/` | 8004 | Distributed job queue + scheduling (Postgres LISTEN/NOTIFY). |
| `insights/` | 8005 | Automated insight generation. |
| `orchestration_service/` | 8006 | Cross-service workflow orchestration. |
| `metadata_store/` | 8007 | Dataset profiles, semantic models, vector store. Also a **library**. |
| `uasr/` | 8009 | Self-healing (UASR): drift detection → shim generation → human-in-the-loop approval. |
| `counterfactual_service/` | 8012 | Causal/counterfactual audit engine — emits ED25519-signed certificates. The deepest IP. |
| `causal_service/` | — | Causal discovery + estimation primitives. |
| `dar_service/` | — | Data-access / audit-record service. |
| `ingestion_service/` | — | Data ingestion. |

## Backend libraries (`aurabackend/`, imported — not standalone services)

| Module | Purpose |
|---|---|
| `shared/` | Cross-cutting: logging, auth/JWT, `persistence` helpers, `tasks` (fire-and-forget), signing, merkle, middleware. |
| `agents/` | LangGraph agents: intent, planner, specialists + the orchestrator that powers Chat + the Agent. |
| `pipeline/` | ETL pipeline `engine` + `models` (the runtime behind `/pipeline/*`). |
| `mcp_core/`, `mcp_servers/` | Model Context Protocol core + servers. |
| `safety/` | Guardrails / safety checks. |
| `contracts/` | Protobuf / interface contracts between services. |
| `evolution/`, `collab/` | Evolution engine + collaboration features. |

> **Naming convention:** a directory ends in `_service` when it's a standalone
> microservice (has a `main.py` + a port). Shared libraries (`shared`, `agents`,
> `pipeline`, `connectors`, …) have no suffix even when they also expose a small
> app, because they are predominantly imported as libraries.

## Tests

| Path | Scope |
|---|---|
| `aurabackend/tests/` | Main backend test suite (pytest). |
| `aurabackend/tests_contract/` | Schemathesis contract tests against the OpenAPI spec. |
| `aurabackend/tests_e2e/` | End-to-end tests (boot the stack). |
| `frontend/src/**/__tests__/`, `*.test.tsx` | Vitest frontend tests, colocated with code. |

Run: `make test` (backend) · `cd frontend && npm test` (frontend).

## Frontend (`frontend/src/`)

| Path | What it is |
|---|---|
| `main.tsx`, `App.tsx`, `AppRoutes.tsx` | Entry point, root component, routing. |
| `pages/` | One file per `/app/*` page (Files, Queries, Lineage, Cost, …). |
| `components/` | Reusable components; `components/Layout/` is the app shell + sidebar. |
| `shell/` | The screen-aware fluid shell: `ViewportProvider` anchor + page-aware `ContextRail`. |
| `terminal/` | The dockable "cockpit" terminal + the Constellation graph canvas. |
| `audit/` | Public audit-service surfaces (run audit → signed certificate → verify). |
| `auth/` | Login/signup, `AuthContext`, `ProtectedRoute`, `UserMenu`. |
| `services/` | API client (`api.ts`) — all backend calls live here. |
| `store/`, `contexts/`, `hooks/` | State, React contexts, custom hooks. |
| `ui/`, `styles/` | Design-system primitives + tokens (`design-system.css`). |

## Where do I find…?

- **An API endpoint?** `aurabackend/api_gateway/routers/<area>.py` (backend), `frontend/src/services/api.ts` (caller).
- **The gateway's database models?** `aurabackend/api_gateway/persistence.py`.
- **How services start?** `aurabackend/start_all.ps1` / `start_all.sh`, `Makefile`.
- **Deploy config?** `deploy/`, `infrastructure/`, `docker-compose*.yml`, `docs/DEPLOYMENT.md`.
- **Dev conventions / how we work?** `CLAUDE.md` (root).
- **What's shipped / in flight?** `docs/SPRINTS.md`.
