# S43 Part 1 — Durable Postgres Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AURA runs correctly and durably on managed Postgres with prod auth armed, proven locally in `docker-compose` against a Postgres container.

**Architecture:** Flip the three store DB URLs to `postgresql+asyncpg://…`; the DuckDB query engine stays in-memory (reads durable per-tenant CSVs). A Tier-B `create_all`-on-Postgres test deterministically catches SQLite-isms; a live smoke test proves the core flows + restart durability on Postgres.

**Tech Stack:** SQLAlchemy async (`asyncpg`, already a dep) · Postgres 16 (container) · pytest + `pytest.mark.asyncio` · Docker Compose. Run tests with `../.venv/Scripts/python.exe -m pytest` from `aurabackend/`.

## Global Constraints

- Postgres DSN form: `postgresql+asyncpg://USER:PASS@HOST:PORT/DB` (async stores).
- Tier-B gating: every Postgres-requiring test is `@pytest.mark.skipif(not os.getenv("AURA_PG_TEST_DSN"), reason="needs Postgres (set AURA_PG_TEST_DSN)")` — matches the repo's optional-dep pattern (CLAUDE.md). Default CI (no DSN) skips them; the prod-compose run sets the DSN.
- `git add` only the explicit files per task. NEVER `git add -A`/`.` — untracked scratch (`data/checkpoints/`, `scratch/`, `yc_*`) must never be staged.
- No real secrets committed. `.env.prod.example` carries variable NAMES + guidance only.
- Branch: `feature/s43-postgres-durable-foundation` (created, issue #106). Do not push from subagents; the controller pushes.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `aurabackend/api_gateway/routers/workspaces.py` | uploads-root resolution | **Add** `AURA_UPLOADS_ROOT` env override |
| `aurabackend/tests/test_postgres_schema.py` | prove every store's schema builds on Postgres | **Create** (Tier-B) |
| `docker-compose.prod.yml` | prod stack incl. Postgres + volume + armed env | **Modify** (add `db` service + env) |
| `aurabackend/.env.prod.example` | documented prod secrets/config manifest | **Create** |
| `aurabackend/tests/smoke_postgres.py` | live end-to-end smoke on the running stack | **Create** |

---

### Task 1: `AURA_UPLOADS_ROOT` env-configurable uploads root

**Files:**
- Modify: `aurabackend/api_gateway/routers/workspaces.py` (the `_UPLOADS_ROOT` constant, ~line 133)
- Test: `aurabackend/tests/test_tenant_upload_dir.py` (append)

**Interfaces:**
- Produces: `_UPLOADS_ROOT` now honors `os.getenv("AURA_UPLOADS_ROOT")`; `tenant_dir_name`/`tenant_upload_dir` signatures unchanged.

- [ ] **Step 1: Write the failing test** (append to the existing file):

```python
def test_uploads_root_honors_env(monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", "/srv/aura/uploads")
    import importlib
    from api_gateway.routers import workspaces
    importlib.reload(workspaces)
    assert workspaces._UPLOADS_ROOT == "/srv/aura/uploads"
    monkeypatch.delenv("AURA_UPLOADS_ROOT")
    importlib.reload(workspaces)  # restore default for other tests
```

- [ ] **Step 2: Run, confirm FAIL:** `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_tenant_upload_dir.py::test_uploads_root_honors_env -q`

- [ ] **Step 3: Implement** — replace the `_UPLOADS_ROOT = os.path.join(...)` constant with:

```python
_UPLOADS_ROOT = os.getenv("AURA_UPLOADS_ROOT") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "uploads",
)
```

- [ ] **Step 4: Run, confirm PASS** (and the rest of the file still passes): `cd aurabackend && ../.venv/Scripts/python.exe -m pytest tests/test_tenant_upload_dir.py -q`

- [ ] **Step 5: Lint + commit:**
```bash
cd aurabackend && ../.venv/Scripts/python.exe -m ruff check --select E,F,I,W --ignore E501,E402,F401,E701,E712 api_gateway/routers/workspaces.py tests/test_tenant_upload_dir.py
cd .. && git add aurabackend/api_gateway/routers/workspaces.py aurabackend/tests/test_tenant_upload_dir.py
git commit -m "feat(s43): AURA_UPLOADS_ROOT env override for the durable volume mount"
```

---

### Task 2: Tier-B `create_all`-on-Postgres test (the SQLite-ism catch-net) + fixes

**Files:**
- Create: `aurabackend/tests/test_postgres_schema.py`
- Modify (only as failures surface): the store models with SQLite-isms (see catalog)

**Interfaces:**
- Consumes: `metadata_store.db.Base`, `api_gateway.persistence.Base`, the scheduler `Base`, `uasr` `Base` (each a `DeclarativeBase` with `.metadata`).
- Produces: a green schema-build guarantee on Postgres (gated on `AURA_PG_TEST_DSN`).

- [ ] **Step 1: Write the test** (the keystone — builds every store's schema on a real Postgres):

```python
# aurabackend/tests/test_postgres_schema.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa: E402

import pytest  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.getenv("AURA_PG_TEST_DSN"),
    reason="needs Postgres (set AURA_PG_TEST_DSN=postgresql+asyncpg://...)",
)


async def _build_and_drop(base):
    from sqlalchemy.ext.asyncio import create_async_engine
    eng = create_async_engine(os.environ["AURA_PG_TEST_DSN"])
    try:
        async with eng.begin() as conn:
            await conn.run_sync(base.metadata.create_all)
            await conn.run_sync(base.metadata.drop_all)
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_metadata_store_schema_builds_on_postgres():
    from metadata_store.db import Base
    await _build_and_drop(Base)


@pytest.mark.asyncio
async def test_gateway_persistence_schema_builds_on_postgres():
    from api_gateway.persistence import Base
    await _build_and_drop(Base)
```

> Add equivalent `test_*_schema_builds_on_postgres` functions for the scheduler and uasr `Base` classes — confirm their import paths first (`grep -rn "class Base" scheduler_service uasr`) and import the real symbol; do not invent a name.

- [ ] **Step 2: Stand up a throwaway Postgres + run the test** (Docker required):
```bash
docker run -d --name aura-pg-test -e POSTGRES_PASSWORD=aura -e POSTGRES_DB=aura -p 55432:5432 postgres:16
cd aurabackend && AURA_PG_TEST_DSN="postgresql+asyncpg://postgres:aura@localhost:55432/aura" ../.venv/Scripts/python.exe -m pytest tests/test_postgres_schema.py -q
```
Expected first run: may FAIL on a SQLite-ism. **That failure is the point.**

- [ ] **Step 3: Fix each surfaced SQLite-ism.** Apply the matching fix, re-run, repeat until green. Catalog of the likely ones and their canonical fixes:
  - **`JSON` column that defaulted to TEXT on SQLite:** ensure the column uses `sqlalchemy.JSON` (portable) — on Postgres it maps to `JSONB`/`JSON`. If a model used `Text` to stash JSON strings, leave it (it works on both); only change real `JSON`-typed columns that fail.
  - **Vector/embedding columns** (metadata_store): if a column is a Postgres-only type (e.g. `pgvector`) the extension must exist; if it's stored as `JSON`/`ARRAY(Float)`, `ARRAY` is Postgres-native and fine. If create_all fails on a vector type, store it as `JSON`/`LargeBinary` for the pilot (no semantic-search regression for v1 — note it).
  - **`String` with no length** on a primary key/index: Postgres allows unbounded `VARCHAR`, so this usually passes; if an index complains, give the column an explicit length.
  - **SQLite-only `server_default`/`DateTime('now')` text:** replace with `sqlalchemy.func.now()` / `server_default=func.now()`.
  - Each fix is its own minimal edit to the offending model file; re-run Step 2's pytest after each.

- [ ] **Step 4: Tear down + commit** (commit the test plus any model fixes, listing the exact files):
```bash
docker rm -f aura-pg-test
cd .. && git add aurabackend/tests/test_postgres_schema.py <any model files you fixed>
git commit -m "test(s43): create_all builds every store schema on Postgres (Tier-B) + fix SQLite-isms"
```

---

### Task 3: Prod Postgres compose profile + secrets manifest

**Files:**
- Modify: `docker-compose.prod.yml`
- Create: `aurabackend/.env.prod.example`

**Interfaces:**
- Produces: `docker compose -f docker-compose.prod.yml up` boots a `db` (Postgres) + the services configured for Postgres, prod gates armed, uploads on a named volume.

- [ ] **Step 1: Inspect the existing prod compose** so additions follow its style:
`grep -nE "services:|image:|environment:|volumes:|DATABASE_URL|gateway|depends_on" docker-compose.prod.yml | head -40`

- [ ] **Step 2: Add a `db` service + wire the gateway/services env.** Add a Postgres service and, on every service that reads a DB, the Postgres URLs + armed gates + the uploads volume. Minimal shape (adapt service names to the file):
```yaml
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: ${DB_USER:-aura}
      POSTGRES_PASSWORD: ${DB_PASSWORD:?set in .env}
      POSTGRES_DB: ${DB_NAME:-aura}
    volumes:
      - aura-pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER:-aura}"]
      interval: 5s
      timeout: 3s
      retries: 10
# on the gateway (and any DB-touching service) environment:
      DATABASE_URL: postgresql+asyncpg://${DB_USER:-aura}:${DB_PASSWORD}@db:5432/${DB_NAME:-aura}
      METADATA_DATABASE_URL: postgresql+asyncpg://${DB_USER:-aura}:${DB_PASSWORD}@db:5432/${DB_NAME:-aura}
      SCHEDULER_DATABASE_URL: postgresql+asyncpg://${DB_USER:-aura}:${DB_PASSWORD}@db:5432/${DB_NAME:-aura}
      AURA_JWT_ENABLED: "true"
      AURA_AUTH_MODE: password
      AURA_UPLOADS_ROOT: /data/uploads
      AURA_SIGNING_PRIVATE_KEY_HEX: ${AURA_SIGNING_PRIVATE_KEY_HEX}
      AURA_PII_TOKEN_KEY: ${AURA_PII_TOKEN_KEY}
      GEMINI_API_KEY: ${GEMINI_API_KEY}
      GROQ_API_KEY: ${GROQ_API_KEY}
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - aura-uploads:/data/uploads
# bottom of file:
volumes:
  aura-pgdata:
  aura-uploads:
```

- [ ] **Step 3: Write `aurabackend/.env.prod.example`** (names + guidance only, NO real values):
```bash
# AURA production env (copy to .env, fill in, keep OUT of git)
DB_USER=aura
DB_PASSWORD=            # strong random; also used by the db service
DB_NAME=aura
# A STABLE ed25519 signing key (hex). Generate once, keep forever — a new key
# invalidates every previously-issued audit certificate.
#   python -c "import os;print(os.urandom(32).hex())"
AURA_SIGNING_PRIVATE_KEY_HEX=
AURA_PII_TOKEN_KEY=     # random; salts the deterministic PII tokenization
JWT_SECRET=             # random; signs session JWTs
GEMINI_API_KEY=
GROQ_API_KEY=
```
(If the gateway reads the JWT secret under a different env name, match it — `grep -rn "JWT_SECRET\|jwt_secret\|SECRET_KEY" aurabackend/shared/`.)

- [ ] **Step 4: Validate the compose file parses:** `docker compose -f docker-compose.prod.yml config >/dev/null && echo "compose OK"`

- [ ] **Step 5: Commit:**
```bash
git add docker-compose.prod.yml aurabackend/.env.prod.example
git commit -m "feat(s43): prod compose with Postgres + durable volumes + armed gates; .env.prod.example"
```

---

### Task 4: Live smoke test on Postgres (core flows + restart durability + isolation)

**Files:**
- Create: `aurabackend/tests/smoke_postgres.py` (a runnable script, not part of the default CI suite — it drives the LIVE stack over HTTP)

**Interfaces:**
- Consumes: the running prod compose (gateway on its published port).

- [ ] **Step 1: Write the smoke script** (`requests`-based; asserts each step):
```python
# aurabackend/tests/smoke_postgres.py — run against the LIVE prod compose:
#   docker compose -f docker-compose.prod.yml up -d
#   AURA_BASE=http://localhost:8000 python aurabackend/tests/smoke_postgres.py
import os, io, sys, requests

BASE = os.getenv("AURA_BASE", "http://localhost:8000")
V1 = f"{BASE}/api/v1"

def main():
    # 1. register + login (password mode, JWT issued)
    email = f"smoke_{os.urandom(4).hex()}@aura.test"
    requests.post(f"{V1}/auth/register", json={"name": "Smoke", "email": email, "password": "supersafe123"}, timeout=30).raise_for_status()
    tok = requests.post(f"{V1}/auth/token", data={"username": email, "password": "supersafe123"}, timeout=30).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    # 2. upload a CSV (lands in this tenant's dir on the volume; metadata in Postgres)
    csv = b"region,revenue\nNorth,100\nSouth,200\n"
    r = requests.post(f"{V1}/files/upload", files={"file": ("smoke.csv", io.BytesIO(csv), "text/csv")}, headers=h, timeout=60); r.raise_for_status()
    assert any(f["filename"] == "smoke.csv" for f in requests.get(f"{V1}/files", headers=h, timeout=30).json()["files"]), "upload not listed"
    # 3. chat -> SQL over that CSV returns rows
    chat = requests.post(f"{V1}/chat", json={"message": "total revenue by region", "session_id": "smoke"}, headers=h, timeout=120).json()
    assert chat.get("status") == "Success", f"chat failed: {chat.get('error_message')}"
    print("SMOKE OK: register/login + upload + chat all green on Postgres")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"SMOKE FAILED: {e}"); sys.exit(1)
```

- [ ] **Step 2: Run it against the live stack:**
```bash
docker compose -f docker-compose.prod.yml up -d --build
# wait for healthy, then:
AURA_BASE=http://localhost:8000 ../.venv/Scripts/python.exe aurabackend/tests/smoke_postgres.py
```
Expected: `SMOKE OK`. Fix any failure (likely a Postgres-ism the schema test missed, or an env-wiring gap) and re-run.

- [ ] **Step 3: Prove restart durability (the signing-key + Postgres durability check), manually documented in the commit message:** run an audit cert via the running stack, `docker compose -f docker-compose.prod.yml restart`, then re-verify the same cert hash — it must still validate (stable key + Postgres-persisted record). If it fails, the signing key isn't stable: set a fixed `AURA_SIGNING_PRIVATE_KEY_HEX` in `.env` and confirm `signing.py` reads it (`grep -n "SIGNING_PRIVATE_KEY_HEX\|getenv" aurabackend/shared/signing.py`).

- [ ] **Step 4: Commit:**
```bash
docker compose -f docker-compose.prod.yml down
git add aurabackend/tests/smoke_postgres.py
git commit -m "test(s43): live smoke (register/upload/chat) + restart-durable cert verify on Postgres"
```

---

## Done criteria
- `test_postgres_schema.py` green against Postgres (every store's schema builds).
- `smoke_postgres.py` prints `SMOKE OK` against the prod compose; an audit cert verifies across a stack restart.
- `AURA_UPLOADS_ROOT` redirects uploads; `.env.prod.example` documents every prod secret.
- Default CI unchanged (Postgres tests skip without `AURA_PG_TEST_DSN`).
- Open the PR referencing issue #106. **#2 (cloud deploy) and #3 (backups + runbook) are separate sprints.**

## Execution note
Tasks 2–4 require **Docker** (a Postgres container / the prod compose). If Docker isn't available in the execution environment, Task 1 (the env override, pure-Python TDD) still lands; Tasks 2–4 are written to run wherever Docker is — including the user's machine — and the test gating (`AURA_PG_TEST_DSN`) keeps CI green meanwhile.
