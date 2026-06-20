"""Local-filesystem storage backend — today's behavior behind the interface."""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

from shared.storage.base import ObjectInfo, StorageBackend, safe_object_name, tenant_slug

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
        filename = safe_object_name(filename)
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
