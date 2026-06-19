# S45 Object-Storage Uploads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store uploaded datasets in S3-compatible object storage (S3/R2/MinIO) behind a `StorageBackend` abstraction, so the api_gateway becomes a stateless pod that scales across nodes — with a backward-compatible local-filesystem mode for dev.

**Architecture:** One `StorageBackend` interface, two implementations — `LocalBackend` (today's `<uploads_root>/<tenant>/` behavior) and `S3Backend` — selected by `AURA_STORAGE_BACKEND`. DuckDB reads `s3://` URIs directly via the httpfs extension (Approach A, zero local disk); a new connection factory configures the S3 secret on every DuckDB connection. The three upload chokepoints (write, FileService, schema/query reader) go through the backend.

**Tech Stack:** Python, FastAPI, DuckDB (httpfs/s3), boto3, moto (test), pydantic-settings.

## Global Constraints

- Scope: **one app-managed bucket, tenant-prefixed keys** `<bucket>/<prefix>/<tenant>/<file>`. Per-tenant buckets are out of scope.
- Default `AURA_STORAGE_BACKEND=local` → dev/single-host behavior is **byte-for-byte unchanged**; all existing tests stay green.
- Fail closed: `storage_backend=s3` with no `AURA_S3_BUCKET` → boot error. No silent fallback to local under s3 mode.
- Tenant key isolation (S42 property) must hold on S3: a tenant's `list` never sees another tenant's keys.
- Sec-8 filename sanitization still gates the filename component of every key.
- Lint: `ruff check --select E,F,I,W --ignore E501,E402,F401,E701,E712`. Tests via `../.venv/Scripts/python.exe -m pytest` from `aurabackend/`.
- Tier-A tests (moto, no network) run in base CI. The real DuckDB-reads-`s3://` test is Tier-B, gated `skipif(not os.getenv("AURA_S3_TEST_ENDPOINT"))`, with a dedicated MinIO CI lane.
- Co-author every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Conventional Commits. Branch `feature/s45-object-storage-uploads` (already checked out). Subagents do NOT push.

## File Structure

- Create `aurabackend/shared/storage/__init__.py` — `get_storage_backend()` singleton + selection.
- Create `aurabackend/shared/storage/base.py` — `StorageBackend` ABC + `ObjectInfo`.
- Create `aurabackend/shared/storage/local.py` — `LocalBackend`.
- Create `aurabackend/shared/storage/s3.py` — `S3Backend` (boto3 + DuckDB secret).
- Create `aurabackend/shared/duckdb_factory.py` — `new_connection()`.
- Modify `aurabackend/shared/config.py` — `AURA_STORAGE_BACKEND` + `AURA_S3_*` fields + validator.
- Modify `aurabackend/shared/data_utils.py` — reader over the backend.
- Modify `aurabackend/api_gateway/routers/files.py` — upload write via backend.
- Modify `aurabackend/shared/file_service.py` — list/get/delete via backend.
- Modify `aurabackend/requirements.txt`, `aurabackend/pyproject.toml` — `boto3`, `moto`.
- Modify `aurabackend/Dockerfile` — pre-install DuckDB httpfs.
- Modify `docker-compose.prod.yml`, `aurabackend/.env.prod.example`, `deploy/helm/aura/values.yaml`, `deploy/helm/aura/README.md`, `docs/DEPLOYMENT.md` — S3 wiring.
- Modify `.github/workflows/ci.yml` — MinIO Tier-B lane.
- Create `aurabackend/tests/test_storage_backend.py` (Tier A), `aurabackend/tests/test_storage_s3_duckdb.py` (Tier B).

---

## Task 1: Dependencies + config + fail-closed validator

**Files:**
- Modify: `aurabackend/requirements.txt`, `aurabackend/pyproject.toml`
- Modify: `aurabackend/shared/config.py` (Security/Auth section, ~line 120-167)
- Test: `aurabackend/tests/test_storage_config.py` (create)

**Interfaces:**
- Produces: `Settings` fields `storage_backend: str`, `s3_bucket: Optional[str]`, `s3_endpoint_url: Optional[str]`, `s3_region: str`, `s3_access_key_id: Optional[str]`, `s3_secret_access_key: Optional[str]`, `s3_url_style: str`, `s3_prefix: str`, `s3_use_ssl: bool`. A validator raises `ValueError` when `storage_backend=="s3"` and `s3_bucket` is falsy.

- [ ] **Step 1: Add deps**

In `aurabackend/requirements.txt` add (anywhere in the alpha-sorted block):
```
boto3>=1.34,<2.0
```
In `aurabackend/pyproject.toml` add `boto3>=1.34,<2.0` to the runtime dependencies array and `moto>=5.0,<6.0` to the test/dev dependencies array (match the file's existing array style). Add `moto>=5.0,<6.0` to `requirements.txt` as well (the repo installs requirements.txt in CI).

- [ ] **Step 2: Write the failing config test**

Create `aurabackend/tests/test_storage_config.py`:
```python
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.config import Settings


def test_default_backend_is_local():
    s = Settings()
    assert s.storage_backend == "local"


def test_s3_backend_requires_bucket(monkeypatch):
    monkeypatch.setenv("AURA_STORAGE_BACKEND", "s3")
    monkeypatch.delenv("AURA_S3_BUCKET", raising=False)
    with pytest.raises(ValueError, match="AURA_S3_BUCKET"):
        Settings()


def test_s3_backend_with_bucket_ok(monkeypatch):
    monkeypatch.setenv("AURA_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AURA_S3_BUCKET", "aura-uploads")
    s = Settings()
    assert s.s3_bucket == "aura-uploads"
    assert s.s3_url_style == "path"
```

- [ ] **Step 3: Run it to verify it fails**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_storage_config.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'storage_backend'`.

- [ ] **Step 4: Add the config fields + validator**

In `aurabackend/shared/config.py`, after the `jwt_enabled` field (~line 166), add:
```python
    # ── Object storage (S45) ────────────────────────────────────────────
    storage_backend: str = Field("local", alias="AURA_STORAGE_BACKEND")
    s3_bucket: Optional[str] = Field(None, alias="AURA_S3_BUCKET")
    s3_endpoint_url: Optional[str] = Field(None, alias="AURA_S3_ENDPOINT_URL")
    s3_region: str = Field("us-east-1", alias="AURA_S3_REGION")
    s3_access_key_id: Optional[str] = Field(
        None, validation_alias=AliasChoices("AURA_S3_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID")
    )
    s3_secret_access_key: Optional[str] = Field(
        None, validation_alias=AliasChoices("AURA_S3_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY")
    )
    # canonical: "path" (MinIO/most on-prem) | "vhost" (AWS). S3Backend maps
    # to boto3 addressing_style (vhost->virtual) and passes verbatim to DuckDB.
    s3_url_style: str = Field("path", alias="AURA_S3_URL_STYLE")
    s3_prefix: str = Field("uploads", alias="AURA_S3_PREFIX")
    s3_use_ssl: bool = Field(True, alias="AURA_S3_USE_SSL")

    @field_validator("storage_backend", mode="after")
    @classmethod
    def _validate_storage_backend(cls, v, info):
        if v == "s3" and not info.data.get("s3_bucket"):
            raise ValueError(
                "AURA_S3_BUCKET must be set when AURA_STORAGE_BACKEND=s3."
            )
        return v
```
At the top of `config.py`, ensure `AliasChoices` is imported: `from pydantic import AliasChoices, Field, field_validator` (extend the existing pydantic import line; keep existing names).

NOTE on validator ordering: pydantic runs field validators in field-declaration order, and `info.data` only contains already-validated fields. Declare `s3_bucket` **before** `storage_backend`, OR validate on `s3_bucket`'s presence inside a `model_validator(mode="after")` instead. Use this `model_validator` form to avoid ordering fragility:
```python
    @model_validator(mode="after")
    def _require_bucket_for_s3(self):
        if self.storage_backend == "s3" and not self.s3_bucket:
            raise ValueError("AURA_S3_BUCKET must be set when AURA_STORAGE_BACKEND=s3.")
        return self
```
Use the `model_validator` version (delete the `field_validator` one above) and import `model_validator` from pydantic. Keep the field declarations.

- [ ] **Step 5: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_storage_config.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Lint + commit**

```bash
../.venv/Scripts/python.exe -m ruff check shared/config.py tests/test_storage_config.py --select E,F,I,W --ignore E501,E402,F401,E701,E712
git add aurabackend/requirements.txt aurabackend/pyproject.toml aurabackend/shared/config.py aurabackend/tests/test_storage_config.py
git commit -m "feat(storage): add AURA_STORAGE_BACKEND + AURA_S3_* config, fail-closed on s3 without bucket (S45)"
```

---

## Task 2: StorageBackend interface + LocalBackend + selection

**Files:**
- Create: `aurabackend/shared/storage/base.py`, `aurabackend/shared/storage/local.py`, `aurabackend/shared/storage/__init__.py`
- Test: `aurabackend/tests/test_storage_backend.py` (create; Tier A)

**Interfaces:**
- Produces: `ObjectInfo(name: str, size: int, fingerprint: str, duckdb_uri: str)`.
- Produces: `StorageBackend` ABC with `write(tenant, filename, data: bytes) -> ObjectInfo`, `read(tenant, filename) -> bytes`, `list(tenant) -> list[ObjectInfo]`, `delete(tenant, filename) -> bool`, `exists(tenant, filename) -> bool`, `duckdb_uri(tenant, filename) -> str`, `configure_duckdb(con) -> None`.
- Produces: `get_storage_backend() -> StorageBackend` (cached singleton; `AURA_STORAGE_BACKEND` selects; unknown → `ValueError`), `reset_storage_backend()` (test helper).
- Consumes: the S42 `tenant_dir_name` slug from `api_gateway/routers/workspaces.py` (importable helper) for the local path and the key prefix.

- [ ] **Step 1: Write the failing LocalBackend + selection tests**

Create `aurabackend/tests/test_storage_backend.py`:
```python
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.storage import get_storage_backend, reset_storage_backend
from shared.storage.base import ObjectInfo
from shared.storage.local import LocalBackend


@pytest.fixture(autouse=True)
def _reset():
    reset_storage_backend()
    yield
    reset_storage_backend()


def _local(tmp_path):
    return LocalBackend(root=str(tmp_path))


def test_local_write_then_read(tmp_path):
    b = _local(tmp_path)
    info = b.write("acme", "sales.csv", b"region,revenue\nN,1\n")
    assert isinstance(info, ObjectInfo)
    assert info.name == "sales.csv"
    assert b.read("acme", "sales.csv") == b"region,revenue\nN,1\n"


def test_local_list_is_tenant_scoped(tmp_path):
    b = _local(tmp_path)
    b.write("acme", "a.csv", b"x\n1\n")
    b.write("globex", "b.csv", b"y\n2\n")
    acme = {o.name for o in b.list("acme")}
    assert acme == {"a.csv"}
    assert b.exists("acme", "a.csv") is True
    assert b.exists("acme", "b.csv") is False


def test_local_delete(tmp_path):
    b = _local(tmp_path)
    b.write("acme", "a.csv", b"x\n1\n")
    assert b.delete("acme", "a.csv") is True
    assert b.list("acme") == []


def test_local_duckdb_uri_is_local_path(tmp_path):
    b = _local(tmp_path)
    b.write("acme", "a.csv", b"x\n1\n")
    uri = b.duckdb_uri("acme", "a.csv")
    assert uri.endswith("a.csv")
    assert "s3://" not in uri


def test_local_configure_duckdb_is_noop(tmp_path):
    import duckdb
    con = duckdb.connect(":memory:")
    _local(tmp_path).configure_duckdb(con)  # must not raise
    assert con.execute("SELECT 1").fetchone() == (1,)


def test_get_storage_backend_default_local(monkeypatch):
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    assert isinstance(get_storage_backend(), LocalBackend)


def test_get_storage_backend_unknown_raises(monkeypatch):
    monkeypatch.setenv("AURA_STORAGE_BACKEND", "nope")
    with pytest.raises(ValueError, match="Unknown storage backend"):
        get_storage_backend()
```

- [ ] **Step 2: Run to verify it fails**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_storage_backend.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.storage'`.

- [ ] **Step 3: Implement base.py**

Create `aurabackend/shared/storage/base.py`:
```python
"""Storage backend abstraction for uploaded datasets (S45)."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List


@dataclass(frozen=True)
class ObjectInfo:
    name: str          # the filename within the tenant (no prefix/dir)
    size: int          # bytes
    fingerprint: str   # stable change token: mtime|size (local) or etag|size (s3)
    duckdb_uri: str    # what read_csv_auto() consumes: local path or s3://...


def tenant_slug(tenant: str) -> str:
    """Filesystem/key-safe tenant component (mirrors S42 tenant_dir_name)."""
    slug = re.sub(r"[^A-Za-z0-9_-]", "_", tenant or "default")
    return slug or "default"


class StorageBackend(ABC):
    @abstractmethod
    def write(self, tenant: str, filename: str, data: bytes) -> ObjectInfo: ...

    @abstractmethod
    def read(self, tenant: str, filename: str) -> bytes: ...

    @abstractmethod
    def list(self, tenant: str) -> List[ObjectInfo]: ...

    @abstractmethod
    def delete(self, tenant: str, filename: str) -> bool: ...

    @abstractmethod
    def exists(self, tenant: str, filename: str) -> bool: ...

    @abstractmethod
    def duckdb_uri(self, tenant: str, filename: str) -> str: ...

    def configure_duckdb(self, con: Any) -> None:
        """Prepare a DuckDB connection to read this backend's duckdb_uri()s.

        Default: no-op (local paths need no setup). S3Backend overrides.
        """
        return None
```

- [ ] **Step 4: Implement local.py**

Create `aurabackend/shared/storage/local.py`:
```python
"""Local-filesystem storage backend — today's behavior behind the interface."""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

from shared.storage.base import ObjectInfo, StorageBackend, tenant_slug

_READ_EXTS = (".csv", ".parquet", ".json")


def _default_root() -> str:
    # Same precedence as api_gateway/routers/workspaces.py _UPLOADS_ROOT.
    return os.getenv("AURA_UPLOADS_ROOT") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "uploads",
    )


class LocalBackend(StorageBackend):
    def __init__(self, root: str | None = None) -> None:
        self._root = Path(root or _default_root())

    def _dir(self, tenant: str) -> Path:
        d = self._root / tenant_slug(tenant)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _path(self, tenant: str, filename: str) -> Path:
        return self._dir(tenant) / filename

    def write(self, tenant: str, filename: str, data: bytes) -> ObjectInfo:
        p = self._path(tenant, filename)
        p.write_bytes(data)
        return self._info(p)

    def read(self, tenant: str, filename: str) -> bytes:
        return self._path(tenant, filename).read_bytes()

    def list(self, tenant: str) -> List[ObjectInfo]:
        d = self._root / tenant_slug(tenant)
        if not d.exists():
            return []
        return [self._info(p) for p in sorted(d.iterdir())
                if p.suffix.lower() in _READ_EXTS]

    def delete(self, tenant: str, filename: str) -> bool:
        p = self._path(tenant, filename)
        if p.exists():
            p.unlink()
            return True
        return False

    def exists(self, tenant: str, filename: str) -> bool:
        return self._path(tenant, filename).exists()

    def duckdb_uri(self, tenant: str, filename: str) -> str:
        return str(self._path(tenant, filename)).replace("\\", "/")

    def _info(self, p: Path) -> ObjectInfo:
        st = p.stat()
        return ObjectInfo(
            name=p.name,
            size=st.st_size,
            fingerprint=f"{st.st_mtime_ns}|{st.st_size}",
            duckdb_uri=str(p).replace("\\", "/"),
        )
```

- [ ] **Step 5: Implement __init__.py (selection)**

Create `aurabackend/shared/storage/__init__.py`:
```python
"""Storage backend factory (S45). Selected by AURA_STORAGE_BACKEND."""
from __future__ import annotations

import os
from typing import Optional

from shared.storage.base import ObjectInfo, StorageBackend
from shared.storage.local import LocalBackend

_cached: Optional[StorageBackend] = None
_cached_kind: Optional[str] = None


def get_storage_backend() -> StorageBackend:
    global _cached, _cached_kind
    kind = os.getenv("AURA_STORAGE_BACKEND", "local").lower()
    if _cached is not None and _cached_kind == kind:
        return _cached
    if kind == "local":
        backend: StorageBackend = LocalBackend()
    elif kind == "s3":
        from shared.storage.s3 import S3Backend  # lazy: boto3 only when needed
        backend = S3Backend()
    else:
        raise ValueError(f"Unknown storage backend: {kind!r}. Use 'local' or 's3'.")
    _cached, _cached_kind = backend, kind
    return backend


def reset_storage_backend() -> None:
    global _cached, _cached_kind
    _cached = None
    _cached_kind = None


__all__ = ["ObjectInfo", "StorageBackend", "get_storage_backend", "reset_storage_backend"]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_storage_backend.py -q`
Expected: PASS (7 passed). The unknown-backend test passes because `__init__` raises before importing s3.

- [ ] **Step 7: Lint + commit**

```bash
../.venv/Scripts/python.exe -m ruff check shared/storage tests/test_storage_backend.py --select E,F,I,W --ignore E501,E402,F401,E701,E712
git add aurabackend/shared/storage aurabackend/tests/test_storage_backend.py
git commit -m "feat(storage): StorageBackend interface + LocalBackend + selection (S45)"
```

---

## Task 3: S3Backend (boto3 + DuckDB secret), moto unit tests

**Files:**
- Create: `aurabackend/shared/storage/s3.py`
- Modify: `aurabackend/tests/test_storage_backend.py` (add an S3 moto class)

**Interfaces:**
- Consumes: `StorageBackend`, `ObjectInfo`, `tenant_slug` from base; `get_settings()` from `shared.config`.
- Produces: `S3Backend` implementing all abstract methods; key layout `<prefix>/<tenant_slug>/<filename>`; `duckdb_uri` = `s3://<bucket>/<prefix>/<tenant>/<file>`; `fingerprint` = `"{etag}|{size}"`; `configure_duckdb(con)` runs `INSTALL httpfs; LOAD httpfs; CREATE OR REPLACE SECRET`.

- [ ] **Step 1: Write failing moto tests**

Append to `aurabackend/tests/test_storage_backend.py`:
```python
boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")


@pytest.fixture
def _s3_env(monkeypatch):
    monkeypatch.setenv("AURA_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AURA_S3_BUCKET", "aura-test")
    monkeypatch.setenv("AURA_S3_REGION", "us-east-1")
    monkeypatch.setenv("AURA_S3_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AURA_S3_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AURA_S3_PREFIX", "uploads")
    from shared.config import reload_settings  # see Task 3 Step 3 note
    reload_settings()
    reset_storage_backend()


class TestS3Backend:
    def test_write_list_read_delete(self, _s3_env):
        from moto import mock_aws
        with mock_aws():
            boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="aura-test")
            from shared.storage.s3 import S3Backend
            b = S3Backend()
            info = b.write("acme", "sales.csv", b"region,revenue\nN,1\n")
            assert info.name == "sales.csv"
            assert info.fingerprint  # etag|size
            assert b.read("acme", "sales.csv") == b"region,revenue\nN,1\n"
            names = {o.name for o in b.list("acme")}
            assert names == {"sales.csv"}
            assert b.exists("acme", "sales.csv") is True
            assert b.delete("acme", "sales.csv") is True
            assert b.list("acme") == []

    def test_list_is_tenant_scoped(self, _s3_env):
        from moto import mock_aws
        with mock_aws():
            boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="aura-test")
            from shared.storage.s3 import S3Backend
            b = S3Backend()
            b.write("acme", "a.csv", b"x\n1\n")
            b.write("globex", "b.csv", b"y\n2\n")
            assert {o.name for o in b.list("acme")} == {"a.csv"}
            assert {o.name for o in b.list("globex")} == {"b.csv"}

    def test_duckdb_uri_shape(self, _s3_env):
        from moto import mock_aws
        with mock_aws():
            boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="aura-test")
            from shared.storage.s3 import S3Backend
            assert (
                S3Backend().duckdb_uri("acme", "a.csv")
                == "s3://aura-test/uploads/acme/a.csv"
            )

    def test_configure_duckdb_runs_create_secret(self, _s3_env):
        from unittest.mock import MagicMock
        from shared.storage.s3 import S3Backend
        con = MagicMock()
        S3Backend().configure_duckdb(con)
        sql = " ".join(call.args[0] for call in con.execute.call_args_list)
        assert "httpfs" in sql
        assert "CREATE OR REPLACE SECRET" in sql
        assert "TYPE s3" in sql.lower() or "TYPE S3" in sql
```

NOTE: `S3Backend()` must NOT call boto3 at construction time for `duckdb_uri`/`configure_duckdb` (they only need config), so those two tests work without a live client. `write/read/list/delete/exists` create the client lazily on first use (inside `mock_aws()`).

- [ ] **Step 2: Run to verify it fails**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_storage_backend.py::TestS3Backend -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.storage.s3'` (and a `reload_settings` import error — added next).

- [ ] **Step 3: Add a settings reload helper (config.py)**

In `aurabackend/shared/config.py`, confirm there is a cached settings accessor (`get_settings()`); if it memoizes (e.g. `lru_cache`), add:
```python
def reload_settings() -> "Settings":
    """Rebuild the cached Settings from the current environment (tests/S45)."""
    global _settings  # or: get_settings.cache_clear() if using lru_cache
    _settings = Settings()
    return _settings
```
Match the file's existing memoization style. If `get_settings` uses `functools.lru_cache`, implement `reload_settings` as `get_settings.cache_clear(); return get_settings()`.

- [ ] **Step 4: Implement s3.py**

Create `aurabackend/shared/storage/s3.py`:
```python
"""S3-compatible object-storage backend (S3 / R2 / MinIO) — S45, Approach A.

DuckDB reads s3:// URIs directly via httpfs; boto3 handles write/list/delete.
"""
from __future__ import annotations

from typing import Any, List, Optional
from urllib.parse import urlparse

from shared.config import get_settings
from shared.storage.base import ObjectInfo, StorageBackend, tenant_slug


class S3Backend(StorageBackend):
    def __init__(self) -> None:
        s = get_settings()
        self._bucket = s.s3_bucket
        self._prefix = (s.s3_prefix or "").strip("/")
        self._region = s.s3_region
        self._endpoint_url = s.s3_endpoint_url  # boto3 wants full URL w/ scheme
        self._key_id = s.s3_access_key_id
        self._secret = s.s3_secret_access_key
        self._url_style = s.s3_url_style  # "path" | "vhost"
        self._use_ssl = s.s3_use_ssl
        self._client: Optional[Any] = None
        if not self._bucket:
            raise ValueError("AURA_S3_BUCKET must be set for the s3 backend.")

    # ── boto3 ──────────────────────────────────────────────────────────
    def _c(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config
            addressing = "virtual" if self._url_style == "vhost" else "path"
            self._client = boto3.client(
                "s3",
                region_name=self._region,
                endpoint_url=self._endpoint_url or None,
                aws_access_key_id=self._key_id,
                aws_secret_access_key=self._secret,
                config=Config(signature_version="s3v4",
                              s3={"addressing_style": addressing}),
            )
        return self._client

    def _key(self, tenant: str, filename: str) -> str:
        parts = [p for p in (self._prefix, tenant_slug(tenant), filename) if p]
        return "/".join(parts)

    def _tenant_prefix(self, tenant: str) -> str:
        parts = [p for p in (self._prefix, tenant_slug(tenant)) if p]
        return "/".join(parts) + "/"

    # ── StorageBackend ─────────────────────────────────────────────────
    def write(self, tenant: str, filename: str, data: bytes) -> ObjectInfo:
        key = self._key(tenant, filename)
        self._c().put_object(Bucket=self._bucket, Key=key, Body=data)
        head = self._c().head_object(Bucket=self._bucket, Key=key)
        return ObjectInfo(
            name=filename,
            size=head["ContentLength"],
            fingerprint=f'{head["ETag"].strip(chr(34))}|{head["ContentLength"]}',
            duckdb_uri=self.duckdb_uri(tenant, filename),
        )

    def read(self, tenant: str, filename: str) -> bytes:
        obj = self._c().get_object(Bucket=self._bucket, Key=self._key(tenant, filename))
        return obj["Body"].read()

    def list(self, tenant: str) -> List[ObjectInfo]:
        prefix = self._tenant_prefix(tenant)
        out: List[ObjectInfo] = []
        paginator = self._c().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                name = obj["Key"][len(prefix):]
                if "/" in name or not name:
                    continue  # flat per-tenant namespace only
                out.append(ObjectInfo(
                    name=name,
                    size=obj["Size"],
                    fingerprint=f'{obj["ETag"].strip(chr(34))}|{obj["Size"]}',
                    duckdb_uri=self.duckdb_uri(tenant, name),
                ))
        return sorted(out, key=lambda o: o.name)

    def delete(self, tenant: str, filename: str) -> bool:
        if not self.exists(tenant, filename):
            return False
        self._c().delete_object(Bucket=self._bucket, Key=self._key(tenant, filename))
        return True

    def exists(self, tenant: str, filename: str) -> bool:
        import botocore.exceptions
        try:
            self._c().head_object(Bucket=self._bucket, Key=self._key(tenant, filename))
            return True
        except botocore.exceptions.ClientError:
            return False

    def duckdb_uri(self, tenant: str, filename: str) -> str:
        return f"s3://{self._bucket}/{self._key(tenant, filename)}"

    def configure_duckdb(self, con: Any) -> None:
        # DuckDB ENDPOINT wants host:port WITHOUT scheme; USE_SSL carries the
        # http/https choice. boto3 wanted the full endpoint_url — translate.
        endpoint_clause = ""
        use_ssl = self._use_ssl
        if self._endpoint_url:
            parsed = urlparse(self._endpoint_url)
            host = parsed.netloc or parsed.path
            endpoint_clause = f", ENDPOINT '{host}'"
            use_ssl = parsed.scheme == "https"
        con.execute("INSTALL httpfs")
        con.execute("LOAD httpfs")
        con.execute(
            "CREATE OR REPLACE SECRET aura_s3 ("
            "TYPE s3, PROVIDER config, "
            f"KEY_ID '{self._key_id or ''}', "
            f"SECRET '{self._secret or ''}', "
            f"REGION '{self._region}', "
            f"URL_STYLE '{self._url_style}', "
            f"USE_SSL {str(use_ssl).lower()}"
            f"{endpoint_clause})"
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_storage_backend.py -q`
Expected: PASS (all, including `TestS3Backend`). If `botocore` import in `exists` is flagged unused-by-some-path, keep it local to the method (already is).

- [ ] **Step 6: Lint + commit**

```bash
../.venv/Scripts/python.exe -m ruff check shared/storage/s3.py tests/test_storage_backend.py shared/config.py --select E,F,I,W --ignore E501,E402,F401,E701,E712
git add aurabackend/shared/storage/s3.py aurabackend/tests/test_storage_backend.py aurabackend/shared/config.py
git commit -m "feat(storage): S3Backend (boto3 + DuckDB httpfs secret) with moto tests (S45)"
```

---

## Task 4: DuckDB connection factory

**Files:**
- Create: `aurabackend/shared/duckdb_factory.py`
- Modify: `aurabackend/api_gateway/routers/chat.py:163`, `aurabackend/api_gateway/routers/dashboards.py:187`, `aurabackend/api_gateway/routers/etl.py:276,328,447`
- Test: `aurabackend/tests/test_duckdb_factory.py` (create)

**Interfaces:**
- Consumes: `get_storage_backend()` from `shared.storage`.
- Produces: `new_connection(database: str = ":memory:") -> duckdb.DuckDBPyConnection` — a connection with `get_storage_backend().configure_duckdb(con)` already applied.

- [ ] **Step 1: Write the failing test**

Create `aurabackend/tests/test_duckdb_factory.py`:
```python
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.duckdb_factory import new_connection


def test_new_connection_local_is_usable(monkeypatch):
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import reset_storage_backend
    reset_storage_backend()
    con = new_connection()
    assert con.execute("SELECT 42").fetchone() == (42,)


def test_new_connection_configures_backend():
    fake = MagicMock()
    with patch("shared.duckdb_factory.get_storage_backend", return_value=fake):
        con = new_connection()
    fake.configure_duckdb.assert_called_once()
    assert con.execute("SELECT 1").fetchone() == (1,)
```

- [ ] **Step 2: Run to verify it fails**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_duckdb_factory.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.duckdb_factory'`.

- [ ] **Step 3: Implement the factory**

Create `aurabackend/shared/duckdb_factory.py`:
```python
"""Central DuckDB connection factory (S45).

Every connection that may read uploaded datasets must be created here so the
active storage backend can configure it (e.g. the S3 httpfs secret). Local
mode adds nothing, so this is safe everywhere.
"""
from __future__ import annotations

from typing import Any

import duckdb

from shared.storage import get_storage_backend


def new_connection(database: str = ":memory:") -> Any:
    con = duckdb.connect(database)
    get_storage_backend().configure_duckdb(con)
    return con
```

- [ ] **Step 4: Run to verify it passes**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_duckdb_factory.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Swap the upload-reading connection sites**

In each of these, replace `duckdb.connect(":memory:")` with `new_connection()` and add `from shared.duckdb_factory import new_connection` to the imports:
- `api_gateway/routers/chat.py:163`
- `api_gateway/routers/dashboards.py:187`
- `api_gateway/routers/etl.py:276`, `:328`, `:447`

Leave `api_gateway/persistence.py` and `shared/schema_indexer.py` as-is for now — they don't read tenant uploads (persistence is gateway state; schema_indexer is invoked with explicit paths). Do NOT remove the `import duckdb` lines if still referenced elsewhere in the file (e.g. duckdb exceptions); ruff F401 will flag if now-unused.

- [ ] **Step 6: Verify nothing broke**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_duckdb_factory.py tests/test_e2e_chat.py -q`
Expected: PASS (chat e2e still green; factory tests pass).

- [ ] **Step 7: Lint + commit**

```bash
../.venv/Scripts/python.exe -m ruff check shared/duckdb_factory.py api_gateway/routers/chat.py api_gateway/routers/dashboards.py api_gateway/routers/etl.py tests/test_duckdb_factory.py --select E,F,I,W --ignore E501,E402,F401,E701,E712
git add aurabackend/shared/duckdb_factory.py aurabackend/api_gateway/routers/chat.py aurabackend/api_gateway/routers/dashboards.py aurabackend/api_gateway/routers/etl.py aurabackend/tests/test_duckdb_factory.py
git commit -m "feat(storage): central DuckDB connection factory; configure backend on upload-reading connections (S45)"
```

---

## Task 5: Schema/query reader over the storage backend

**Files:**
- Modify: `aurabackend/shared/data_utils.py` — `build_schema_context` (~385), `_signature_for_upload_dirs` (~586), `_replay_tables` (~611), `_build_schema_context_with_recipe` (~634), `build_schema_context_cached` (~687)
- Modify: `aurabackend/api_gateway/routers/chat.py:161,164`, plus `queries.py`, `dashboards.py`, `etl.py`, `pipelines.py` call sites
- Test: `aurabackend/tests/test_data_utils_storage.py` (create)

**Interfaces:**
- Consumes: `ObjectInfo` (`name`, `duckdb_uri`, `fingerprint`), `get_storage_backend()`.
- Produces: `build_schema_context_cached(conn, tenant: str, use_llm=True)` and `build_schema_context(conn, tenant: str, use_llm=True)` keyed on the tenant; internally enumerate `get_storage_backend().list(tenant)`. The `upload_dirs: List[Path]` parameter is replaced by `tenant: str`.

- [ ] **Step 1: Write the failing test (LocalBackend parity)**

Create `aurabackend/tests/test_data_utils_storage.py`:
```python
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_build_schema_context_over_local_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import get_storage_backend, reset_storage_backend
    reset_storage_backend()
    get_storage_backend().write("acme", "sales.csv", b"region,revenue\nN,100\nS,200\n")

    import duckdb
    from shared.data_utils import build_schema_context_cached
    con = duckdb.connect(":memory:")
    result = asyncio.run(build_schema_context_cached(con, "acme", use_llm=False))
    assert "sales" in result["tables"]
    cols = [c["name"] for c in result["tables"]["sales"]["columns"]]
    assert "region" in cols and "revenue" in cols


def test_schema_context_tenant_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import get_storage_backend, reset_storage_backend
    reset_storage_backend()
    get_storage_backend().write("acme", "a.csv", b"x\n1\n")
    get_storage_backend().write("globex", "b.csv", b"y\n2\n")

    import duckdb
    from shared.data_utils import build_schema_context_cached
    con = duckdb.connect(":memory:")
    acme = asyncio.run(build_schema_context_cached(con, "acme", use_llm=False))
    assert set(acme["tables"]) == {"a"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_data_utils_storage.py -q`
Expected: FAIL — `build_schema_context_cached` currently takes `upload_dirs`, so passing `"acme"` raises `AttributeError`/`TypeError` (a string has no `.exists()`).

- [ ] **Step 3: Generalize the reader**

In `aurabackend/shared/data_utils.py`:

(a) `build_schema_context(conn, tenant, use_llm=True)` — replace the `for upload_dir in upload_dirs: ... upload_dir.iterdir()` loop with:
```python
    from shared.storage import get_storage_backend
    backend = get_storage_backend()
    tables: Dict[str, Dict] = {}
    for obj in backend.list(tenant):
        ext = os.path.splitext(obj.name)[1].lower()
        if ext not in (".csv", ".parquet", ".json"):
            continue
        table_name = re.sub(r"[^A-Za-z0-9_]", "_", os.path.splitext(obj.name)[0])
        try:
            info = smart_load_file(conn, obj.duckdb_uri, table_name, use_llm=use_llm)
            tables[table_name] = info
        except Exception as e:
            logger.warning("Failed to load %s: %s", obj.name, e)
```
Keep the relationships + `_format_context_for_llm` tail unchanged.

(b) `_signature_for_upload_dirs(upload_dirs)` → `_signature_for_tenant(tenant)`:
```python
def _signature_for_tenant(tenant: str) -> str:
    from shared.storage import get_storage_backend
    parts = [f"{o.name}|{o.fingerprint}" for o in get_storage_backend().list(tenant)]
    if not parts:
        return ""
    return hashlib.sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()[:32]
```

(c) `_replay_tables(conn, loaders)` — change the per-loader path: replace `file_path = loader["file_path"]; if not Path(file_path).exists(): continue; file_path_str = file_path.replace(...)` with `uri = loader["duckdb_uri"]` and use `uri` in the `read_fn('{uri}')` call (drop the `.exists()` guard; on failure the existing `except` warns). Wherever the recipe is BUILT (`_build_schema_context_with_recipe`), store `"duckdb_uri": obj.duckdb_uri` instead of `"file_path": str(path)`.

(d) `_build_schema_context_with_recipe(conn, tenant, use_llm)` — mirror (a), and build each loader dict with `"duckdb_uri"`, `"table_name"`, `"read_fn"`, `"renames"`.

(e) `build_schema_context_cached(conn, tenant, use_llm=True)` — replace `sig = _signature_for_upload_dirs(upload_dirs)` with `sig = _signature_for_tenant(tenant)`; thread `tenant` into the cache key and the `_build_schema_context_with_recipe(conn, tenant, use_llm)` call. Keep the in-memory `schema_cache` keyed on `f"{tenant}:{sig}"`.

- [ ] **Step 4: Update the callers**

- `chat.py`: delete line 161 (`upload_dirs = [...]`); change line 164 to `schema_result = await build_schema_context_cached(con, tenant, use_llm=True)` where `tenant = _request_tenant(http_request)` (import it from `api_gateway.routers.workspaces`). `con = new_connection()` (from Task 4).
- `queries.py` (×2), `dashboards.py`, `etl.py`, `pipelines.py`: same swap — pass the request-derived tenant instead of `[tenant_upload_dir(request)]`, and create the connection via `new_connection()`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_data_utils_storage.py tests/test_e2e_chat.py -q`
Expected: PASS. If `test_e2e_chat.py` planted files in `data/uploads/default/`, it still works because LocalBackend lists the `default` tenant dir.

- [ ] **Step 6: Run the broader data/query suite (regression)**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_data_utils.py tests/test_queries.py -q`
Expected: PASS. Fix any caller still passing `upload_dirs`.

- [ ] **Step 7: Lint + commit**

```bash
../.venv/Scripts/python.exe -m ruff check shared/data_utils.py api_gateway/routers/chat.py api_gateway/routers/queries.py api_gateway/routers/dashboards.py api_gateway/routers/etl.py api_gateway/routers/pipelines.py tests/test_data_utils_storage.py --select E,F,I,W --ignore E501,E402,F401,E701,E712
git add aurabackend/shared/data_utils.py aurabackend/api_gateway/routers/chat.py aurabackend/api_gateway/routers/queries.py aurabackend/api_gateway/routers/dashboards.py aurabackend/api_gateway/routers/etl.py aurabackend/api_gateway/routers/pipelines.py aurabackend/tests/test_data_utils_storage.py
git commit -m "feat(storage): read schema/query datasets through the storage backend, keyed by tenant (S45)"
```

---

## Task 6: Upload write + FileService through the backend

**Files:**
- Modify: `aurabackend/api_gateway/routers/files.py` (`upload_universal`, `_safe_upload_path`)
- Modify: `aurabackend/shared/file_service.py` (`save_file`, `list_files`, `get_file_info`, `delete_file`, `process_file`)
- Test: `aurabackend/tests/test_files_storage.py` (create)

**Interfaces:**
- Consumes: `get_storage_backend()`, `_request_tenant`, the Sec-8 sanitizer.
- Produces: `_safe_upload_name(filename: str) -> str` (sanitized filename; raises 400 on traversal/NUL/empty) — refactored from `_safe_upload_path`. Upload writes via `backend.write(tenant, safe_name, data)`.

- [ ] **Step 1: Write the failing test**

Create `aurabackend/tests/test_files_storage.py`:
```python
import io
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import reset_storage_backend
    reset_storage_backend()
    from api_gateway.main import app
    return TestClient(app)


def test_upload_then_list_via_backend(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/v1/files/upload",
                    files={"file": ("sales.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")})
    assert r.status_code == 200, r.text
    from shared.storage import get_storage_backend
    names = {o.name for o in get_storage_backend().list("default")}
    assert "sales.csv" in names


def test_upload_rejects_traversal(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/v1/files/upload",
                    files={"file": ("../evil.csv", io.BytesIO(b"x\n1\n"), "text/csv")})
    assert r.status_code == 400
```

- [ ] **Step 2: Run to verify it fails**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_files_storage.py -q`
Expected: FAIL — upload still writes to the flat path / `get_storage_backend().list("default")` does not see it.

- [ ] **Step 3: Refactor `_safe_upload_path` → `_safe_upload_name` and route the write**

In `files.py`, change the Sec-8 helper to return a sanitized filename (keep all rejection rules — basename after `replace("\\","/")`, reject `""`, `"."`, `".."`, NUL). In `upload_universal`:
```python
    from shared.storage import get_storage_backend
    from api_gateway.routers.workspaces import _request_tenant
    safe_name = _safe_upload_name(file.filename)   # raises HTTPException(400) on bad input
    data = await file.read()
    tenant = _request_tenant(request)
    get_storage_backend().write(tenant, safe_name, data)
    # ...existing dataset-ownership upsert, now using safe_name + tenant...
```
Return the sanitized `safe_name` in the response (as today).

- [ ] **Step 4: Route FileService through the backend**

In `shared/file_service.py`, change `list_files(subdir)`, `get_file_info`, `delete_file(file_id, subdir)` to call `get_storage_backend()` with `subdir` as the tenant; `process_file`/profiling reads bytes via `backend.read(tenant, filename)`. Preserve the public return shapes (the dicts the routes serialize).

- [ ] **Step 5: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_files_storage.py tests/test_file_service.py -q`
Expected: PASS (new tests + existing FileService tests).

- [ ] **Step 6: Lint + commit**

```bash
../.venv/Scripts/python.exe -m ruff check api_gateway/routers/files.py shared/file_service.py tests/test_files_storage.py --select E,F,I,W --ignore E501,E402,F401,E701,E712
git add aurabackend/api_gateway/routers/files.py aurabackend/shared/file_service.py aurabackend/tests/test_files_storage.py
git commit -m "feat(storage): upload write + FileService through the storage backend (S45)"
```

---

## Task 7: Tier-B MinIO integration test + CI lane

**Files:**
- Create: `aurabackend/tests/test_storage_s3_duckdb.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: a live S3-compatible endpoint via `AURA_S3_TEST_ENDPOINT`, `AURA_S3_TEST_KEY`, `AURA_S3_TEST_SECRET`, `AURA_S3_TEST_BUCKET`.

- [ ] **Step 1: Write the gated integration test**

Create `aurabackend/tests/test_storage_s3_duckdb.py`:
```python
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = pytest.mark.skipif(
    not os.getenv("AURA_S3_TEST_ENDPOINT"),
    reason="set AURA_S3_TEST_ENDPOINT to run the real DuckDB-reads-s3 test",
)


def _env(monkeypatch):
    monkeypatch.setenv("AURA_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AURA_S3_ENDPOINT_URL", os.environ["AURA_S3_TEST_ENDPOINT"])
    monkeypatch.setenv("AURA_S3_BUCKET", os.environ.get("AURA_S3_TEST_BUCKET", "aura-test"))
    monkeypatch.setenv("AURA_S3_ACCESS_KEY_ID", os.environ["AURA_S3_TEST_KEY"])
    monkeypatch.setenv("AURA_S3_SECRET_ACCESS_KEY", os.environ["AURA_S3_TEST_SECRET"])
    monkeypatch.setenv("AURA_S3_URL_STYLE", "path")
    monkeypatch.setenv("AURA_S3_USE_SSL", "false")
    from shared.config import reload_settings
    reload_settings()
    from shared.storage import reset_storage_backend
    reset_storage_backend()


def test_duckdb_reads_csv_from_s3(monkeypatch):
    _env(monkeypatch)
    import boto3
    from shared.storage import get_storage_backend
    b = get_storage_backend()
    try:
        boto3.client(
            "s3", endpoint_url=os.environ["AURA_S3_TEST_ENDPOINT"],
            aws_access_key_id=os.environ["AURA_S3_TEST_KEY"],
            aws_secret_access_key=os.environ["AURA_S3_TEST_SECRET"],
        ).create_bucket(Bucket=os.environ.get("AURA_S3_TEST_BUCKET", "aura-test"))
    except Exception:
        pass
    b.write("acme", "sales.csv", b"region,revenue\nN,100\nS,200\nN,50\n")

    from shared.data_utils import build_schema_context_cached
    from shared.duckdb_factory import new_connection
    con = new_connection()
    result = asyncio.run(build_schema_context_cached(con, "acme", use_llm=False))
    assert "sales" in result["tables"]
    rows = con.execute(
        "SELECT region, SUM(revenue) FROM read_csv_auto('"
        + b.duckdb_uri("acme", "sales.csv") + "') GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert dict(rows) == {"N": 150, "S": 200}
```

- [ ] **Step 2: Run locally against MinIO (optional but recommended)**

```bash
docker run -d --name minio -p 9000:9000 -e MINIO_ROOT_USER=test -e MINIO_ROOT_PASSWORD=test12345 minio/minio server /data
AURA_S3_TEST_ENDPOINT=http://localhost:9000 AURA_S3_TEST_KEY=test AURA_S3_TEST_SECRET=test12345 \
  ../.venv/Scripts/python.exe -m pytest tests/test_storage_s3_duckdb.py -q
```
Expected: PASS (1 passed). Without the env vars: `1 skipped`.

- [ ] **Step 3: Add the CI lane**

In `.github/workflows/ci.yml`, add a job mirroring the Scheduler-Postgres lane but with a MinIO service container:
```yaml
  storage-s3:
    name: Storage S3 (MinIO integration)
    runs-on: ubuntu-latest
    services:
      minio:
        image: bitnami/minio:latest
        ports: ["9000:9000"]
        env:
          MINIO_ROOT_USER: test
          MINIO_ROOT_PASSWORD: test12345
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r aurabackend/requirements.txt
      - name: Run S3 integration test
        working-directory: aurabackend
        env:
          AURA_S3_TEST_ENDPOINT: http://localhost:9000
          AURA_S3_TEST_KEY: test
          AURA_S3_TEST_SECRET: test12345
          AURA_S3_TEST_BUCKET: aura-test
        run: python -m pytest tests/test_storage_s3_duckdb.py -v
```
Match the existing jobs' checkout/python-setup style in the file (copy from the `Scheduler Distributed` job).

- [ ] **Step 4: Commit**

```bash
git add aurabackend/tests/test_storage_s3_duckdb.py .github/workflows/ci.yml
git commit -m "test(storage): Tier-B MinIO lane for the real DuckDB-reads-s3 path (S45)"
```

---

## Task 8: Air-gap httpfs bundling + deploy wiring + docs

**Files:**
- Modify: `aurabackend/Dockerfile`
- Modify: `aurabackend/.env.prod.example`, `docker-compose.prod.yml`, `deploy/helm/aura/values.yaml`, `deploy/helm/aura/README.md`, `docs/DEPLOYMENT.md`

**Interfaces:** none (deploy/config only).

- [ ] **Step 1: Pre-install httpfs in the image**

In `aurabackend/Dockerfile`, after the Python deps install (so `duckdb` is present), add a build step that installs the httpfs extension into the image so `LOAD httpfs` works offline:
```dockerfile
# Pre-install DuckDB httpfs so air-gapped s3:// reads work with no network.
RUN python -c "import duckdb; duckdb.connect().execute('INSTALL httpfs')"
```
Place it in BOTH backend targets that read uploads (`base-runtime`; and `streaming-runtime` if it doesn't inherit). Verify the extension dir is in the final image layer (the install writes to `~/.duckdb/extensions`).

- [ ] **Step 2: Add the env block to .env.prod.example**

Append an object-storage section (names + guidance only, no secrets):
```dotenv
# ── Object storage (uploads) — multi-node scale & on-prem/air-gapped ───────
# Default is local disk (AURA_STORAGE_BACKEND=local). For multi-replica k8s,
# point uploads at S3-compatible storage so the gateway is stateless.
# AURA_STORAGE_BACKEND=s3
# AURA_S3_BUCKET=aura-uploads
# AURA_S3_ENDPOINT_URL=          # set for R2/MinIO/on-prem; leave blank for AWS S3
# AURA_S3_REGION=us-east-1
# AURA_S3_ACCESS_KEY_ID=         # (or AWS_ACCESS_KEY_ID)
# AURA_S3_SECRET_ACCESS_KEY=     # (or AWS_SECRET_ACCESS_KEY)
# AURA_S3_URL_STYLE=path         # path for MinIO/most on-prem; vhost for AWS
# AURA_S3_USE_SSL=true           # false for an http:// MinIO
```

- [ ] **Step 3: Thread S3 env through prod compose**

In `docker-compose.prod.yml` `x-backend-env` anchor, add the `AURA_STORAGE_BACKEND` + `AURA_S3_*` keys with `${VAR:-}` defaults (they flow via `env_file: .env` anyway, but listing them documents intent). Document an optional `minio` service overlay in a comment.

- [ ] **Step 4: Helm values + README + DEPLOYMENT.md**

- `deploy/helm/aura/values.yaml`: add the `AURA_STORAGE_BACKEND` / `AURA_S3_*` non-secret keys under `env` (commented guidance) and note that `AURA_S3_ACCESS_KEY_ID`/`SECRET` belong in the `aura-secrets` Secret; when `s3`, set `uploads.enabled: false` (no PVC).
- `deploy/helm/aura/README.md` + `docs/DEPLOYMENT.md`: object storage is the recommended multi-replica path; the existing uploads-PVC note gets a "or use object storage (S45)" pointer. In the air-gapped section, note `AURA_STORAGE_BACKEND=s3` + in-cluster/on-host MinIO + that the image bundles httpfs.

- [ ] **Step 5: Verify the chart still renders**

Run: `python deploy/helm/aura/tests/render_test.py`
Expected: `All N templates parse cleanly.`

- [ ] **Step 6: Commit**

```bash
git add aurabackend/Dockerfile aurabackend/.env.prod.example docker-compose.prod.yml deploy/helm/aura/values.yaml deploy/helm/aura/README.md docs/DEPLOYMENT.md
git commit -m "feat(storage): bundle DuckDB httpfs in image + wire S3 config into compose/helm/docs (S45)"
```

---

## Final verification (before PR)

- [ ] Run the full backend suite on the default (local) backend: `../.venv/Scripts/python.exe -m pytest -q` — everything green, proving backward compatibility.
- [ ] Run the Tier-B test against a local MinIO (Task 7 Step 2) — 1 passed.
- [ ] Ruff clean across all touched files.
- [ ] Open the PR (controller, not a subagent), closes #114; request `security-review` on the new path (S3 secret handling, key prefix isolation, the `_safe_upload_name` refactor).
