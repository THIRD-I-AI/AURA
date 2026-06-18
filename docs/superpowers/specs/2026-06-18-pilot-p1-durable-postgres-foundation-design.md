# Production Pilot — Part 1: Durable Postgres-backed Persistence + Armed Prod Gates

> **Sprint S43 · issue #106.** Part 1 of 3 in the production-pilot effort.
> Part #2 (cloud deploy — single VM + Docker Compose + managed Postgres/Neon +
> volume + Caddy TLS) and Part #3 (backups + signing-key restore + go-live
> checklist) follow and are described at the end. They are **NOT built here**.

**Goal:** AURA runs correctly and durably on managed **Postgres** with the
production auth gates armed — proven locally in `docker-compose` against a
real Postgres container, *before* any cloud is provisioned. This retires the
single biggest unknown ("does AURA even run on Postgres?") at zero infra cost.

## Favorable starting point (verified 2026-06-18)

- `asyncpg` + `psycopg2-binary` are already dependencies (`requirements.txt`,
  `pyproject.toml`).
- A `docker-compose.prod.yml` already exists.
- Every store's DB URL is env-overridable: `DATABASE_URL` (`config.py`),
  `METADATA_DATABASE_URL` (`metadata_store/db.py`), `SCHEDULER_DATABASE_URL`.
- The scheduler is already Postgres-aware (`scheduler_service/worker.py`:
  LISTEN/NOTIFY on Postgres, polling on SQLite).
- Prod auth gates exist (`AURA_JWT_ENABLED` + password mode); S42 tenant
  isolation just merged and depends on them being on.

So this is **wiring + verification**, not a rewrite. The real work is hunting
SQLite-isms that surface when the stores hit Postgres.

## Architecture

Nothing new is invented; we flip configuration and verify. The data-of-record
moves to Postgres; the **DuckDB query engine stays in-memory by design** — it
materializes the durable per-tenant CSVs on the fly and is not a store of
record, so it needs no durability change.

```
managed Postgres  ← metadata_store, api_gateway/persistence, scheduler   (state of record)
durable volume    ← data/uploads/<tenant>/  (CSVs; DuckDB reads these in-memory)
injected secrets  ← signing key, JWT secret, PII token key, LLM keys, DB password
armed gates       ← AURA_JWT_ENABLED + password mode → every request authed + tenant-scoped (S42)
```

## Components

### 1. Stores → Postgres (the risk)
Point the three URLs at `postgresql+asyncpg://…` (async stores) and the sync
ones at `postgresql://…`. Then **verify each store's `create_all` builds on a
fresh Postgres** — this is where SQLite-isms hide:
- `metadata_store` (uses `create_all`, not Alembic) — JSON columns, the
  vector/embedding columns, any `String` length assumptions.
- `api_gateway/persistence.py` (gateway-private SQLAlchemy, lazy `session_scope`
  `create_all`).
- `scheduler_service` (already Postgres-aware; verify LISTEN/NOTIFY path boots).
Fix whatever breaks (e.g. a SQLite-only default, a JSON vs JSONB mismatch, a
missing server-side default). Fresh-DB `create_all` is in scope; data migration
from an existing SQLite DB is **not** (a pilot starts empty).

### 2. Uploads root → env-configurable
S42 hardcoded `_UPLOADS_ROOT` in `workspaces.py`. Add `AURA_UPLOADS_ROOT`
(default = the current computed path, so dev is unchanged) so the deploy can
point uploads at a mounted volume. The Sec-8 / S42 sanitization is unchanged.

### 3. Signing key + secrets
- **Stable signing key:** confirm the ED25519 audit key survives a restart —
  either a fixed injected `AURA_SIGNING_PRIVATE_KEY_HEX` (loaded by `signing.py`)
  or `persisted_file` on the durable volume. This closes the verify-bug class
  (a transient shell-exported HEX key becomes un-verifiable after restart).
- **Secrets are all env-driven:** `GEMINI_API_KEY`/`GROQ_API_KEY`, the JWT
  secret, `AURA_PII_TOKEN_KEY`, DB password. Deliver a documented
  `aurabackend/.env.prod.example` enumerating every required prod var (no real
  secrets committed).

### 4. Prod gates armed
Set `AURA_JWT_ENABLED` + password mode in the prod compose env. Verify the
`JWTAuthMiddleware` 401s unauthenticated data requests and that an authenticated
request is tenant-scoped (S42) end to end.

## Data flow (unchanged shape, durable backing)
- Auth/register/login → user rows in **Postgres** (`metadata_store`).
- Upload → file on the **durable volume** under `data/uploads/<tenant>/` + the
  metadata in Postgres.
- Chat→SQL → DuckDB (in-memory) reads the tenant's CSVs from the volume.
- Audit cert → signed with the **stable** key, persisted in Postgres, verifies
  across a restart.

## Error handling & safety
- Fail-closed auth (S42 depends on the armed gates; verify a no-token request is
  401, never the `default` bucket under JWT).
- Any `create_all`/DDL failure on Postgres is a hard boot error we fix, not
  swallow.
- No secret values in the repo; `.env.prod.example` carries names + guidance only.

## Testing (the deliverable)
A `docker-compose` profile that boots the stack against a **Postgres container**,
plus a smoke test (script or pytest, run against the live compose) of the core
flows **on Postgres**:
1. register → login (JWT issued, password mode).
2. upload a CSV (lands in the tenant dir on the volume; metadata in Postgres).
3. chat → SQL query returns rows over that CSV.
4. audit cert: sign, then **restart the stack**, then verify the cert still
   validates (proves signing-key + Postgres durability).
5. a second tenant cannot see the first tenant's upload/tables (S42 holds on
   Postgres).

Plus a focused, automatable **`create_all`-on-Postgres test**: spin up each
store's metadata against a Postgres test container (gated `skipif` when no
`AURA_PG_TEST_DSN`, matching the repo's Tier-B optional-dep pattern) and assert
the schema builds — this catches SQLite-isms (JSON/JSONB, vector columns,
server defaults) deterministically, where the smoke test only catches what its
flows happen to touch. The existing backend suite keeps running on SQLite in CI
(unchanged); we do **not** retarget the whole suite at Postgres.

## Out of scope — the roadmap (NOT built here)
- **Part #2 — cloud deploy:** a single small VM + Docker Compose + managed
  Postgres (Neon) + a mounted volume + Caddy auto-TLS for a real URL; the
  one-command bring-up. (~$30–60/mo for one pilot; graduate to the existing
  Helm/k8s chart at multi-customer scale.)
- **Part #3 — operational durability:** Postgres backups, signing-key
  backup/restore, and the go-live security checklist (`ENTERPRISE.md` 10-item
  list: PII masking armed, rate limits, etc.).
- **Billing** (roadmap Phase 3) — not part of a pilot.
- **Object storage** (S3/R2) for uploads — a later refinement; a durable volume
  is sufficient for one pilot.
