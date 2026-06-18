# Data Access Control — Part 1: Tenant-Scoped Dataset Isolation

> **Sprint S42 · issue #104.** Part 1 of 3 in the data access-control roadmap.
> Parts #2 (per-account grants, IAM-lite) and #3 (selective/conditional PII
> masking) are described at the end as the roadmap but are **NOT built here** —
> they wait for a concrete driver that defines their exact shape.

**Goal:** Every uploaded dataset is an *owned resource* scoped to a tenant. No
request can list, fetch, delete, or query a dataset owned by another tenant.

**Approach (chosen: C):** per-tenant upload directories enforced through one
helper, **plus** a dataset-ownership record that is the resource model the
future grants/PII layers attach to. Builds on the existing `current_workspace_id`
tenant chokepoint and the Sec-8 filename sanitization.

## Why this shape

The flat `data/uploads/` is read from ~10 places — the upload write + the
`FileService` (list/get/delete), and the query/schema path in `chat`,
`queries`, `dashboards`, `etl`, `pipelines`, plus the startup pre-warm in
`main`/`persistence`. Almost every query reader funnels through
`shared/data_utils.build_schema_context_cached(con, upload_dirs, …)` and just
passes a directory. So routing every site through a single
`tenant_upload_dir(request)` helper makes all ten tenant-correct via *one*
mechanism, and the directory scanners become isolated automatically.

A pure-logical registry (no folders, "scan owned datasets") is the more literal
AWS/Azure shape and where #2/#3 ultimately point — but it's a bigger change now
(every scanner stops scanning and starts loading specific files) and the flat
dir stays a single blast radius. We take the folders now and leave the seam for
the registry override that #2 will add.

## Architecture

Two coordinated layers:

1. **Storage layer** — per-tenant upload directories `data/uploads/<tenant>/`.
   A single `tenant_upload_dir(request) → Path` helper resolves the caller's
   dir; every read/write site uses it instead of the flat `data/uploads`. This
   is the v1 enforcement mechanism.
2. **Resource layer** — a `dataset` ownership record in `metadata.db`
   (`owner_tenant`, `name`, `relpath`, `created_at`). `metadata_store` already
   has a dataset-profile concept (`repo.get_dataset_profile`); the ownership
   record is realized as an `owner_tenant` column on that record or a small
   companion table (decided in the implementation plan). Records ownership for
   the listing and is the model that #2's grants and #3's PII tags extend.

## Components

### 1. Tenant resolution (`api_gateway/routers/workspaces.py`)
- Reuse `_request_tenant(request)` (org_id from the verified JWT) and the
  `default` fallback when untenanted — identical to `current_workspace_id`'s
  existing behavior, so dev / no-JWT mode is unchanged.
- New `tenant_dir_name(tenant) → str`: a filesystem-safe slug (allow
  `[A-Za-z0-9_-]`, reject anything else / path separators), so a hostile
  `org_id` can't traverse — same philosophy as Sec-8.

### 2. `tenant_upload_dir(request) → Path` helper
- Returns `<uploads_root>/<tenant_dir_name>`, creating it; untenanted →
  `<uploads_root>/default`. Asserts containment under `uploads_root`
  (`commonpath`). Single source of truth for all sites.

### 3. Upload (`api_gateway/routers/files.py`)
- `upload_universal` writes to `tenant_upload_dir(request)` combined with the
  Sec-8 `_safe_upload_path`. On success, upsert a `dataset` record
  (`owner_tenant`, `name`, `relpath`).

### 4. FileService (`shared/file_service.py`)
- Thread a `tenant_subdir` parameter into `list_files` / `get_file_info` /
  `delete_file` / the upload helpers — operate within the tenant's subdir. The
  `files.py` endpoints pass the request-derived tenant.

### 5. Query / schema readers
- Every caller of `build_schema_context_cached(con, upload_dirs, …)` —
  `chat.py`, `queries.py` (×2), `dashboards.py`, `etl.py`, `pipelines.py` —
  passes `[tenant_upload_dir(request)]` instead of the flat dir.
- `build_schema_context_cached`'s cache key includes the tenant, so tenant A
  and B never share a schema context for same-named tables.

### 6. Non-request sites (`main.py` startup, `persistence.py`)
- The startup pre-warm + `compute_schema_fingerprint` / `refresh_stale_file_metadata`
  run with no request, so no tenant. Change to **lazy per-tenant**: drop the
  eager global pre-warm; schema context builds on first query per tenant (the
  cache populates on demand), and the fingerprint/refresh become per-tenant,
  invoked from the request path. Net simplification — removes a boot-time
  global scan.

### 7. Migration (idempotent, on startup)
- Move any files sitting directly in `data/uploads/` (the existing demo files)
  into `data/uploads/default/`, and backfill `dataset` records with
  `owner_tenant = default`. Dev demo keeps working; real tenants start empty.
  Safe to re-run.

## Data flow

- **Upload:** request → tenant → `tenant_upload_dir` → Sec-8 safe path → write
  file + upsert `dataset(owner=tenant)`.
- **List / get / delete:** request → tenant → FileService scoped to the tenant
  subdir → only that tenant's files.
- **Query / chat:** request → tenant → `build_schema_context_cached([tenant_dir])`
  (cache keyed by tenant) → DuckDB sees only the tenant's CSVs.

## Error handling & safety

- **Path safety:** Sec-8 `_safe_upload_path` for filenames; `tenant_dir_name`
  sanitization for the tenant component (defense against a hostile `org_id`);
  `commonpath` containment on the resolved dir.
- **Fail-closed:** when JWT is enabled but no tenant resolves, reject — never
  fall back to a shared dir under auth. The `default` bucket is reachable only
  when JWT is disabled (dev).
- **Backward compatible:** dev / no-JWT keeps today's single-bucket behavior
  via `default`; the migration preserves the existing demo files.

## Testing

- Tenant A uploads → tenant B list/get/delete/query returns nothing / 404
  (isolation, the core property).
- Dev mode (no JWT) → `default` tenant; existing demo files visible.
- Migration: flat files moved to `default/`, `dataset` records backfilled,
  idempotent on re-run.
- Hostile `org_id` (`../`, separators) can't escape the uploads root.
- Schema-context cache: tenants A and B get distinct contexts for same-named
  tables.

## Out of scope — the roadmap, NOT built here

- **#2 Access grants (IAM-lite):** `(account, dataset, permission)` grants
  enforced at the same chokepoints, enabling cross-account/cross-tenant
  sharing. The `dataset` resource + `tenant_upload_dir` seam are designed so #2
  can add a *registry override* (a grant lets a principal read a dataset
  outside its own tenant dir). Gated on a concrete driver.
- **#3 Selective + conditional PII masking:** per-dataset/column PII tags; the
  existing keyed-tokenization `PIIMaskingMiddleware` (Sec-7 / S34d) applies
  based on the accessor's grant/role — `read, masked` vs `read, raw`. Gated on
  a concrete driver.
- **A generic IAM policy engine** (principals / policies / conditions / roles /
  groups) is explicitly **not** built. v1 is ownership; #2 is the smallest
  explicit-grant model that serves a real use case — not an IAM clone.
