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
