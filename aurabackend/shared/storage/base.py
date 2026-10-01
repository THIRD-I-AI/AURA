"""Storage backend abstraction for uploaded datasets (S45)."""
from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List

_TENANT_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(frozen=True)
class ObjectInfo:
    name: str          # the filename within the tenant (no prefix/dir)
    size: int          # bytes
    fingerprint: str   # stable change token: mtime|size (local) or etag|size (s3)
    duckdb_uri: str    # what read_csv_auto() consumes: local path or s3://...


def safe_object_name(filename: str) -> str:
    """Reject path-traversal / separator / NUL filenames (Sec-8 parity).

    The tenant component is slugged separately; this guards the filename axis
    so a raw caller cannot escape the tenant namespace. Returns the name
    unchanged when safe; raises ValueError otherwise.
    """
    name = filename or ""
    if name in ("", ".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError(f"unsafe object filename: {filename!r}")
    return name


def tenant_slug(tenant: str) -> str:
    """Filesystem/key-safe tenant component -- the ONE definition (workspaces.py's
    tenant_dir_name delegates here).

    Anything outside [A-Za-z0-9_-] is stripped so a hostile org_id cannot escape the
    upload hierarchy; empty/None -> 'default'.

    BUG-231: stripping alone is lossy, so distinct ids shared a directory ('acme.com'
    and 'acmec.om' -> 'acmecom'; workspace folder 'orgA::x' -> 'orgAx', the same as a
    tenant literally named 'orgAx'). An id that is already safe keeps EXACTLY its old
    name (UUID org ids, 'default' -- no existing data moves); an id that loses
    characters gets a short hash of the ORIGINAL id appended, making it unique.
    """
    raw = str(tenant or "")
    slug = _TENANT_SLUG_RE.sub("", raw).strip("-_")
    if not raw:
        return "default"
    if slug == raw:
        return slug
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{slug or 'tenant'}-{digest}"


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
