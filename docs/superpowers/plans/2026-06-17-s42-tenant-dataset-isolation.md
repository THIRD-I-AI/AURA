# S42 Part 1 — Tenant-Scoped Dataset Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** No request can list, fetch, delete, or query an uploaded dataset owned by another tenant — enforced by routing every upload-dir read/write through one tenant-scoped helper.

**Architecture:** Each tenant's uploads live in `data/uploads/<tenant>/`. A single `tenant_upload_dir(request)` helper (built on the existing `_request_tenant`) resolves that dir; the upload write, the `FileService` list/delete, and every `build_schema_context_cached` caller use it instead of the flat `data/uploads`. The schema cache key is already path-derived, so distinct tenant dirs give distinct cache entries for free. The directory path *is* the ownership for v1; the queryable ownership registry lands with Part #2.

**Tech Stack:** FastAPI gateway, SQLAlchemy/SQLite metadata, DuckDB (in-memory) query engine, pytest. Run tests with `../.venv/Scripts/python.exe -m pytest` from `aurabackend/` (the repo-root `.venv` has working pytest; `aurabackend/.venv` is a broken stub).

**Scope refinement vs. design (flag for reviewer):** the design's "resource layer / dataset ownership record" is deferred to Part #2. For v1, the per-tenant directory fully encodes ownership and delivers the isolation goal; an explicit `owner_tenant` metadata table earns its keep only once #2's grants need to query it. v1 = the physical isolation + migration.

**Branch:** `feature/s42-tenant-dataset-isolation` (already created, issue #104).

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `aurabackend/api_gateway/routers/workspaces.py` | Tenant resolution chokepoint | **Add** `tenant_dir_name()` + `tenant_upload_dir(request)` |
| `aurabackend/api_gateway/routers/files.py` | Upload write + list/get/delete endpoints | Use `tenant_upload_dir`; pass tenant subdir to FileService |
| `aurabackend/shared/file_service.py` | Flat-dir file ops | Thread `subdir` into `list_files` / `delete_file` |
| `aurabackend/api_gateway/routers/{chat,queries,dashboards,etl,pipelines}.py` | Query/schema readers | Pass `[tenant_upload_dir(request)]` to `build_schema_context_cached` |
| `aurabackend/api_gateway/main.py` | Startup pre-warm | Drop eager global scan; run idempotent migration |
| `aurabackend/shared/upload_migration.py` | Flat→`default/` migration | **Create** (idempotent) |
| `aurabackend/tests/test_tenant_upload_dir.py` | Helper unit tests | **Create** |
| `aurabackend/tests/test_tenant_isolation.py` | End-to-end isolation | **Create** |
| `aurabackend/tests/test_upload_migration.py` | Migration idempotency | **Create** |

---

### Task 1: `tenant_upload_dir` helper

**Files:**
- Modify: `aurabackend/api_gateway/routers/workspaces.py` (add after `current_workspace_id`, ~line 95)
- Test: `aurabackend/tests/test_tenant_upload_dir.py`

The existing `_request_tenant(request)` returns the org_id (or `sub`) from the verified JWT, or `None`. We reuse it; `None` → `"default"`.

- [ ] **Step 1: Write the failing tests**

```python
# aurabackend/tests/test_tenant_upload_dir.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api_gateway.routers.workspaces import tenant_dir_name, _tenant_upload_dir_for

def test_dir_name_slugs_safely():
    assert tenant_dir_name("org_ABC-123") == "org_ABC-123"
    # hostile org ids cannot traverse or contain separators
    assert "/" not in tenant_dir_name("../../etc")
    assert "\\" not in tenant_dir_name("..\\win")
    assert tenant_dir_name("a/b/c") == "abc" or ".." not in tenant_dir_name("a/b/c")
    assert tenant_dir_name("") == "default"
    assert tenant_dir_name(None) == "default"

def test_upload_dir_is_contained(tmp_path):
    root = str(tmp_path)
    d = _tenant_upload_dir_for(root, "org_1")
    assert os.path.commonpath((os.path.abspath(d), os.path.abspath(root))) == os.path.abspath(root)
    assert os.path.basename(d) == "org_1"
    # untenanted -> default bucket
    assert os.path.basename(_tenant_upload_dir_for(root, None)) == "default"
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_tenant_upload_dir.py -q`
Expected: FAIL — `ImportError: cannot import name 'tenant_dir_name'`.

- [ ] **Step 3: Implement the helpers**

Add to `workspaces.py` (imports `import os, re` at top if absent):

```python
_TENANT_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]")

def tenant_dir_name(tenant) -> str:
    """Filesystem-safe slug for a tenant id. Strips anything outside
    [A-Za-z0-9_-] so a hostile org_id cannot traverse; empty -> 'default'."""
    slug = _TENANT_SLUG_RE.sub("", str(tenant or "")).strip("-_")
    return slug or "default"

def _tenant_upload_dir_for(uploads_root: str, tenant) -> str:
    """Resolve <uploads_root>/<slug>, asserting containment. Pure (no request)
    so it is unit-testable; the request-bound wrapper is tenant_upload_dir()."""
    name = tenant_dir_name(tenant)
    path = os.path.join(uploads_root, name)
    if os.path.commonpath((os.path.abspath(path), os.path.abspath(uploads_root))) != os.path.abspath(uploads_root):
        # tenant_dir_name already prevents this; belt-and-suspenders.
        path = os.path.join(uploads_root, "default")
    os.makedirs(path, exist_ok=True)
    return path

_UPLOADS_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "uploads",
)

def tenant_upload_dir(request) -> str:
    """The caller's per-tenant upload dir (request-bound). Untenanted -> default."""
    return _tenant_upload_dir_for(_UPLOADS_ROOT, _request_tenant(request))
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_tenant_upload_dir.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint + commit**

```bash
cd aurabackend && ../.venv/Scripts/python.exe -m ruff check --select E,F,I,W --ignore E501,E402,F401,E701,E712 api_gateway/routers/workspaces.py tests/test_tenant_upload_dir.py
cd .. && git add aurabackend/api_gateway/routers/workspaces.py aurabackend/tests/test_tenant_upload_dir.py
git commit -m "feat(s42): tenant_upload_dir helper — per-tenant upload dir resolution"
```

---

### Task 2: Scope the upload write to the tenant dir

**Files:**
- Modify: `aurabackend/api_gateway/routers/files.py` — `upload_universal` (the `upload_dir = ...` line, ~109) + signature.

Currently the handler computes `upload_dir` as the flat dir and uses `_safe_upload_path(upload_dir, filename)` (Sec-8). We swap `upload_dir` for the tenant dir; the Sec-8 guard is unchanged.

- [ ] **Step 1: Add `request: Request` to the endpoint signature**

`upload_universal` has no `request` param. Add it (FastAPI injects it):

```python
from fastapi import Request  # add to existing fastapi import line
from .workspaces import tenant_upload_dir  # add near other router imports

@router.post("/upload")
async def upload_universal(
    request: Request,
    file: UploadFile = File(None),
    upload_file: UploadFile = File(None),
    x_upload_id: Optional[str] = Header(None, alias="X-Upload-Id"),
):
```

- [ ] **Step 2: Swap the upload dir**

Replace the flat-dir line (`upload_dir = os.path.join(... "data","uploads")`) with:

```python
    upload_dir = tenant_upload_dir(request)  # per-tenant; creates the dir
    file_path = _safe_upload_path(upload_dir, target_file.filename)
    if file_path is None:
        raise HTTPException(status_code=400, detail="Invalid filename")
    safe_name = os.path.basename(file_path)
```

(Delete the now-redundant `os.makedirs(upload_dir, ...)` — `tenant_upload_dir` makes it.)

- [ ] **Step 3: Verify the module imports**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -c "from api_gateway.routers.files import router; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add aurabackend/api_gateway/routers/files.py
git commit -m "feat(s42): uploads write to the caller's per-tenant dir"
```

(End-to-end isolation is asserted in Task 6 once list/query are also scoped.)

---

### Task 3: Thread tenant subdir into FileService list/delete

**Files:**
- Modify: `aurabackend/shared/file_service.py` — `list_files`, `delete_file`
- Modify: `aurabackend/api_gateway/routers/files.py` — the `/files` (list) and `DELETE /files/{id}` endpoints
- Test: `aurabackend/tests/test_tenant_isolation.py` (first cases)

- [ ] **Step 1: Write the failing test**

```python
# aurabackend/tests/test_tenant_isolation.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.file_service import FileService

def test_list_files_is_scoped_to_subdir(tmp_path, monkeypatch):
    fs = FileService()
    monkeypatch.setattr(fs, "uploads_path", tmp_path)
    (tmp_path / "orgA").mkdir(); (tmp_path / "orgB").mkdir()
    (tmp_path / "orgA" / "a.csv").write_text("x")
    (tmp_path / "orgB" / "b.csv").write_text("y")
    names_a = {f["filename"] for f in fs.list_files(subdir="orgA")}
    assert names_a == {"a.csv"}
    assert "b.csv" not in names_a
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_tenant_isolation.py::test_list_files_is_scoped_to_subdir -q`
Expected: FAIL — `list_files() got an unexpected keyword argument 'subdir'`.

- [ ] **Step 3: Add `subdir` to `list_files` and `delete_file`**

```python
    def list_files(self, subdir: str = "") -> List[Dict[str, Any]]:
        """List uploaded files within an optional per-tenant subdir."""
        files = []
        base = self.uploads_path / subdir if subdir else self.uploads_path
        if not base.exists():
            return files
        for file_path in base.glob("*"):
            if file_path.is_file():
                stat = file_path.stat()
                files.append({
                    'filename': file_path.name,
                    'size': stat.st_size,
                    'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })
        return files

    def delete_file(self, file_id: str, subdir: str = "") -> bool:
        try:
            base = self.uploads_path / subdir if subdir else self.uploads_path
            for file_path in base.glob(f"{file_id}.*"):
                file_path.unlink()
            for file_path in self.processed_path.glob(f"{file_id}_processed.*"):
                file_path.unlink()
            return True
        except Exception:
            return False
```

- [ ] **Step 4: Pass the tenant subdir from the endpoints**

In `files.py`, add `request: Request` to `list_files` and `delete_file` endpoints and pass the tenant slug:

```python
from .workspaces import tenant_dir_name, _request_tenant  # add to imports

@router.get("/files")
async def list_files(request: Request) -> Dict[str, Any]:
    if file_service is None:
        return {"status": "error", "error": "File service not available"}
    try:
        sub = tenant_dir_name(_request_tenant(request))
        return {"status": "success", "files": file_service.list_files(subdir=sub)}
    except Exception as e:
        return {"status": "error", "error": sanitize_error(e, logger=logger, context="list files")}

@router.delete("/files/{file_id}")
async def delete_file(file_id: str, request: Request) -> Dict[str, Any]:
    if file_service is None:
        return {"status": "error", "error": "File service not available"}
    try:
        sub = tenant_dir_name(_request_tenant(request))
        success = file_service.delete_file(file_id, subdir=sub)
        if success:
            await invalidate_schema_cache()
            return {"status": "success", "message": "File deleted successfully"}
        raise HTTPException(status_code=404, detail="File not found or deletion failed")
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": sanitize_error(e, logger=logger, context="delete file")}
```

- [ ] **Step 5: Run the test + import check**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_tenant_isolation.py -q && ../.venv/Scripts/python.exe -c "from api_gateway.routers.files import router; print('ok')"`
Expected: PASS + `ok`.

- [ ] **Step 6: Commit**

```bash
git add aurabackend/shared/file_service.py aurabackend/api_gateway/routers/files.py aurabackend/tests/test_tenant_isolation.py
git commit -m "feat(s42): scope file list/delete to the caller's tenant subdir"
```

---

### Task 4: Scope the query/schema readers to the tenant dir

**Files (each builds `upload_dirs` then calls `build_schema_context_cached`):**
- `aurabackend/api_gateway/routers/chat.py:159-167`
- `aurabackend/api_gateway/routers/queries.py:305-317` and `:749-752`
- `aurabackend/api_gateway/routers/dashboards.py:186-195`
- `aurabackend/api_gateway/routers/etl.py:262, 314, 437`
- `aurabackend/api_gateway/routers/pipelines.py:103`

**The single uniform transformation** at every site: these endpoints already receive `request` (they are FastAPI routes). Replace the hardcoded list

```python
    base = pathlib.Path(__file__).resolve().parent.parent.parent
    upload_dirs = [base / "data" / "uploads", ...]
```

with

```python
    from .workspaces import tenant_upload_dir
    upload_dirs = [pathlib.Path(tenant_upload_dir(request))]
```

No change to `build_schema_context_cached` — its cache key is a fingerprint of each file's `(path, mtime, size)` (`_signature_for_upload_dirs`), and per-tenant paths differ, so cache entries are already tenant-distinct.

- [ ] **Step 1: Write the failing isolation test**

```python
# append to aurabackend/tests/test_tenant_isolation.py
import pathlib
from shared.data_utils import build_schema_context_cached
import duckdb, asyncio

def test_schema_context_is_tenant_scoped(tmp_path):
    (tmp_path / "orgA").mkdir(); (tmp_path / "orgB").mkdir()
    (tmp_path / "orgA" / "sales.csv").write_text("id,amt\n1,10\n")
    (tmp_path / "orgB" / "secret.csv").write_text("id,ssn\n1,999\n")
    async def run(d):
        con = duckdb.connect(":memory:")
        return await build_schema_context_cached(con, [pathlib.Path(d)], use_llm=False)
    a = asyncio.run(run(tmp_path / "orgA"))
    assert "sales" in a["tables"]; assert "secret" not in a["tables"]
```

- [ ] **Step 2: Run — expect PASS already** (this test validates the mechanism `build_schema_context_cached` provides; it should pass without code change, proving per-dir scoping works):

Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_tenant_isolation.py::test_schema_context_is_tenant_scoped -q`
Expected: PASS. (If FAIL, the scoping assumption is wrong — stop and re-examine `_signature_for_upload_dirs` before editing callers.)

- [ ] **Step 3: Edit each caller** (apply the uniform transformation above to all 5 files / 7 sites). Ensure each endpoint function has a `request` parameter (chat/queries/dashboards/etl/pipelines routes do; if a helper is called without `request`, thread it down from the route).

- [ ] **Step 4: Import-check every touched module**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -c "import api_gateway.routers.chat, api_gateway.routers.queries, api_gateway.routers.dashboards, api_gateway.routers.etl, api_gateway.routers.pipelines; print('ok')"`
Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
git add aurabackend/api_gateway/routers/chat.py aurabackend/api_gateway/routers/queries.py aurabackend/api_gateway/routers/dashboards.py aurabackend/api_gateway/routers/etl.py aurabackend/api_gateway/routers/pipelines.py aurabackend/tests/test_tenant_isolation.py
git commit -m "feat(s42): scope query/schema readers to the caller's tenant dir"
```

---

### Task 5: Migration of existing flat files + lazy startup

**Files:**
- Create: `aurabackend/shared/upload_migration.py`
- Modify: `aurabackend/api_gateway/main.py` (startup: call migration; drop the eager global pre-warm of `refresh_stale_file_metadata`/`compute_schema_fingerprint` over the flat dir)
- Test: `aurabackend/tests/test_upload_migration.py`

- [ ] **Step 1: Write the failing test**

```python
# aurabackend/tests/test_upload_migration.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.upload_migration import migrate_flat_uploads_to_default

def test_moves_flat_files_into_default_idempotently(tmp_path):
    (tmp_path / "customer.csv").write_text("a")
    (tmp_path / ".gitkeep").write_text("")     # dotfiles ignored
    (tmp_path / "orgX").mkdir()                  # existing tenant dirs untouched
    (tmp_path / "orgX" / "keep.csv").write_text("b")
    moved = migrate_flat_uploads_to_default(str(tmp_path))
    assert moved == 1
    assert (tmp_path / "default" / "customer.csv").exists()
    assert not (tmp_path / "customer.csv").exists()
    assert (tmp_path / "orgX" / "keep.csv").exists()
    # idempotent: re-run moves nothing, raises nothing
    assert migrate_flat_uploads_to_default(str(tmp_path)) == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_upload_migration.py -q`
Expected: FAIL — `ModuleNotFoundError: shared.upload_migration`.

- [ ] **Step 3: Implement the migration**

```python
# aurabackend/shared/upload_migration.py
"""One-time, idempotent migration: files sitting directly in data/uploads/
(pre-S42) belong to the 'default' tenant. Move them into uploads/default/ so
the per-tenant readers (S42) still see them. Subdirs and dotfiles untouched."""
from __future__ import annotations
import logging, os, shutil

logger = logging.getLogger("aura.upload_migration")

def migrate_flat_uploads_to_default(uploads_root: str) -> int:
    if not os.path.isdir(uploads_root):
        return 0
    default_dir = os.path.join(uploads_root, "default")
    os.makedirs(default_dir, exist_ok=True)
    moved = 0
    for name in os.listdir(uploads_root):
        if name.startswith("."):            # .gitkeep, .aura_header_cache
            continue
        src = os.path.join(uploads_root, name)
        if not os.path.isfile(src):          # skip tenant subdirs
            continue
        dst = os.path.join(default_dir, name)
        if os.path.exists(dst):
            continue
        shutil.move(src, dst)
        moved += 1
    if moved:
        logger.info("Migrated %d flat upload(s) into default/", moved)
    return moved
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_upload_migration.py -q`
Expected: PASS (1 test, both assertions incl. idempotent re-run).

- [ ] **Step 5: Wire into startup + drop the eager global pre-warm**

In `main.py` startup (around the `upload_dir = base / "data" / "uploads"` block, ~lines 48-62): call `migrate_flat_uploads_to_default(str(upload_dir))` and **remove** the eager `refresh_stale_file_metadata(...)` / `compute_schema_fingerprint([...])` / `refresh_schema_context([...])` calls over the flat dir (schema context now builds lazily per-tenant on first query, Task 4).

```python
    from shared.upload_migration import migrate_flat_uploads_to_default
    upload_dir = base / "data" / "uploads"
    try:
        migrate_flat_uploads_to_default(str(upload_dir))
    except Exception as exc:
        logger.warning("upload migration skipped (non-fatal): %s", exc)
    # (eager global schema pre-warm removed — built lazily per tenant)
```

- [ ] **Step 6: Import-check + commit**

```bash
cd aurabackend && ../.venv/Scripts/python.exe -c "import api_gateway.main; print('ok')"
cd .. && git add aurabackend/shared/upload_migration.py aurabackend/api_gateway/main.py aurabackend/tests/test_upload_migration.py
git commit -m "feat(s42): migrate flat uploads to default/ + drop eager global pre-warm"
```

---

### Task 6: End-to-end isolation + fail-closed

**Files:**
- Test: `aurabackend/tests/test_tenant_isolation.py` (add e2e cases via the gateway app with mocked JWT principals)

- [ ] **Step 1: Write the per-principal test** (a mocked request carrying `state.user` is enough — no full TestClient/JWT harness needed, since `_request_tenant` just reads `request.state.user`):

```python
# append to aurabackend/tests/test_tenant_isolation.py
import types
def _req(org):
    return types.SimpleNamespace(
        state=types.SimpleNamespace(user={"org_id": org} if org else None))

def test_tenant_upload_dir_is_per_principal(tmp_path, monkeypatch):
    from api_gateway.routers import workspaces
    monkeypatch.setattr(workspaces, "_UPLOADS_ROOT", str(tmp_path))
    a = workspaces.tenant_upload_dir(_req("orgA"))
    b = workspaces.tenant_upload_dir(_req("orgB"))
    assert os.path.basename(a) == "orgA" and os.path.basename(b) == "orgB" and a != b
    # untenanted (dev / no JWT) -> shared default bucket
    assert os.path.basename(workspaces.tenant_upload_dir(_req(None))) == "default"
    # hostile org id cannot escape the uploads root
    h = workspaces.tenant_upload_dir(_req("../../keys"))
    assert os.path.commonpath((os.path.abspath(h), str(tmp_path))) == str(tmp_path)
```

> **Fail-closed note:** the `None → default` fallback is only reachable when JWT is *disabled* (dev). With `AURA_JWT_ENABLED`, `JWTAuthMiddleware` 401s any request lacking a verified principal, so a request reaching a file endpoint always carries a tenant — `_request_tenant` is non-`None` and the `default` bucket is never used under auth. Fail-closed is provided by that existing invariant, not new code; this test documents both branches.

- [ ] **Step 2: Run the full S42 test set**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_tenant_upload_dir.py tests/test_tenant_isolation.py tests/test_upload_migration.py tests/test_upload_path_safety.py -q`
Expected: all PASS.

- [ ] **Step 3: Regression — touched routers still import + their tests pass**

Run: `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_chat_pipeline.py tests/test_api_gateway_persistence.py -q`
Expected: PASS (or pre-existing skips only).

- [ ] **Step 4: Ruff + commit**

```bash
cd aurabackend && ../.venv/Scripts/python.exe -m ruff check --select E,F,I,W --ignore E501,E402,F401,E701,E712 shared/file_service.py shared/upload_migration.py api_gateway/routers/files.py api_gateway/routers/workspaces.py tests/test_tenant_isolation.py
cd .. && git add aurabackend/tests/test_tenant_isolation.py
git commit -m "test(s42): end-to-end tenant isolation across upload + list + query"
```

---

## Done criteria

- A file uploaded by tenant A is not listed, fetched, deleted, or queryable by tenant B.
- Dev / no-JWT keeps working via the `default` bucket; the existing demo files are migrated there and still visible.
- A hostile `org_id` can't escape the uploads root.
- `npm`-side: none (backend only). CI backend lanes green.
- Open the PR: `gh pr create --base main --head feature/s42-tenant-dataset-isolation` referencing issue #104.
