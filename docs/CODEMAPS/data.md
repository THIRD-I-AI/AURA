<!-- Generated: 2026-07-28 | Files scanned: 45 | Token estimate: ~850 -->

# AURA — Data Codemap

Primary store: **PostgreSQL `aura_vault`** (prod). Dev falls back to SQLite
per-service (`sqlite:///./aura.db` default `DATABASE_URL`,
`sqlite+aiosqlite:///data/scheduler.db` for the scheduler). Per-tenant
analytics execution + upload lake: **DuckDB**. All schema-owning modules
declare their own `DeclarativeBase` (independent evolution, no shared Base).

## Gateway persistence (`api_gateway/persistence.py`)

Lazy-init via `session_scope()` — tables auto-create on first session use,
NOT solely from the FastAPI lifespan, so a router imported directly (tests)
still gets working tables (P-1 fix; don't break this pattern).

| Table | Purpose |
|---|---|
| `gateway_query_history` | executed-query log (tenant-scoped) |
| `gateway_saved_queries` | Library — saved queries + schedules + share tokens |
| `gateway_schema_context` | cached per-workspace schema context for chat |
| `gateway_file_metadata` | uploaded-file index (P-2a cache, 60s refresh loop) |
| `gateway_share_tokens` | public share links for saved queries |
| `gateway_lineage_edges` | data-lineage graph edges (sqlglot-derived) |
| `gateway_chat_messages` | chat session history |
| `gateway_pipelines` | saved ETL/streaming pipeline definitions |

## Audit ledger (`shared/audit_ledger.py`) — the tamper-evident chain

`audit_ledger` table (own `DeclarativeBase`): `tenant_id, seq (per-tenant
monotonic gap-free), kind, subject_id, subject_type, preparer_id,
reviewer_id, decided_at, cert_hash, input_fingerprint, payload_json,
prev_hash, record_hash, ts`. `UNIQUE(tenant_id, seq)` + index on
`(tenant_id, subject_id)`. Multi-replica chain ordering: (1) in-process
`asyncio.Lock` per tenant, (2) Postgres `pg_advisory_xact_lock` transaction
scope, (3) the unique constraint as the fail-closed correctness net — a race
past both locks gets rejected and the append retries from a fresh tip read.
`GET /api/v1/counterfactual/audit/ledger/verify` recomputes the chain and
returns record count + Merkle root + `ok` flag.

## Metadata Store (`metadata_store/models.py`, port 8007)

`users` (auth backing store, bcrypt password_hash + `org_id` tenant column) ·
`data_sources` · `documents` · `document_embeddings` · `schema_columns` ·
`dar_insights` (Data-Agnostic-Researcher findings) · `dataset_profiles` ·
`semantic_models` · `semantic_fields`.

## DuckDB

Per-tenant analytics lake + query execution engine. Gateway opens a
per-tenant connection to load uploaded datasets and build schema context;
`execution_sandbox_service` (:8003) runs isolated SQL against it; UASR lands
healed batches via atomic Parquet transactions (per `ARCHITECTURE.md` — not
independently re-verified in code this pass).

## Alembic migrations (`aurabackend/alembic/versions/`, chronological)

```
20260415  bb602a415b1a  initial_schema
20260416  a1b2c3d4e5f6  add_password_hash
20260428  c7d8e9f0a1b2  add_schema_columns
20260429  d2e3f4a5b6c7  add_dar_insights
20260430  e3f4a5b6c7d8  add_users_org_id
20260501  f4a5b6c7d8e9  recovery_hitl_columns
20260724  5e19034a50f4  gateway_persistence_and_audit_ledger   ← newest
```

Gateway lifespan (`api_gateway/main.py::_lifespan`) runs
`run_migrations_to_head()` first; on Alembic failure it falls back to
`create_all()` via `metadata_store.db.init_db()` + `evolution.db.init_evolution_db()`
(non-fatal, logged as warning either way).

## Object storage (S45, opt-in)

`AURA_STORAGE_BACKEND` = `local` (default) | `s3` — S3-compatible via boto3,
config validator (`shared/config.py::_require_bucket_for_s3`) hard-fails if
`s3` selected without `AURA_S3_BUCKET`.
