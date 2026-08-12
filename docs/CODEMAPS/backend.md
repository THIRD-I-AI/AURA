<!-- Generated: 2026-07-28 | Files scanned: 45 | Token estimate: ~990 -->

# AURA — Backend Codemap

All routers mounted in `aurabackend/api_gateway/main.py` under `_API_V1 = "/api/v1"`.
Only `approvals`, `auth`, `counterfactual` declare their own `APIRouter(prefix=...)`;
the rest embed the segment directly in each route path (no router-level prefix).

## Service chassis (every one of the 9 processes)

`shared/service_factory.py::create_service()` — middleware order (outermost first):
1. `CORSMiddleware` (settings.cors_origins, credentials=True, explicit methods/headers)
2. `RateLimitMiddleware` (sliding-window, opt-out `AURA_RATE_LIMIT_ENABLED=0`)
3. `JWTAuthMiddleware` (opt-in `AURA_JWT_ENABLED=true`)
4. `APIKeyMiddleware` (opt-in, only if `AURA_API_KEY` set)
5. `RequestIDMiddleware` → `request.state.request_id`
6. `RequestLoggingMiddleware`
7. `AuditLogMiddleware` (TRAIGA hash-chained JSONL, no-op unless `AURA_AUDIT_ENABLED=true`)
8. `SecurityHeadersMiddleware` (HSTS only in prod)
+ `register_exception_handlers()`, Prometheus `init_metrics()`, optional OTel `FastAPIInstrumentor`.
Every service also gets `GET /health` for free.

## Gateway (`api_gateway/main.py`) — routers mounted, in order

```
auth · approvals · workspaces · chat · files · connections · queries ·
dashboards · lineage · etl · pipelines · synthetic · stream · webhooks ·
inbound_hooks · counterfactual
collab_router          — mounted at ROOT (no /api/v1) — WS can't send auth headers
agents.api (optional)  — Agentic DE framework, ImportError-tolerant
pipeline.streaming.streaming_api (optional) — streaming pipeline engine
evolution.api (optional) — self-evolution engine, mounted at /api/v1/evolution
```

`GET /system/health` → polls `_SERVICES` dict (7 entries: code_generation,
database_service[=connectors], execution_sandbox, scheduler, insights,
metadata_store, uasr) + api_gateway itself = 8; also pulls `hu_score` from
UASR `/uasr/metrics` and circuit-breaker states. `GET /system/evolution`,
`GET /`, root `/health` (per-service, from chassis).

## Routes by router file

**auth.py** (`prefix=/auth`): `POST /token` · `POST /register` · `GET /oidc/status` ·
`GET /oidc/login` · `GET /oidc/callback` · `POST /oidc/exchange` · `GET /me`

**approvals.py** (`prefix=/approvals`): `POST ""` · `GET /pending` ·
`GET /{request_id}` · `POST /{request_id}/decide`

**counterfactual.py** (`prefix=/counterfactual`, in-process engine): `POST /jobs` ·
`GET /jobs/{job_id}` · `GET /info` · `GET /artifacts/{hash}` ·
`GET /artifacts/{hash}/report.pdf` · `GET /artifacts/{hash}/verify` ·
`GET /public-key` · `GET /demo/scenarios` · `POST /demo/{scenario_id}` ·
`POST /audit` · `POST /audit/financial` · `GET /audit/financial/demo` ·
`GET /audit/ledger/verify` · `GET /audit/financial/verify/{hash}` ·
`GET /audit/financial/{hash}/exceptions` ·
`POST /audit/financial/{hash}/exceptions/{finding_id}/decision`

**chat.py**: `POST /chat` · `POST /chat/stream` (SSE, commander, flag-gated) ·
`GET /chat/history/{session_id}` · `POST /chat/history/{session_id}`

**files.py**: `GET /files/supported-formats` · `POST /upload` · `GET /files` ·
`GET /files/{file_id}` · `GET /files/{file_id}/profile` · `DELETE /files/{file_id}`

**connections.py**: `GET /connectors/available` · `GET /connectors/registry` ·
`POST /connectors/{type}/test` · `POST /connectors/{type}/tables` ·
`POST /connectors/{type}/profile` · `GET /connections` · `POST /connections` ·
`POST /connections/{id}/test` · `DELETE /connections/{id}` ·
`GET /connections/{id}/schema` · `GET /databases/test/{db_type}` ·
`POST /connectors/{type}/ingest`

**queries.py**: `POST /execute` · `POST /execute/query` · `POST /generate_query` ·
`POST /validate/query` · `POST /lint/query` · `POST /analyze/results` ·
`GET /query-history` · `POST /query-history` · `GET|POST /saved-queries` ·
`PATCH|DELETE /saved-queries/{id}` · `POST|DELETE /saved-queries/{id}/share` ·
`GET /public/saved-queries/{token}` · `PUT|DELETE /saved-queries/{id}/schedule` ·
`GET /saved-queries/{id}/runs` · `GET /llm-stats` · `GET /dashboard/stats` ·
`POST /jobs/{job_id}/approve` · `POST /jobs/{job_id}/cancel`

**dashboards.py**: `GET|POST /dashboards` · `GET|PATCH|DELETE /dashboards/{id}` ·
`POST /dashboards/{id}/render`

**etl.py**: `POST /etl/preview-source` · `POST /etl/execute` ·
`GET /etl/download/{filename}` · `POST /etl/natural-language`

**pipelines.py**: `POST /pipeline/generate|execute|execute/async|save` ·
`GET /pipeline/list|{id}|schema/{file}|download/{filename}` ·
`DELETE /pipeline/{id}` · `POST /semantic/models[/from-file/{file_id}]` ·
`GET /semantic/models[/{id}]` · `POST /uasr/ingest|baseline` ·
`GET /uasr/metrics|drift/status|recovery/pending` ·
`POST /uasr/recovery/{id}/approve|reject`

**lineage.py**: `GET /lineage`  |  **stream.py**: `GET /stream/{topic}` · `GET /stream`
**synthetic.py**: `POST /synthetic/plan|generate` · `GET /synthetic/jobs[/{id}]`
**webhooks.py**: `POST|GET /webhooks` · `GET /webhooks/events|deliveries` ·
`GET|PATCH|DELETE /webhooks/{id}` · `POST /webhooks/{id}/test`
**inbound_hooks.py**: `GET|POST /hooks` · `GET|PATCH|DELETE /hooks/{id}` ·
`POST /hooks/fire/{slug}`
**workspaces.py**: `GET|POST /workspaces` · `PATCH|DELETE /workspaces/{id}`
**collab.py** (root, no /api/v1): `WS /ws/collab/{room_id}` · `GET /collab/rooms`

## Downstream microservice → responsibility

| Service | Port | Responsibility |
|---|---|---|
| Code-Generation | 8011 | plan-step → SQL, `shared/llm_provider`, 503/502 loud-fail on no LLM |
| Connectors/Vault | 8002 | PG/MySQL/BigQuery/DuckDB connections + registry |
| Execution Sandbox | 8003 | isolated SQL execution |
| Scheduler | 8004 | distributed job queue (cron/interval) |
| Insights | 8005 | chart specs + narratives |
| Orchestration | 8006 | generator⇄critic loop (`TinyRecursiveCoordinator`) |
| Metadata Store | 8007 | users table (auth), schema catalog |
| UASR | 8009 | MAPE-K drift detection + repair shims |
