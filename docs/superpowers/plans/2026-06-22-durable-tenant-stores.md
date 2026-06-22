# S50 Durable Tenant-Scoped Stores — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move chat history and pipelines off in-memory global dicts onto the existing durable, tenant-scoped gateway persistence layer.

**Architecture:** Add two ORM models + repository functions to `api_gateway/persistence.py` (the established pattern), then rewire `chat.py` and `pipelines.py` to call them scoped by `current_workspace_id(request)`. `PipelineEngine` becomes execute-only.

**Tech Stack:** Python, FastAPI, SQLAlchemy async (SQLite dev / Postgres prod), pytest.

## Global Constraints

- Branch `feature/s50-durable-tenant-stores`; additive — existing tests stay green.
- Run pytest from `aurabackend/` via the repo-root venv: `../.venv/Scripts/python.exe -m pytest <file> -q` (the `aurabackend/.venv` pytest is a broken stub — trust only the repo-root `.venv`).
- Pre-push ruff: `python -m ruff check --select E,F,I,W --ignore E501,E402,F401,W191,W291,W293,F841,E701,E712,F823 .`
- Tenant key is `current_workspace_id(request)` (from `api_gateway/routers/workspaces.py`): authenticated → `org_id` (or `org_id::folder`); unauth dev → `default`. Never trust the client `X-Workspace-Id` for isolation.
- Caps: `CHAT_MESSAGES_PER_SESSION_CAP = 100`, `PIPELINES_PER_WORKSPACE_CAP = 200`.
- New tables register on the shared `persistence.Base`; `create_all` (lifespan + lazy `session_scope`) creates them — no migration script for dev.
- Co-author trailer on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: Persistence models + repository functions + tests

**Files:**
- Modify: `aurabackend/api_gateway/persistence.py` (add 2 models, 2 caps, 9 functions, `__all__`)
- Create: `aurabackend/tests/test_durable_tenant_stores.py`

**Interfaces:**
- Consumes: existing `Base`, `session_scope`, `reset_all_for_tests` from `persistence.py`.
- Produces: `ChatMessageRow`, `PipelineRow`; `insert_chat_message(workspace_id, record)`, `list_chat_messages(workspace_id, session_id)`, `list_chat_sessions(workspace_id)`, `save_pipeline(record)`, `list_pipelines(workspace_id)`, `get_pipeline(pipeline_id, workspace_id=None)`, `delete_pipeline(pipeline_id, workspace_id)`.

- [ ] **Step 1: Write the failing test** — `aurabackend/tests/test_durable_tenant_stores.py`

```python
import os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway import persistence as p

@pytest.fixture(autouse=True)
async def fresh_db():
    await p.reset_all_for_tests()
    yield

def _msg(session_id, content, mtype="user"):
    return {"id": None, "session_id": session_id, "type": mtype, "content": content, "timestamp": "2026-06-22T00:00:00", "metadata": {"k": 1}}

def _pipe(pid, ws, name="P"):
    return {"id": pid, "workspace_id": ws, "name": name, "description": "d",
            "definition": {"id": pid, "name": name, "steps": [{"x": 1}]},
            "status": "draft", "source_label": "file:a.csv", "sink_type": "table",
            "step_count": 1, "tags": ["t"], "created_at": "2026-06-22T00:00:00", "updated_at": None}

@pytest.mark.asyncio
async def test_chat_durability_and_isolation():
    await p.insert_chat_message("orgA", _msg("s1", "hi A"))
    await p.insert_chat_message("orgB", _msg("s1", "hi B"))
    a = await p.list_chat_messages("orgA", "s1")
    assert [m["content"] for m in a] == ["hi A"]
    assert a[0]["metadata"] == {"k": 1}
    b = await p.list_chat_messages("orgB", "s1")
    assert [m["content"] for m in b] == ["hi B"]
    sessions = await p.list_chat_sessions("orgA")
    assert sessions[0]["session_id"] == "s1" and sessions[0]["message_count"] == 1

@pytest.mark.asyncio
async def test_chat_session_cap():
    for i in range(105):
        await p.insert_chat_message("orgA", _msg("s1", f"m{i}"))
    rows = await p.list_chat_messages("orgA", "s1")
    assert len(rows) == p.CHAT_MESSAGES_PER_SESSION_CAP
    assert rows[-1]["content"] == "m104"  # newest kept, oldest evicted

@pytest.mark.asyncio
async def test_pipeline_durability_and_isolation():
    await p.save_pipeline(_pipe("p1", "orgA", "A-pipe"))
    await p.save_pipeline(_pipe("p2", "orgB", "B-pipe"))
    la = await p.list_pipelines("orgA")
    assert [x["name"] for x in la] == ["A-pipe"]
    assert la[0]["source"] == "file:a.csv" and la[0]["steps"] == 1 and la[0]["tags"] == ["t"]
    assert await p.list_pipelines("orgB")  # B sees its own
    # B cannot read or delete A's pipeline
    assert await p.get_pipeline("p1", workspace_id="orgB") is None
    assert await p.delete_pipeline("p1", "orgB") is False
    # unscoped get (webhook path) returns the full definition
    full = await p.get_pipeline("p1")
    assert full["id"] == "p1" and full["steps"] == [{"x": 1}]
    # scoped delete works for the owner
    assert await p.delete_pipeline("p1", "orgA") is True
    assert await p.get_pipeline("p1") is None

@pytest.mark.asyncio
async def test_pipeline_upsert_updates_in_place():
    await p.save_pipeline(_pipe("p1", "orgA", "v1"))
    await p.save_pipeline(_pipe("p1", "orgA", "v2"))
    rows = await p.list_pipelines("orgA")
    assert len(rows) == 1 and rows[0]["name"] == "v2"
```

- [ ] **Step 2: Run to verify it fails** — from `aurabackend/`: `../.venv/Scripts/python.exe -m pytest tests/test_durable_tenant_stores.py -q` → FAIL (AttributeError: module has no attribute `ChatMessageRow`/`insert_chat_message`).

- [ ] **Step 3a: Add models** — in `persistence.py`, after `LineageEdgeRow` (before the engine section), add:

```python
class ChatMessageRow(Base):
    """One chat message, tenant-scoped, capped per (workspace, session)."""
    __tablename__ = "gateway_chat_messages"
    id = Column(String(64), primary_key=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    session_id = Column(String(128), nullable=False)
    type = Column(String(32), nullable=False, default="user")
    content = Column(Text, nullable=False, default="")
    metadata_json = Column(Text, nullable=True)
    timestamp = Column(String(64), nullable=False)
    created_ts = Column(Float, nullable=False)
    __table_args__ = (
        Index("ix_chat_ws_session_created", workspace_id, session_id, created_ts),
    )


class PipelineRow(Base):
    """One saved pipeline, tenant-scoped. ``definition_json`` is the full
    Pipeline.model_dump(); the denormalised columns drive the list view
    without parsing JSON per row."""
    __tablename__ = "gateway_pipelines"
    id = Column(String(64), primary_key=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    name = Column(String(255), nullable=False, default="")
    description = Column(Text, nullable=True)
    definition_json = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="draft")
    source_label = Column(String(255), nullable=False, default="")
    sink_type = Column(String(64), nullable=False, default="")
    step_count = Column(Integer, nullable=False, default=0)
    tags_json = Column(Text, nullable=True)
    created_at = Column(String(64), nullable=False)
    updated_at = Column(String(64), nullable=False)
    created_ts = Column(Float, nullable=False)
    __table_args__ = (
        Index("ix_pipelines_ws_created", workspace_id, created_ts.desc()),
    )
```

- [ ] **Step 3b: Add caps + functions** — after the `SAVED_QUERIES_PER_WORKSPACE_CAP` constant add `CHAT_MESSAGES_PER_SESSION_CAP = 100` and `PIPELINES_PER_WORKSPACE_CAP = 200`; then, before `reset_all_for_tests`, add:

```python
# ── Chat messages (S50) ──

import uuid as _uuid


def _row_to_chat_dict(row: "ChatMessageRow") -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "id": row.id, "type": row.type, "content": row.content,
        "timestamp": row.timestamp,
    }
    if row.metadata_json:
        try:
            out["metadata"] = json.loads(row.metadata_json)
        except json.JSONDecodeError:
            logger.warning("chat_message %s has malformed metadata_json", row.id)
    return out


async def insert_chat_message(workspace_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Persist one chat message; evict beyond the per-session cap."""
    session_id = record["session_id"]
    async with session_scope() as s:
        row = ChatMessageRow(
            id=record.get("id") or str(_uuid.uuid4())[:12],
            workspace_id=workspace_id,
            session_id=session_id,
            type=record.get("type", "user"),
            content=record.get("content", "") or "",
            metadata_json=(
                json.dumps(record["metadata"], separators=(",", ":"))
                if record.get("metadata") is not None else None
            ),
            timestamp=record.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            created_ts=time.time(),
        )
        s.add(row)
        await s.flush()
        keep_ids = (await s.execute(
            select(ChatMessageRow.id)
            .where(ChatMessageRow.workspace_id == workspace_id)
            .where(ChatMessageRow.session_id == session_id)
            .order_by(ChatMessageRow.created_ts.desc())
            .limit(CHAT_MESSAGES_PER_SESSION_CAP)
        )).scalars().all()
        if keep_ids:
            await s.execute(
                delete(ChatMessageRow)
                .where(ChatMessageRow.workspace_id == workspace_id)
                .where(ChatMessageRow.session_id == session_id)
                .where(ChatMessageRow.id.notin_(keep_ids))
            )
        return _row_to_chat_dict(row)


async def list_chat_messages(workspace_id: str, session_id: str) -> List[Dict[str, Any]]:
    """Oldest-first messages for one (tenant, session)."""
    async with session_scope() as s:
        rows = (await s.execute(
            select(ChatMessageRow)
            .where(ChatMessageRow.workspace_id == workspace_id)
            .where(ChatMessageRow.session_id == session_id)
            .order_by(ChatMessageRow.created_ts.asc())
        )).scalars().all()
        return [_row_to_chat_dict(r) for r in rows]


async def list_chat_sessions(workspace_id: str) -> List[Dict[str, Any]]:
    """Distinct sessions for a tenant, most-recent-first."""
    async with session_scope() as s:
        rows = (await s.execute(
            select(
                ChatMessageRow.session_id,
                func.max(ChatMessageRow.created_ts).label("last_ts"),
                func.count().label("n"),
            )
            .where(ChatMessageRow.workspace_id == workspace_id)
            .group_by(ChatMessageRow.session_id)
            .order_by(func.max(ChatMessageRow.created_ts).desc())
        )).all()
        return [
            {"session_id": r[0], "last_ts": r[1], "message_count": r[2]}
            for r in rows
        ]


# ── Pipelines (S50) ──


def _row_to_pipeline_list_dict(row: "PipelineRow") -> Dict[str, Any]:
    return {
        "id": row.id, "name": row.name, "description": row.description,
        "source": row.source_label, "steps": row.step_count,
        "sink": row.sink_type, "status": row.status,
        "created_at": row.created_at,
        "tags": json.loads(row.tags_json) if row.tags_json else [],
    }


async def save_pipeline(record: Dict[str, Any]) -> Dict[str, Any]:
    """Upsert a pipeline by id within its workspace; cap per workspace on insert."""
    workspace_id = record["workspace_id"]
    now = record.get("updated_at") or datetime.now(timezone.utc).isoformat()
    async with session_scope() as s:
        existing = (await s.execute(
            select(PipelineRow)
            .where(PipelineRow.id == record["id"])
            .where(PipelineRow.workspace_id == workspace_id)
        )).scalar_one_or_none()
        fields = dict(
            name=record.get("name", "") or "",
            description=record.get("description"),
            definition_json=json.dumps(record["definition"], separators=(",", ":")),
            status=record.get("status", "draft"),
            source_label=record.get("source_label", "") or "",
            sink_type=record.get("sink_type", "") or "",
            step_count=int(record.get("step_count", 0) or 0),
            tags_json=json.dumps(record.get("tags") or []),
            updated_at=now,
        )
        if existing is None:
            s.add(PipelineRow(
                id=record["id"], workspace_id=workspace_id,
                created_at=record.get("created_at") or now,
                created_ts=time.time(), **fields,
            ))
            await s.flush()
            keep_ids = (await s.execute(
                select(PipelineRow.id)
                .where(PipelineRow.workspace_id == workspace_id)
                .order_by(PipelineRow.created_ts.desc())
                .limit(PIPELINES_PER_WORKSPACE_CAP)
            )).scalars().all()
            if keep_ids:
                await s.execute(
                    delete(PipelineRow)
                    .where(PipelineRow.workspace_id == workspace_id)
                    .where(PipelineRow.id.notin_(keep_ids))
                )
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
            await s.flush()
        return {"id": record["id"], "name": fields["name"]}


async def list_pipelines(workspace_id: str) -> List[Dict[str, Any]]:
    async with session_scope() as s:
        rows = (await s.execute(
            select(PipelineRow)
            .where(PipelineRow.workspace_id == workspace_id)
            .order_by(PipelineRow.created_ts.desc())
        )).scalars().all()
        return [_row_to_pipeline_list_dict(r) for r in rows]


async def get_pipeline(
    pipeline_id: str, workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return the full pipeline definition dict. Scoped when workspace_id is
    set (private endpoints); unscoped when None (trusted webhook path)."""
    async with session_scope() as s:
        stmt = select(PipelineRow).where(PipelineRow.id == pipeline_id)
        if workspace_id is not None:
            stmt = stmt.where(PipelineRow.workspace_id == workspace_id)
        row = (await s.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        try:
            return json.loads(row.definition_json)
        except json.JSONDecodeError:
            logger.warning("pipeline %s has malformed definition_json", pipeline_id)
            return None


async def delete_pipeline(pipeline_id: str, workspace_id: str) -> bool:
    async with session_scope() as s:
        existing = (await s.execute(
            select(PipelineRow.id)
            .where(PipelineRow.id == pipeline_id)
            .where(PipelineRow.workspace_id == workspace_id)
        )).scalar_one_or_none()
        if existing is None:
            return False
        await s.execute(delete(PipelineRow).where(PipelineRow.id == pipeline_id))
        return True
```

- [ ] **Step 3c: Extend `__all__`** — add the new names: `"ChatMessageRow", "PipelineRow", "CHAT_MESSAGES_PER_SESSION_CAP", "PIPELINES_PER_WORKSPACE_CAP", "insert_chat_message", "list_chat_messages", "list_chat_sessions", "save_pipeline", "list_pipelines", "get_pipeline", "delete_pipeline"`.

- [ ] **Step 4: Run tests** — from `aurabackend/`: `../.venv/Scripts/python.exe -m pytest tests/test_durable_tenant_stores.py -q` → 4 passed. Then ruff on `api_gateway/persistence.py`.

- [ ] **Step 5: Commit**

```bash
git add aurabackend/api_gateway/persistence.py aurabackend/tests/test_durable_tenant_stores.py
git commit -m "feat(s50): durable tenant-scoped chat + pipeline persistence layer"
```

---

### Task 2: Rewire chat history endpoints (tenant-scoped, durable)

**Files:**
- Modify: `aurabackend/api_gateway/routers/chat.py`

**Interfaces:**
- Consumes: `insert_chat_message`, `list_chat_messages` (Task 1); `current_workspace_id` (`.workspaces`).

- [ ] **Step 1: Add a tenant-isolation test** to `tests/test_durable_tenant_stores.py`:

```python
@pytest.mark.asyncio
async def test_chat_history_endpoint_scopes_by_tenant(monkeypatch):
    from fastapi import Request
    from api_gateway.routers import chat as chatmod
    # Simulate two tenants by stubbing current_workspace_id.
    seen = {}
    def fake_ws(req):
        return seen["ws"]
    monkeypatch.setattr(chatmod, "current_workspace_id", fake_ws)
    req = Request({"type": "http", "headers": [], "method": "POST"})
    seen["ws"] = "orgA"
    await chatmod.save_chat_message("s1", {"type": "user", "content": "from A"}, req)
    seen["ws"] = "orgB"
    out_b = await chatmod.get_chat_history("s1", req)
    assert out_b == []  # B sees nothing of A's session
    seen["ws"] = "orgA"
    out_a = await chatmod.get_chat_history("s1", req)
    assert [m.content for m in out_a] == ["from A"]
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_durable_tenant_stores.py::test_chat_history_endpoint_scopes_by_tenant -q` → FAIL (signature mismatch: endpoints don't take a request).

- [ ] **Step 3: Rewire `chat.py`.**
  (a) Delete the in-memory block (`import threading`, `_chat_history_lock`, `_chat_history_store`).
  (b) Add imports near the top: `from api_gateway.persistence import insert_chat_message, list_chat_messages` and extend the existing `.workspaces` import to `from .workspaces import _request_tenant, current_workspace_id, tenant_upload_dir`.
  (c) Replace the two history endpoints:

```python
@router.get("/chat/history/{session_id}", response_model=List[ChatHistoryEntry])
async def get_chat_history(session_id: str, http_request: Request) -> List[ChatHistoryEntry]:
    """Tenant-scoped chat history for a session."""
    workspace_id = current_workspace_id(http_request)
    messages = await list_chat_messages(workspace_id, session_id)
    return [ChatHistoryEntry(**m) for m in messages]


@router.post("/chat/history/{session_id}", response_model=SaveChatResponse)
async def save_chat_message(
    session_id: str, payload: Dict[str, Any], http_request: Request,
) -> SaveChatResponse:
    """Append a tenant-scoped chat message."""
    workspace_id = current_workspace_id(http_request)
    saved = await insert_chat_message(workspace_id, {
        "id": payload.get("id"),
        "session_id": session_id,
        "type": payload.get("type", "user"),
        "content": payload.get("content", ""),
        "timestamp": payload.get("timestamp"),
        "metadata": payload.get("metadata"),
    })
    return SaveChatResponse(success=True, id=saved["id"])
```

  (d) Remove the now-unused `_uuid` import only if nothing else uses it (the `import uuid as _uuid` at module top — keep if other code references it; the conversational early-return uses `session_id`, not uuid). Leave `from datetime import datetime` (still used).

- [ ] **Step 4: Run tests** — `pytest tests/test_durable_tenant_stores.py tests/test_chat_pipeline.py::test_chat_history_roundtrip -q` → all pass (the roundtrip test is unauth → `default`, still works). Ruff on `chat.py`.

- [ ] **Step 5: Commit**

```bash
git add aurabackend/api_gateway/routers/chat.py aurabackend/tests/test_durable_tenant_stores.py
git commit -m "feat(s50): tenant-scope + persist chat history; close cross-tenant read hole"
```

---

### Task 3: Rewire pipeline CRUD endpoints to the durable store

**Files:**
- Modify: `aurabackend/api_gateway/routers/pipelines.py`

**Interfaces:**
- Consumes: `save_pipeline`, `list_pipelines`, `get_pipeline`, `delete_pipeline` (Task 1); `current_workspace_id` (`.workspaces`).

- [ ] **Step 1: Add an endpoint test** to `tests/test_durable_tenant_stores.py`:

```python
@pytest.mark.asyncio
async def test_pipeline_endpoints_scope_by_tenant(monkeypatch):
    from fastapi import Request
    from api_gateway.routers import pipelines as plmod
    seen = {}
    monkeypatch.setattr(plmod, "current_workspace_id", lambda req: seen["ws"])
    req = Request({"type": "http", "headers": [], "method": "POST"})
    pdef = {"id": "pX", "name": "Endpoint pipe", "source": {"type": "file", "file_name": "a.csv"},
            "steps": [], "sink": {"type": "table"}, "tags": []}
    seen["ws"] = "orgA"
    saved = await plmod.pipeline_save(plmod.PipelineSaveRequest(pipeline=pdef), req)
    assert saved["pipeline_id"] == "pX"
    listed = await plmod.pipeline_list(req)
    assert any(x["id"] == "pX" for x in listed["pipelines"])
    seen["ws"] = "orgB"
    listed_b = await plmod.pipeline_list(req)
    assert all(x["id"] != "pX" for x in listed_b["pipelines"])
```
(If the minimal `pdef` fails `PipelineModel` validation, adjust the source/sink to the model's required fields — confirm against `pipeline/models.py` while implementing.)

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_durable_tenant_stores.py::test_pipeline_endpoints_scope_by_tenant -q` → FAIL (endpoints don't take a request / still use `_pipeline_engine`).

- [ ] **Step 3: Rewire `pipelines.py`.**
  (a) Add imports: `from api_gateway.persistence import save_pipeline, list_pipelines, get_pipeline, delete_pipeline` and extend the `.workspaces` import to include `current_workspace_id`.
  (b) Replace the four CRUD endpoints:

```python
@router.post("/pipeline/save")
async def pipeline_save(req: PipelineSaveRequest, request: Request):
    """Save a pipeline definition for later use (tenant-scoped)."""
    try:
        pipeline = PipelineModel(**req.pipeline)
    except Exception as e:
        logger.warning("[Pipeline] Invalid pipeline on save: %s", e)
        raise HTTPException(status_code=400, detail="Invalid pipeline payload")
    saved = await save_pipeline({
        "id": pipeline.id,
        "workspace_id": current_workspace_id(request),
        "name": pipeline.name,
        "description": pipeline.description,
        "definition": pipeline.model_dump(),
        "status": pipeline.status.value,
        "source_label": pipeline.source.label(),
        "sink_type": pipeline.sink.type.value,
        "step_count": len(pipeline.steps or []),
        "tags": pipeline.tags or [],
        "created_at": pipeline.created_at,
        "updated_at": pipeline.updated_at,
    })
    return {"status": "success", "pipeline_id": saved["id"], "name": saved["name"]}


@router.get("/pipeline/list")
async def pipeline_list(request: Request):
    """List the caller-tenant's saved pipelines."""
    pipelines = await list_pipelines(current_workspace_id(request))
    return {"status": "success", "count": len(pipelines), "pipelines": pipelines}


@router.get("/pipeline/{pipeline_id}")
async def pipeline_get(pipeline_id: str, request: Request):
    """Get one of the caller-tenant's saved pipelines by ID."""
    definition = await get_pipeline(pipeline_id, workspace_id=current_workspace_id(request))
    if not definition:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return {"status": "success", "pipeline": definition}


@router.delete("/pipeline/{pipeline_id}")
async def pipeline_delete(pipeline_id: str, request: Request):
    """Delete one of the caller-tenant's saved pipelines."""
    deleted = await delete_pipeline(pipeline_id, current_workspace_id(request))
    if not deleted:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return {"status": "success", "deleted": pipeline_id}
```
  Note: `/pipeline/{pipeline_id}` must stay declared AFTER `/pipeline/list`, `/pipeline/schema/...`, `/pipeline/download/...` (FastAPI matches in declaration order — the path-param route must not shadow the static ones; it already is last among GETs, keep it so).

- [ ] **Step 4: Run tests** — `pytest tests/test_durable_tenant_stores.py -q` (all pass). Ruff on `pipelines.py`.

- [ ] **Step 5: Commit**

```bash
git add aurabackend/api_gateway/routers/pipelines.py aurabackend/tests/test_durable_tenant_stores.py
git commit -m "feat(s50): tenant-scope + persist pipeline CRUD endpoints"
```

---

### Task 4: PipelineEngine execute-only + inbound_hooks fetch via store

**Files:**
- Modify: `aurabackend/pipeline/engine.py` (remove in-mem store + CRUD)
- Modify: `aurabackend/api_gateway/routers/inbound_hooks.py:167-173`

**Interfaces:**
- Consumes: `get_pipeline` (Task 1); `PipelineModel` (`pipeline.models`).

- [ ] **Step 1: Edit `engine.py`.** Remove `self._pipelines = {}` from `__init__` and delete the four CRUD methods (`save`, `get`, `list_all`, `delete`) — the engine keeps only `execute()` and its private helpers. (`__init__` may then be empty; keep it as `def __init__(self) -> None: pass` or remove it.)

- [ ] **Step 2: Edit `inbound_hooks.py`** `_fire_pipeline` (lines ~167-173):

```python
async def _fire_pipeline(pipeline_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from api_gateway.persistence import get_pipeline
    from pipeline.engine import PipelineEngine
    from pipeline.models import Pipeline as PipelineModel

    definition = await get_pipeline(pipeline_id)  # trusted internal path, unscoped
    if not definition:
        raise HTTPException(status_code=404, detail=f"Pipeline '{pipeline_id}' not found")
    pipeline: PipelineModel = PipelineModel(**definition)
    _engine = PipelineEngine()
    ...
```
  Replace the later `_pipeline_engine.execute(...)` call in this function (if any) with `_engine.execute(...)`. (Confirm the exact execute call site while editing; the engine is stateless now so a fresh instance is fine.)

- [ ] **Step 3: Run the broad backend slice** — from `aurabackend/`:
  `../.venv/Scripts/python.exe -m pytest tests/test_durable_tenant_stores.py tests/test_chat_pipeline.py -q`
  plus any `tests/test_pipeline*.py` and `tests/test_operability.py` (the grep showed `agent`/pipeline references) → all pass. Ruff on the two files.

- [ ] **Step 4: Commit**

```bash
git add aurabackend/pipeline/engine.py aurabackend/api_gateway/routers/inbound_hooks.py
git commit -m "feat(s50): PipelineEngine execute-only; inbound hooks fetch from durable store"
```

---

## Self-Review

**Spec coverage:** models + repo fns + caps (T1) ✓; chat rewire + close hole (T2) ✓; pipeline CRUD rewire (T3) ✓; engine execute-only + inbound_hooks (T4) ✓; tenant-isolation/durability/cap/unscoped-get tests (T1–T3) ✓. All spec sections mapped.

**Placeholder scan:** every code step has concrete code. Two "confirm against the model while implementing" notes (T3 minimal `pdef`, T4 execute call site) are implementation verifications, not placeholders — the surrounding code is complete; flagged because the exact `PipelineModel` required fields / the engine execute signature must be eyeballed in-file.

**Type consistency:** `insert_chat_message(workspace_id, record)` / `list_chat_messages(workspace_id, session_id)` / `save_pipeline(record)` / `get_pipeline(id, workspace_id=None)` / `delete_pipeline(id, workspace_id)` are used identically in T2–T4 as defined in T1. `_row_to_chat_dict` returns `{id,type,content,timestamp,metadata}` matching `ChatHistoryEntry`. `_row_to_pipeline_list_dict` returns the historic list shape (`source`/`steps`/`sink`). `get_pipeline` returns the full definition dict (consumed by both `pipeline_get` and `_fire_pipeline`).
```
