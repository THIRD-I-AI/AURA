# S45 — Object-Storage Backend for Uploads (S3 / R2 / MinIO)

> **Sprint S45 · issue #114.** Retires the #1 enterprise horizontal-scale
> blocker: uploads on a local per-tenant filesystem dir. Builds on S42 (tenant
> isolation) and the S44 endpoint-portability pattern.

**Goal:** Store uploaded datasets in S3-compatible object storage with a
configurable endpoint + credentials, so the api_gateway becomes a stateless pod
that scales horizontally across nodes — in cloud (S3/R2) and on-prem/air-gapped
(MinIO). Fully backward-compatible with the local-filesystem mode for
dev/single-host.

## Why now / why this shape

A `ReadWriteOnce` PVC binds to one node, so the default 2-replica gateway can't
spread across nodes while sharing one upload set (see `deploy/helm/aura` notes).
Object storage is the store-of-record that every replica reads from equally —
the thing that converts "runs on k8s" into "scales horizontally on k8s."

**Scope (chosen via brainstorming): one app-managed bucket, tenant-prefixed
keys** (`<bucket>/<prefix>/<tenant>/<file>`). Per-tenant / bring-your-own-bucket
(data residency) is deferred to a future sprint via the same seam.

**Approach (chosen): A — DuckDB reads `s3://` directly via httpfs.** Zero local
disk; idiomatic for the read-only-root-FS pods hardened earlier. The two
rejected alternatives: B (download-cache to local ephemeral disk — less invasive
to the query layer but reintroduces a disk cache with eviction + coherence) and
C (ship A, add B's cache later only if measured). C's first half == A, so A is
the v1 and B remains a clean additive follow-on if per-query S3 reads ever
measure as a bottleneck.

## Current read/write path (what we're generalizing)

Traced this session:
- **Write:** `api_gateway/routers/files.py::upload_universal` → Sec-8
  `_safe_upload_path` → writes a file under `tenant_upload_dir(request)`
  (`workspaces.py`, S42: `<uploads_root>/<tenant>/`).
- **List/get/delete:** `shared/file_service.py` (`list_files(subdir)`,
  `get_file_info`, `delete_file(file_id, subdir)`) — direct filesystem ops on the
  tenant subdir.
- **Schema/query read:** `shared/data_utils.py::build_schema_context` enumerates
  files with `upload_dir.iterdir()` and loads each into DuckDB via
  `smart_load_file(con, str(path), …)` → `read_csv_auto('<local path>')`. The
  schema cache key `_signature_for_upload_dirs` uses `file.stat()` mtime/size.
  `_replay_tables` re-creates DuckDB tables from local path strings.
- **Connections:** `duckdb.connect(":memory:")` is open-coded in ~8 sites
  (chat.py, dashboards.py, etl.py ×3, persistence.py, schema_indexer.py). No
  central factory.

All three readers assume **local filesystem paths** — the generalization point.

## Architecture

One storage abstraction, two backends, selected by env. The query layer reads
through DuckDB-readable URIs the backend supplies (local paths or `s3://`).

```
AURA_STORAGE_BACKEND = local (default) | s3
  local → LocalBackend  → <uploads_root>/<tenant>/<file>   (today's behavior)
  s3    → S3Backend     → s3://<bucket>/<prefix>/<tenant>/<file>  (httpfs reads)

store of record:  object storage (or local disk in `local` mode)
DuckDB:           in-memory; reads s3:// via httpfs (no local copy) in s3 mode
config:           AURA_S3_* (endpoint/bucket/region/keys/url_style), like S44
```

## Components

### 1. `shared/storage/` (new package)

- **`base.py`** — `StorageBackend` ABC + `ObjectInfo` dataclass
  (`name: str`, `size: int`, `fingerprint: str`, `duckdb_uri: str`). Methods:
  - `write(tenant: str, filename: str, data: bytes) -> ObjectInfo`
  - `read(tenant: str, filename: str) -> bytes`
  - `list(tenant: str) -> list[ObjectInfo]`
  - `delete(tenant: str, filename: str) -> bool`
  - `exists(tenant: str, filename: str) -> bool`
  - `duckdb_uri(tenant: str, filename: str) -> str`
  - `configure_duckdb(con) -> None` — load httpfs + set the S3 secret; **no-op for local**
- **`local.py`** — `LocalBackend`: wraps `<uploads_root>/<tenant>/` (reuses the
  S42 `tenant_dir_name` slug + containment). `duckdb_uri` = local path;
  `fingerprint` = `"{mtime_ns}|{size}"`; `configure_duckdb` = no-op. **Byte-for-byte
  today's behavior.**
- **`s3.py`** — `S3Backend`: a boto3 client built from config
  (`endpoint_url`, `region_name`, keys, `config=Config(s3={"addressing_style":
  url_style})`). Keys = `<prefix>/<tenant>/<filename>`. `duckdb_uri` =
  `s3://<bucket>/<prefix>/<tenant>/<filename>`. `fingerprint` = `"{etag}|{size}"`
  from `list_objects_v2` / `head_object`. `configure_duckdb` runs
  `INSTALL httpfs; LOAD httpfs; CREATE OR REPLACE SECRET (TYPE S3, KEY_ID …,
  SECRET …, REGION …, ENDPOINT …, URL_STYLE …, USE_SSL …)`.
- **`__init__.py`** — `get_storage_backend()` cached singleton selected by
  `AURA_STORAGE_BACKEND`; unknown value → `ValueError` (fail loud, mirrors
  `get_llm`). `reset_storage_backend()` for tests.

### 2. `shared/duckdb_factory.py` (new)

`new_connection(database=":memory:") -> duckdb.DuckDBPyConnection` →
`con = duckdb.connect(database); get_storage_backend().configure_duckdb(con)`.
Replace the open-coded `duckdb.connect(":memory:")` in the upload-reading sites
(chat, dashboards, etl, and the schema/query path). Non-upload sites may adopt it
too (harmless; the local no-op costs nothing).

### 3. Config (`shared/config.py`)

New fields (pydantic aliases), defaults keep `local`:
- `storage_backend: str = Field("local", alias="AURA_STORAGE_BACKEND")`
- `s3_bucket: Optional[str] = Field(None, alias="AURA_S3_BUCKET")`
- `s3_endpoint_url: Optional[str] = Field(None, alias="AURA_S3_ENDPOINT_URL")`
- `s3_region: str = Field("us-east-1", alias="AURA_S3_REGION")`
- `s3_access_key_id: Optional[str]` (alias `AURA_S3_ACCESS_KEY_ID`, fallback `AWS_ACCESS_KEY_ID`)
- `s3_secret_access_key: Optional[str]` (alias `AURA_S3_SECRET_ACCESS_KEY`, fallback `AWS_SECRET_ACCESS_KEY`)
- `s3_url_style: str = Field("path", alias="AURA_S3_URL_STYLE")` — canonical
  values `path` (MinIO/most on-prem) | `vhost` (AWS). The `S3Backend` maps this
  to boto3's `addressing_style` (`vhost`→`virtual`) and passes it verbatim to
  DuckDB's `URL_STYLE`, so one env drives both clients consistently.
- `s3_prefix: str = Field("uploads", alias="AURA_S3_PREFIX")`
- `s3_use_ssl: bool = Field(True, alias="AURA_S3_USE_SSL")`

A validator fails closed at boot when `storage_backend == "s3"` and `s3_bucket`
is unset.

### 4. Upload write (`api_gateway/routers/files.py`)

`upload_universal`: keep Sec-8 `_safe_upload_path`'s sanitization to derive a
**safe filename** (refactor it to return the sanitized name, not a filesystem
path; the rejection rules — `..`, separators, NUL, empty/`.`/`..` — are
unchanged), then `get_storage_backend().write(tenant, safe_name, data)`. Tenant
from `_request_tenant(request)`. Dataset-ownership upsert (S42) unchanged.

### 5. FileService (`shared/file_service.py`)

`list_files(subdir)`, `get_file_info`, `delete_file(file_id, subdir)`,
`save_file`, `process_file` route through `get_storage_backend()` (subdir ==
tenant). Profiling reads bytes via `backend.read(tenant, filename)` into the
existing pandas profiler.

### 6. Schema/query reader (`shared/data_utils.py`)

The core change. Generalize from `upload_dirs: List[Path]` to a tenant-scoped
dataset enumeration:
- `build_schema_context` / `_build_schema_context_with_recipe`: iterate
  `backend.list(tenant)` → `smart_load_file(con, obj.duckdb_uri, table_name)`.
- `_signature_for_upload_dirs` → `_signature_for_objects(objects)` over
  `obj.fingerprint` (ETag for S3, mtime|size for local).
- `_replay_tables`: read from `obj.duckdb_uri`; drop the `Path(...).exists()`
  guard (attempt + warn, backend-agnostic).
- Callers (`chat.py`, `queries.py` ×2, `dashboards.py`, `etl.py`,
  `pipelines.py`) pass the request-derived tenant instead of
  `[tenant_upload_dir(request)]`; the DuckDB connection comes from
  `new_connection()` so it's S3-configured.
- `tenant_upload_dir` stays as a `LocalBackend` internal.

### 7. Air-gap httpfs

The DuckDB `httpfs` extension auto-downloads on first `LOAD` — which fails in an
airgap. The backend `Dockerfile` pre-installs it at build time (a build step that
runs `INSTALL httpfs` into the image's DuckDB extension dir, or copies the
`.duckdb_extension`), so `LOAD httpfs` works offline. Documented in
`docs/DEPLOYMENT.md` (air-gapped target).

### 8. Deploy wiring

- `aurabackend/.env.prod.example` — an object-storage block (the `AURA_S3_*`
  vars + `AURA_STORAGE_BACKEND`), names + guidance only.
- `docker-compose.prod.yml` — pass the S3 vars through `x-backend-env`
  (`${VAR:-}`); optional `minio` service overlay documented for on-prem.
- `deploy/helm/aura/values.yaml` + README — when `AURA_STORAGE_BACKEND=s3`, set
  `uploads.enabled=false` (no PVC) and supply the S3 secret; this becomes the
  **recommended multi-replica path**.
- `docs/DEPLOYMENT.md` — object storage as the cloud/multi-node default; MinIO
  for on-prem/air-gapped.

## Data flow (s3 mode)

- **Upload:** request → tenant → Sec-8 safe name → `S3Backend.write` →
  `PutObject <prefix>/<tenant>/<file>` + dataset-ownership upsert (Postgres).
- **List/get/delete:** request → tenant → `S3Backend.list/read/delete` scoped to
  the `<prefix>/<tenant>/` key prefix → only that tenant's objects.
- **Query/chat:** request → tenant → `new_connection()` (httpfs + S3 secret) →
  `build_schema_context` enumerates `S3Backend.list(tenant)` → DuckDB
  `read_csv_auto('s3://…')`; schema cache keyed by ETag fingerprints.

## Error handling & safety

- `storage_backend=s3` with no bucket → **boot fails** (validator).
- S3 errors surface to the caller; **no silent fallback to local** under s3 mode.
- Object-not-found → existing 404 behavior.
- Tenant key isolation: every op is prefixed by `<prefix>/<tenant>/`; a tenant's
  `list` can never see another tenant's keys (the S42 property, on S3).
- Filename safety: Sec-8 sanitization still gates the filename component of the
  key (no `..`, separators, NUL).
- `configure_duckdb` failure (httpfs load / secret) → loud, not swallowed.

## Testing

- **`tests/test_storage_backend.py` (Tier A, `moto`):** S3Backend
  write/list/read/delete/exists/fingerprint against a moto-mocked bucket;
  LocalBackend parity on a tmp dir; `get_storage_backend` selection +
  unknown→`ValueError`; **tenant key isolation** (tenant A's `list` excludes B's
  objects); Sec-8 safe-name rejection still applies. `moto` + `boto3` added to
  test/runtime deps.
- **`tests/test_storage_s3_duckdb.py` (Tier B, gated `skipif` on
  `AURA_S3_TEST_ENDPOINT`):** the real DuckDB-reads-`s3://` path against a MinIO
  container in a dedicated CI lane (mirrors the Postgres `AURA_PG_TEST_DSN`
  pattern) — upload a CSV → `build_schema_context` returns its table → a chat→SQL
  query returns rows.
- **Backward-compat:** `AURA_STORAGE_BACKEND` unset/`local` → existing upload,
  FileService, and chat tests pass unchanged (the LocalBackend is today's code
  behind the interface).

## Out of scope (future, via the same seam)

- Per-tenant / bring-your-own bucket + region (data residency).
- A local read-through cache (Approach B) as a measured perf optimization.
- Migrating other local-disk state (scheduler checkpoints, audit logs) to object
  storage — those are separate concerns with their own durability stories.
- Moving existing on-disk uploads into the bucket: a pilot starts empty; an
  optional one-shot sync script can be added if a populated instance needs it.
