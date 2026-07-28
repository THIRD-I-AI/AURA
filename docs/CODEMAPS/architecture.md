<!-- Generated: 2026-07-28 | Files scanned: 45 | Token estimate: ~950 -->

# AURA — Architecture Codemap

Source of truth: root `README.md` (current, 2026-07-24). `ARCHITECTURE.md`
(root, "last updated 2026-06-13") describes a STALE 13-service/3-plane
topology (ports 8000/8001/8010-causal/8012-cf) predating the pivot to the
9-process model below — do not use it for ports or service counts.

## System diagram (local dev, this machine)

```
Browser ─┬─> Frontend (Vite :5173, React 19)
         │      routes: / login /workbench /app/terminal /verify/:hash /certificate/:hash
         │
         └─> API Gateway :8010 (api_gateway.main:app)
               mounts every /api/v1/* router + /system/health, /health
               IN-PROCESS (not separate uvicorn procs):
                 counterfactual_service (causal + financial audit engine)
                 agents/ (commander tool-loop, LangGraph orchestrator)
                 pipeline/ (ETL + streaming engines)
                 evolution/ (self-evolution engine)
               │
               ├──> Code-Generation      :8011  NL plan-step → SQL (llm_provider)
               ├──> Connectors/Vault     :8002  Postgres·MySQL·BigQuery·DuckDB
               ├──> Execution Sandbox    :8003  isolated SQL execution
               ├──> Scheduler            :8004  distributed job queue
               ├──> Insights             :8005  charts + narratives
               ├──> Orchestration        :8006  generator⇄critic loop (NOT in /system/health)
               ├──> Metadata Store       :8007  users + schema catalog
               └──> UASR                 :8009  MAPE-K self-healing worker
               │
               ├·········> PostgreSQL `aura_vault` (prod primary) / SQLite (dev: gateway, scheduler, metadata)
               ├·········> DuckDB (per-tenant analytics lake + query execution)
               └·········> LLM providers: Groq → Gemini → Ollama → OpenAI (fallback chain)
```

Docker/prod default gateway port is **8000** (`docker-compose.prod.yml` runs
`uvicorn api_gateway.main:app --port 8000`); the local :8010/:8011 shift is
this-machine-only because a `claude-science` daemon squats 8000/8001
(`aurabackend/start_all.ps1` lines 72-77). Frontend CSP `connect-src` allows
only `:8000`/`:8010`.

`GET /system/health` polls 7 downstream services (all above except
Orchestration) + counts itself → **8 healthy** on a fully-up stack
(`aurabackend/api_gateway/main.py::_SERVICES`, `system_health()`).

## Service boundaries

| Service | Port | Module | Owns |
|---|---|---|---|
| API Gateway | 8010 (8000 prod) | `api_gateway.main:app` | all `/api/v1` routers, SSE, health aggregation, in-process CF/agents/pipeline/evolution |
| Code-Generation | 8011 (8001 prod) | `code_generation_service.main:code_gen_app` | plan-step → SQL via `shared/llm_provider` |
| Connectors/Vault | 8002 | `connectors.main:app` | external DB connections + connector registry |
| Execution Sandbox | 8003 | `execution_sandbox_service.main:execution_app` | isolated SQL execution |
| Scheduler | 8004 | `scheduler_service.main:scheduler_app` | cron/interval job queue |
| Insights | 8005 | `insights_service.main:app` | chart specs + narratives |
| Orchestration | 8006 | `orchestration_service.main:app` | generator/critic agent loop |
| Metadata Store | 8007 | `metadata_store.main:metadata_app` | users table, schema registry |
| UASR | 8009 | `uasr.service:app` | drift detection + JIT repair shims |

All nine share one chassis: `shared/service_factory.py::create_service()` —
see `backend.md` for the middleware stack.

## End-to-end query flow (`api_gateway/routers/chat.py`)

1. Frontend → `POST /api/v1/chat` (bearer token + `X-Workspace-Id` header) via `frontend/src/services/api.ts`.
2. Gateway opens per-tenant DuckDB conn, builds schema context from uploaded datasets.
3. `IntentAgent` classifies: `conversation` / `sql` / `pipeline` / `audit`.
4. `sql` → `run_orchestrator` (LangGraph) generates SQL via `llm_provider`; critic reviews; DPC cross-checks against a sandboxed pandas re-solve.
5. SQL executes on DuckDB → rows + chart spec + conclusion.
6. Persisted to tenant-scoped history; audits are ED25519-signed and appended to the ledger (`counterfactual_service.financial_report.sign_and_persist`).

Commander variant (`POST /api/v1/chat/stream`, flag `AURA_COMMANDER_ENABLED`)
runs the same intents as an agentic tool-loop (`agents/commander.py`),
emitting SSE frames `tool_call`/`tool_result`/`text`/`error`.

## Notable architecture changes (verify against your own recollection)

- Classic `/app` shell (`App.tsx`) was **deleted** — only orphaned `App.css`
  remains on disk; `frontend/src/AppRoutes.tsx` redirects `/app/*` →
  `/workbench` behind `ProtectedRoute`.
- Local ports moved off 8000/8001 → 8010/8011 (`start_all.ps1` comments cite
  a `claude-science` daemon collision).
- `aurabackend/alembic/versions/20260724_5e19034a50f4_gateway_persistence_and_audit_ledger_.py`
  is the newest migration — brings gateway persistence + `audit_ledger` under
  Alembic (previously create_all-only).
