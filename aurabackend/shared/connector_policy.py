"""The one place a connector to a caller-named target is opened.

A connector config comes from a request: a database host, or a path on this
server's own disk. Two rules apply to every one of them --

* a network connector's host must be a public address (``shared.ssrf``), so the
  backend cannot be pointed at its own loopback, private network or the cloud
  metadata service, and an omitted host cannot silently become ``localhost``;
* a server-file connector's path must be ``:memory:`` or lie inside the directory
  the caller is allowed to read.

Each route used to carry its own copy of those checks, or none: BUG-232, 357, 367,
377, 404, 441 and 442 are the same gap found in seven places. ``open_connector``
applies both and then builds the connector; ``tests/test_connector_door.py`` fails
if any other code constructs one.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Optional

from shared.ssrf import resolve_allowed_db_host

# Connector types that open a network connection to a caller-named host.
NETWORK_HOST_CONNECTORS = frozenset({"postgresql", "mysql"})
# Connector types that open a file on this server's own disk.
SERVER_FILE_CONNECTORS = frozenset({"duckdb", "duckdb_spatial", "faiss"})


class ConnectorPolicyError(ValueError):
    """The requested connector target is not one the caller may reach."""


async def require_allowed_host(conn_type: str, host: Any) -> Any:
    """Return the address to connect to for ``host`` (see shared.ssrf, BUG-547)."""
    if conn_type not in NETWORK_HOST_CONNECTORS:
        return host
    pinned = await resolve_allowed_db_host(host)
    if pinned is None:
        raise ConnectorPolicyError(f"{conn_type} host must be a public address")
    return pinned


def confine_file_paths(conn_type: str, config: Any, files_root: Optional[str]) -> None:
    """Rewrite a server-file connector's paths to resolved paths inside ``files_root``.

    ``files_root`` is the directory this caller may read (their own uploads); None
    means there is no such local directory -- a remote storage backend -- and only
    ``:memory:`` passes. Covers ``connection_string``, ``database`` and
    ``extra_params["db_path"]``, which DuckDBConnector opens ahead of ``database``.
    """
    if conn_type not in SERVER_FILE_CONNECTORS:
        return
    root = os.path.realpath(files_root) if files_root else None

    def confined(raw: Any) -> Any:
        if not raw or raw == ":memory:":
            return raw
        if root is not None:
            candidate = str(raw) if os.path.isabs(str(raw)) else os.path.join(root, str(raw))
            resolved = os.path.realpath(candidate)
            try:
                if os.path.commonpath([resolved, root]) == root:
                    return resolved
            except ValueError:  # different drives
                pass
        raise ConnectorPolicyError(f"{conn_type} path must be ':memory:' or a file in your own uploads")

    for attr in ("connection_string", "database"):
        setattr(config, attr, confined(getattr(config, attr, None)))
    extra = getattr(config, "extra_params", None)
    if isinstance(extra, dict) and "db_path" in extra:
        extra["db_path"] = confined(extra["db_path"])


async def open_connector(
    conn_type: str,
    config: Any,
    *,
    files_root: Optional[str],
    build: Optional[Callable[[Any], Any]] = None,
) -> Any:
    """Check ``config`` against both rules, then build the connector.

    ``build`` turns the checked config into a connector; by default the central
    registry does. Raises ``ConnectorPolicyError`` for a refused target. Returns
    None for an unknown or unavailable connector type, as the registry does.
    """
    conn_type = conn_type.lower()
    pinned = await require_allowed_host(conn_type, getattr(config, "host", None))
    if conn_type in NETWORK_HOST_CONNECTORS:
        if hasattr(config, "tls_mode"):
            from shared.db_tls import build_ssl_context

            # ADR-001: TLS verifies the certificate as the name the caller entered,
            # taken here from `host` before it is pinned (never from the request body).
            config.tls_server_name = str(getattr(config, "host", "") or "").strip().strip("[]") or None

            # Built once here so a bad CA bundle or key pair is a 400 now, not a
            # connection that quietly reports "failed" later.
            await asyncio.to_thread(build_ssl_context, config)
        # BUG-547: the driver gets the address that was checked, not the name, so
        # it cannot resolve again and be rebound to an internal address.
        config.host = pinned
    confine_file_paths(conn_type, config, files_root)
    if build is not None:
        return build(config)
    from connectors.registry import build_connector

    return build_connector(conn_type, config)
