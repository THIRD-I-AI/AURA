# S50 — Durable, Tenant-Scoped Chat Sessions & Pipelines

**Status:** Approved (2026-06-22)
**Author:** Rohith (Claude Opus 4.8) with Mounith

## Problem

Two subsystems were built before the P-1/S42/S43 persistence machinery existed
and still hold state in module-global in-memory dicts:

- **Chat history** — `_chat_history_store: Dict[str, List]` in
  `api_gateway/routers/chat.py:124`, keyed only by `session_id`.
- **Pipelines** — `PipelineEngine._pipelines: Dict[str, Pipeline]` in
  `pipeline/engine.py:58`.

Consequences (all blockers for enterprise deployment):

1. **No durability** — both are wiped on every restart.
2. **No tenant isolation** — pipelines have no `org_id` anywhere; and
   `GET /chat/history/{session_id}` (`chat.py:313`) takes no `Request` and does
   no auth/tenant check, so chat history is readable cross-tenant by guessing a
   session id.
3. **No multi-replica scale-out** — each gateway pod has its own copy.

Every other gateway store (queries, saved-queries, files, schema-context,
lineage edges) already lives in `api_gateway/persistence.py`, tenant-scoped by
`workspace_id` (resolved from the JWT `org_id` via `current_workspace_id()`).
S50 brings these last two into line. **The in-memory stores are ephemeral, so
there is no existing data to back-fill** — this is a forward-only switch.

## Goals

1. Durable chat history and pipelines (survive restart).
2. Tenant isolation: every read/write filtered by `workspace_id`; close the
   `GET /chat/history` cross-tenant hole.
3. Multi-replica safe (shared DB, no module-global mutable state).
4. Follow the established `persistence.py` pattern exactly; existing tests green.

## Non-Goals (explicit follow-ons this unblocks, NOT in S50)

- Conversation-sidebar UX (list/name/switch sessions in the frontend).
- Pipeline run-history persistence + monitoring dashboards + alerting.
- The chat→pipeline→self-heal agentic loop.
- Splitting `persistence.py` into smaller modules (kept as-is for pattern
  consistency + guaranteed `Base.metadata` registration).

## Architecture

### New ORM models (in `api_gateway/persistence.py`, on the shared `Base`)

```python
class ChatMessageRow(Base):
    __tablename__ = "gateway_chat_messages"
    id           = Column(String(64), primary_key=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    session_id   = Column(String(128), nullable=False)
    type         = Column(String(32), nullable=False, default="user")
    content      = Column(Text, nullable=False, default="")
    metadata_json = Column(Text, nullable=True)
    timestamp    = Column(String(64), nullable=False)   # wire ISO string
    created_ts   = Column(Float, nullable=False)        # numeric, for ORDER BY
    __table_args__ = (
        Index("ix_chat_ws_session_created", workspace_id, session_id, created_ts),
    )

class PipelineRow(Base):
    __tablename__ = "gateway_pipelines"
    id             = Column(String(64), primary_key=True)
    workspace_id   = Column(String(64), nullable=False, index=True)
    name           = Column(String(255), nullable=False, default="")
    description    = Column(Text, nullable=True)
    definition_json = Column(Text, nullable=False)      # full Pipeline.model_dump()
    status         = Column(String(32), nullable=False, default="draft")
    source_label   = Column(String(255), nullable=False, default="")
    sink_type      = Column(String(64), nullable=False, default="")
    step_count     = Column(Integer, nullable=False, default=0)
    tags_json      = Column(Text, nullable=True)        # JSON list
    created_at     = Column(String(64), nullable=False)
    updated_at     = Column(String(64), nullable=False)
    created_ts     = Column(Float, nullable=False)
    __table_args__ = (
        Index("ix_pipelines_ws_created", workspace_id, created_ts.desc()),
    )
```

`create_all` (run from the lifespan and lazily from `session_scope`) creates
these on restart. They are *new* tables, so unlike the historic `org_id`
column-ALTER gap, no migration script is required for dev/SQLite; Postgres prod
follows the existing "run Alembic for new tables" convention.

Caps (centralised constants, mirroring `QUERY_HISTORY_CAP`):
`CHAT_MESSAGES_PER_SESSION_CAP = 100` (matches today), `PIPELINES_PER_WORKSPACE_CAP = 200`.

### New repository functions (in `persistence.py`, same style as saved-queries)

Chat:
- `async insert_chat_message(workspace_id, record) -> dict` — insert + evict
  beyond 100 newest for `(workspace_id, session_id)`.
- `async list_chat_messages(workspace_id, session_id) -> list[dict]` —
  oldest-first (chat reads chronologically), workspace+session filtered.
- `async list_chat_sessions(workspace_id) -> list[dict]` — distinct sessions for
  a tenant with `last_ts` + `message_count` (answers "where do chats hold";
  enables the deferred sidebar without building it).

Pipelines:
- `async save_pipeline(workspace_id, pipeline_dict) -> dict` — upsert by `id`
  (insert or update); enforces the per-workspace cap on insert.
- `async list_pipelines(workspace_id) -> list[dict]` — the list-view shape
  (id/name/description/source/steps/sink/status/created_at/tags), newest-first.
- `async get_pipeline(pipeline_id, workspace_id=None) -> dict | None` — scoped
  when `workspace_id` set (private endpoints); **unscoped when None** for the
  trusted internal path (webhook-triggered runs). This mirrors `get_saved_query`
  exactly.
- `async delete_pipeline(pipeline_id, workspace_id) -> bool`.

All filter on `workspace_id` — the isolation guarantee lives in one place.

### Router rewiring

`api_gateway/routers/chat.py`:
- Delete `_chat_history_store`, `_chat_history_lock`, the in-memory imports.
- `get_chat_history` + `save_chat_message` gain `http_request: Request` and
  resolve `workspace_id = current_workspace_id(http_request)`, then call the
  repo functions. **This is what closes the cross-tenant hole.**
- `chat_endpoint` already has `http_request`; no behavioural change (it doesn't
  persist messages itself today — the frontend calls `POST /chat/history`).

`api_gateway/routers/pipelines.py`:
- `pipeline_save`/`pipeline_list`/`pipeline_get`/`pipeline_delete` gain
  `request: Request`, resolve `workspace_id = current_workspace_id(request)`,
  and call the new repo functions instead of `_pipeline_engine`.
- `PipelineEngine` keeps only `execute()` (+ helpers it uses). Its in-memory
  `_pipelines` dict and `save/get/list_all/delete` methods are removed — the
  engine becomes stateless (replica-safe).

`api_gateway/routers/inbound_hooks.py:171`:
- `_pipeline_engine.get(pipeline_id)` → `await get_pipeline(pipeline_id)`
  (unscoped trusted fetch) then `PipelineModel(**definition)` to rebuild the
  model for `execute()`. (Webhook context has no JWT; the registered hook is the
  trust boundary, same rationale as the public share-token path.)

### Data flow

`current_workspace_id(request)` → `workspace_id` → repo function → `session_scope()`
transaction → `gateway_chat_messages` / `gateway_pipelines` filtered by
`workspace_id`. No module-global state; correct across replicas.

### Error handling

- Repo functions run inside `session_scope()` (commit on success, rollback on
  error) — same as existing functions.
- Malformed `metadata_json`/`tags_json`/`definition_json` on read: log + skip
  the bad field (mirrors `_row_to_saved_query_dict`'s `schedule_json` handling),
  never 500.
- Chat-history tracking stays best-effort: a persistence failure must not break
  the `/chat` response path (the existing `_track_query` swallow pattern).

## Testing (pytest, `tests/test_durable_tenant_stores.py`)

Following `reset_all_for_tests()` + the existing gateway-persistence test style:

1. **Tenant isolation (load-bearing):** tenant A writes a chat message (session S)
   and saves a pipeline; tenant B's `list_chat_messages`/`list_chat_sessions`/
   `list_pipelines`/`get_pipeline` return nothing for A's data; B cannot delete
   A's pipeline.
2. **Durability:** write → re-read returns the same record (proves it's not the
   ephemeral dict).
3. **Caps:** 101st message in a session evicts the oldest; 201st pipeline in a
   workspace evicts per-workspace.
4. **Unscoped fetch:** `get_pipeline(id, workspace_id=None)` returns the row
   (webhook path); `get_pipeline(id, other_ws)` returns None.
5. **Endpoint scoping:** `GET /chat/history/{session}` under tenant A's JWT does
   not return tenant B's session; unauth dev mode falls back to `default`.

Plus: existing `tests/test_chat*.py`, `tests/test_pipeline*.py`, and the gateway
persistence suite stay green. Pre-push: `ruff` + the touched test files via the
repo-root `.venv`.

## File Structure

```
aurabackend/api_gateway/persistence.py          — +2 models, +7 repo fns, +2 caps, __all__
aurabackend/api_gateway/routers/chat.py         — drop in-mem store; scope history endpoints
aurabackend/api_gateway/routers/pipelines.py    — CRUD → repo fns, scoped by request
aurabackend/pipeline/engine.py                  — remove in-mem _pipelines + CRUD; execute-only
aurabackend/api_gateway/routers/inbound_hooks.py — get via repo (unscoped), rebuild model
aurabackend/tests/test_durable_tenant_stores.py — new tenant-isolation + durability suite
```

## Verification Checklist

- [ ] Chat history persists across a gateway restart.
- [ ] Pipelines persist across a gateway restart.
- [ ] Tenant B cannot read/delete tenant A's chat or pipelines (test + manual).
- [ ] `GET /chat/history/{session}` requires/uses the caller's tenant.
- [ ] Webhook-triggered pipeline run still resolves the pipeline (unscoped get).
- [ ] Per-session (100) and per-workspace (200) caps enforced.
- [ ] All existing backend tests green; ruff clean.
```
