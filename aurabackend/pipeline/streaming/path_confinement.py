"""Where a streaming pipeline may read and write on the server's disk.

A pipeline's source and sink ``config`` comes straight from the request body, and the
file watcher, the file sink and the DuckDB sink used the paths in it as given
(BUG-277, BUG-278, BUG-283): a tenant could watch ``data/uploads`` -- the parent of
every tenant's upload directory -- or any absolute path, and write output or a
database file anywhere the process can.

The engine now builds those components from a config rewritten here:

* the file watcher reads only the pipeline tenant's own upload directory, with a
  plain filename pattern (no separators, no ``..``, no ``**``);
* the file sink and the DuckDB sink write only under
  ``data/streaming_output/<tenant>/<pipeline>/``.

Any ``watch_dir`` / ``output_dir`` in the request is ignored rather than rejected, so
a definition saved before this existed keeps running -- inside its own tenant.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

from shared.storage import get_storage_backend
from shared.storage.base import tenant_slug
from shared.storage.local import LocalBackend

STREAMING_OUTPUT_ROOT = os.path.join("data", "streaming_output")

_PLAIN_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def _plain(value: str, what: str) -> str:
    if not value or value in (".", "..") or not _PLAIN_NAME.match(value):
        raise ValueError(f"{what} must be a plain name (letters, digits, '.', '_' or '-')")
    return value


def tenant_output_dir(tenant: Optional[str], pipeline_id: str) -> str:
    return os.path.join(STREAMING_OUTPUT_ROOT, tenant_slug(tenant), _plain(str(pipeline_id), "pipeline id"))


def confine_file_watcher(config: Dict[str, Any], tenant: Optional[str]) -> Dict[str, Any]:
    backend = get_storage_backend()
    if not isinstance(backend, LocalBackend):
        raise ValueError("the file_watcher source needs local upload storage; it is not available on this deployment")
    pattern = str(config.get("pattern", "*.csv"))
    if "/" in pattern or "\\" in pattern or ".." in pattern or "**" in pattern:
        raise ValueError("file_watcher pattern must match file names only (no path separators, '..' or '**')")
    confined = dict(config)
    confined["watch_dir"] = str(backend.tenant_dir(tenant_slug(tenant)))
    confined["pattern"] = pattern
    return confined


def confine_file_sink(config: Dict[str, Any], tenant: Optional[str], pipeline_id: str) -> Dict[str, Any]:
    confined = dict(config)
    confined["output_dir"] = tenant_output_dir(tenant, pipeline_id)
    return confined


def confine_database_sink(config: Dict[str, Any], tenant: Optional[str], pipeline_id: str) -> Dict[str, Any]:
    if config.get("connector", "duckdb") != "duckdb":
        return config
    path = config.get("path", ":memory:")
    if path == ":memory:":
        return config
    name = _plain(os.path.basename(str(path).replace("\\", "/")), "database file name")
    out_dir = tenant_output_dir(tenant, pipeline_id)
    os.makedirs(out_dir, exist_ok=True)
    confined = dict(config)
    confined["path"] = os.path.join(out_dir, name)
    return confined
