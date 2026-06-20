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
