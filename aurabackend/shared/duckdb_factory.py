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


def lock_down_connection(con: Any) -> None:
    """Cut a connection off from the filesystem and network (BUG-196).

    Call this AFTER every table the caller needs has been materialised into the
    connection (``build_schema_context_cached`` does that with CREATE TABLE ... AS)
    and BEFORE any user-, stored- or LLM-supplied SQL runs on it. A connection from
    ``new_connection`` can otherwise ``read_csv('/etc/passwd')``, ``COPY ... TO``, ``ATTACH``
    or hit a URL, and the keyword/AST validators in front of it do not cover those.

    ``enable_external_access=false`` disables every file/network reader and writer
    (including replacement scans like ``FROM '/path'`` and ``glob``); tables already
    loaded stay fully queryable. ``lock_configuration`` stops the SQL from switching it
    back on.
    """
    con.execute("SET enable_external_access=false")
    con.execute("SET lock_configuration=true")
