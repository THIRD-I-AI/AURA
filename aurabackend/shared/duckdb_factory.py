"""Central DuckDB connection factory (S45).

Every connection that may read uploaded datasets must be created here so the
active storage backend can configure it (e.g. the S3 httpfs secret). Local
mode adds nothing, so this is safe everywhere.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, TypeVar

import duckdb

from shared.storage import get_storage_backend

T = TypeVar("T")


def query_timeout_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("AURA_QUERY_TIMEOUT_SECONDS", "120")))
    except ValueError:
        return 120.0


async def run_interruptible(con: Any, fn: Callable[[], T], timeout: float | None = None) -> T:
    """Run blocking DuckDB work ``fn`` in a thread, and stop the query if it overruns
    ``timeout`` (default ``AURA_QUERY_TIMEOUT_SECONDS``) or the caller is cancelled.

    BUG-381: a bare ``asyncio.to_thread`` has no limit, and a client disconnecting does
    not cancel it -- a runaway user or stored query held one of the loop's few default
    executor threads (6 on the deployed 2-vCPU box) for as long as it liked. A timed-out
    ``wait_for`` alone would only abandon the thread; ``interrupt()`` stops the query."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn), timeout or query_timeout_seconds())
    except (asyncio.TimeoutError, asyncio.CancelledError):
        con.interrupt()
        raise


# BUG-468: DuckDB's default memory_limit is 80% of detected RAM *per instance*, and every
# request opens its own instance; one user sort over range() filled that within seconds
# and pushed the 512M gateway container into the OOM killer. Past this cap DuckDB spills
# or fails the one query. A caller that needs more (the pipeline engine) raises it before
# lock_down_connection; after the lockdown user SQL cannot.
CONNECTION_MEMORY_LIMIT = "256MB"


# BUG-597: no connection may fetch or load an extension on its own. An ATTACH of a
# non-DuckDB file used to auto-install and auto-load sqlite_scanner from the internet
# before any lockdown. httpfs, which S3 mode needs, is installed in the image and
# loaded explicitly by configure_duckdb.
_NO_AUTO_EXTENSIONS = {"autoinstall_known_extensions": False, "autoload_known_extensions": False}


def require_duckdb_file(path: str) -> None:
    """Refuse a caller-supplied database file that is not a DuckDB database (BUG-597).

    DuckDB opens other formats through extensions: duckdb.connect() or an ATTACH of a
    SQLite file loads sqlite_scanner, whose sqlite_scan() then reads any SQLite file on
    the box through SQLite's own I/O, past enable_external_access=false. A DuckDB file
    carries the magic bytes "DUCK" after its 8-byte checksum."""
    try:
        with open(path, "rb") as fh:
            header = fh.read(12)
    except FileNotFoundError:
        return
    if header and header[8:12] != b"DUCK":
        raise ValueError("not a DuckDB database file")


def new_connection(database: str = ":memory:") -> Any:
    con = duckdb.connect(database, config=dict(_NO_AUTO_EXTENSIONS))
    con.execute(f"SET memory_limit='{CONNECTION_MEMORY_LIMIT}'")
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
