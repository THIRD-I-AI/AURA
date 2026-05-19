# Performance Audit Burn-Down — Playbook

Mouni and a collaborator received a performance audit on 2026-05-18
identifying 8 findings in `aurabackend/api_gateway/routers/queries.py`
and `aurabackend/safety/validator.py`. **Three findings are done**
(P-1 covered #1 + #7, P-2a covered #2). **Five remain**, and the
collaborator is driving them.

This document is the **playbook** for the remaining work. It
documents the pattern that worked in P-1 and P-2a so the collaborator
can move fast without re-deriving the architecture.

## Status of the 8 findings

| # | Location | Severity | Status | Sprint |
|---|---|---|---|---|
| #1 | `queries.py` in-memory stores | HIGH | ✅ DONE | P-1 (`5a03f16`) |
| #2 | `queries.py:818-835` per-file DuckDB | HIGH | ✅ DONE | P-2a (`ab25f71`) |
| #3 | `validator.py:162` `_estimate_query_cost` | MEDIUM | TODO | P-3 |
| #4 | `validator.py:117-121` regex `PERFORMANCE_WARNINGS` | MEDIUM | TODO | P-3 |
| #5 | `queries.py:217` `build_schema_context_cached` | HIGH | ✅ DONE | P-2b (`3a9d195`) |
| #6 | `queries.py:270-274` no connection pooling | HIGH | TODO | P-3 |
| #7 | `queries.py:500-504` O(n) share-token loop | MEDIUM | ✅ DONE | P-1 |
| #8 | `lineage.py` graph computed per request | MEDIUM | TODO | P-2c |

## Suggested execution order

Pick from the top — each sprint stands alone and lands as a
self-contained PR:

1. **P-2b** — schema context cache (finding #5). Highest pain
   (blocks the event loop during query execution). Reuses the
   pattern from P-2a.
2. **P-2c** — lineage materialised view (finding #8). Smaller blast
   radius than #5, same materialised-view pattern.
3. **P-3** — sqlglot AST replaces regex + naive cost estimator
   (#3 + #4). Add connection pooling (#6) in the same bundle
   since it touches the same connector path.

## The proven pattern (P-1 + P-2a)

Both completed sprints converged on this shape:

### 1. Add the table to `aurabackend/api_gateway/persistence.py`

* New SQLAlchemy `Base`-derived model with a composite index that
  satisfies the read endpoint's ORDER BY without an in-memory sort.
* Primary key is the natural identity (file_path, record_hash,
  workspace_id+slug) — don't auto-increment if a natural key
  exists; upsert semantics fall out for free.

### 2. Add async repository functions

* `insert_*` / `list_*` / `get_*` / `update_*` / `delete_*` — typed
  inputs, return wire-shape dicts that match the legacy in-memory
  store's shape exactly. Endpoints don't change their response
  contract.
* CPU-bound work (e.g. DuckDB COUNT) runs in `asyncio.to_thread`
  to keep the event loop free.

### 3. Refactor the read endpoint

* Replace per-request computation with a single repository call.
* **Cold-start guard:** if the cache is empty but reality has data,
  return zeros / sensible defaults + spawn an async refresh as a
  background task. NEVER block the caller doing the heavy work
  inline — that defeats the cache.

### 4. Add the populate-on-write hook

* Whatever endpoint causes new data (file upload, query save,
  saved-query schedule change) writes the cache after the
  primary action. Run as `asyncio.create_task(index_*)` — best
  effort, never delays the primary response.

### 5. Add the 60s background refresh tick

* New helper in `main.py` lifespan that wakes every 60s, walks
  reality (upload dir, saved_queries table, etc.), and re-indexes
  anything stale (mtime / updated_at differs from cache).
* Idempotent + interval-configurable via env var. Pattern in
  `_file_metadata_refresh_loop` from P-2a.

### 6. Lazy-init via `session_scope()`

* Already wired into `persistence.py` — your new table benefits
  for free. **Don't break this**: it's what lets tests work
  without explicit fixture init.

### 7. Tests

* SQLite-backed contract tests in `aurabackend/tests/test_api_gateway_persistence.py`
  (add to the existing file). Cover: insert + upsert + workspace
  isolation if applicable + missing-file pruning + stale-mtime
  refresh.
* If you add a new Postgres-specific feature (LISTEN/NOTIFY, JSONB
  query), add a new CI lane following the `scheduler-distributed-test`
  pattern.

## P-2b — Schema context cache (finding #5)

**Problem:** `await build_schema_context_cached(con, upload_dirs, use_llm=True)`
at `queries.py:217` runs during query execution. It synchronously
reads every uploaded file's schema and (with `use_llm=True`) calls
the LLM to enrich the context. This blocks the event loop for
seconds.

**Suggested approach:**
1. Schema fingerprinting key: hash of `(upload_dir, sorted list of (filename, mtime))`.
   Same fingerprint → same context.
2. Cache table `gateway_schema_context` (fingerprint PK + context JSON
   + last_indexed_at).
3. Populate-on-write: `files.py` upload hook recomputes after each
   upload. Run in `asyncio.to_thread`.
4. Endpoint reads the latest cached context for the workspace.
   Cold-start guard returns an empty context + kicks off refresh.

**Files to touch:**
* `aurabackend/api_gateway/persistence.py` — new model + helpers.
* `aurabackend/api_gateway/routers/queries.py:217` — swap the
  blocking call for a `persistence.get_schema_context(workspace_id)`.
* `aurabackend/api_gateway/routers/files.py:upload_universal` —
  hook the cache refresh.
* `aurabackend/api_gateway/main.py` — extend the existing
  `_file_metadata_refresh_loop` to also refresh schema context.

**Tests to add:**
* Cache populates on upload.
* Stale entries (mtime change) re-index.
* Endpoint returns the cached value, not a fresh compute.
* Cold-start returns empty context + background refresh works.

## P-2c — Lineage materialised view (finding #8)

**Problem:** `aurabackend/api_gateway/routers/lineage.py::get_lineage`
walks every saved query + dashboard on every request, parses each
SQL with `_extract_tables`, and builds the graph from scratch.
O(saved_queries × ave SQL length) per request.

**Suggested approach:**
1. Cache table `gateway_lineage_edges` (source_node, target_node,
   edge_type, workspace_id, last_indexed_at). Composite index on
   `(workspace_id, edge_type)`.
2. Populate-on-write: rebuild for a workspace whenever a saved
   query is created / updated / deleted OR a dashboard is touched.
   Hook via the existing `persistence.insert_saved_query` /
   `persistence.update_saved_query` / `persistence.delete_saved_query`
   functions — they're the choke points.
3. Endpoint reads the workspace's edges; assembles nodes + edges
   into the existing response shape.

**Files to touch:**
* `aurabackend/api_gateway/persistence.py` — model + helpers.
* `aurabackend/api_gateway/routers/lineage.py` — swap the inline
  computation for a repository read.
* `aurabackend/api_gateway/routers/queries.py` + `dashboards.py` —
  hook the rebuild after writes (small wrappers around the existing
  persistence writes).

**Tests to add:**
* New saved query → new edges in the cache.
* Deleting a saved query removes its edges.
* Updating the SQL of a saved query rewrites its edges.
* Endpoint response matches what the old inline computation would
  have produced (snapshot test).

## P-3 — sqlglot AST + connection pooling (#3 + #4 + #6)

**Problem:**
* `safety/validator.py:_estimate_query_cost` is a string-match heuristic
  that multiplies by `2 ** join_count` — wildly inaccurate, can
  overestimate by orders of magnitude.
* `safety/validator.py` PERFORMANCE_WARNINGS regex sweep runs on every
  query validation.
* `queries.py:execute_query` opens a new connection per query, no
  pool.

**Suggested approach:**
1. Add `sqlglot` to `aurabackend/requirements.txt`.
2. Rewrite `_estimate_query_cost` to parse the SQL once via
   `sqlglot.parse_one(query, dialect="postgres")`, then walk the
   AST counting `exp.Join`, `exp.Subquery`, `exp.Select.has_window_func`.
3. Rewrite the regex-based warnings to AST traversals:
   * `WHERE col LIKE '%...'` → look for `exp.Like` with a leading
     wildcard literal.
   * `SELECT *` → `exp.Select` with `expressions == [exp.Star]`.
   * `CARTESIAN JOIN` → a `exp.Select` with `len(joins) == 0` and
     `len(from_.expressions) > 1`.
4. Connection pooling: add a global `asyncpg.Pool` (or per-connector
   pool) initialised in the gateway lifespan. Endpoints borrow +
   return.

**Files to touch:**
* `aurabackend/requirements.txt` — add sqlglot.
* `aurabackend/safety/validator.py` — replace `_estimate_query_cost`
  + `PERFORMANCE_WARNINGS` block.
* `aurabackend/api_gateway/routers/queries.py:execute_query` —
  borrow connection from pool instead of `connector.connect()`.
* `aurabackend/api_gateway/main.py` — initialise pool in lifespan,
  close on shutdown.

**Tests to add:**
* AST-based cost estimator returns sensible values for known
  shapes (single SELECT vs 3-join vs 5-subquery).
* Wildcard LIKE detection (leading wildcard, not trailing).
* Connection pool: 100 concurrent requests share N connections,
  not 100.

## Coordination with Mouni's track

Mouni is working on **S22 (TMLE)** on `feature/s22-tmle`. That branch
touches `aurabackend/counterfactual_service/` only. The audit burn-
down touches `aurabackend/api_gateway/` and `aurabackend/safety/`.
**No expected merge conflicts.**

When you finish a sprint:
1. Push your feature branch.
2. Open a PR against `main`.
3. Update `docs/SPRINTS.md` — move your row from In Flight to
   Completed, add the merge SHA.
4. Update this file — mark the finding ✅ DONE.

If you hit a wall (e.g., sqlglot can't parse a real query), open
a GitHub issue and tag Mouni. We can rubber-duck in the issue
thread or escalate.

Last updated 2026-05-12 — P-2b landed, finding #5 closed. 4 findings remain (#3, #4, #6, #8).
