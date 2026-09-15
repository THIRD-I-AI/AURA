"""Blocklist guard for caller-supplied SQL *expressions* spliced into a
larger, trusted DuckDB query (e.g. a `custom_sql`/`add_column` transform
step's condition or computed-column expression).

This is NOT a general SQL sanitizer -- it can't be, since the expression is
legitimate, arbitrary SQL by design (a WHERE condition, a computed column).
What it blocks is DuckDB's file/network-access surface: the shared DuckDB
connection factory (`shared/duckdb_factory.py`) applies no
`enable_external_access` restriction (it's shared with the legitimate
source-load and sink-write steps on the same connection, so it can't simply
be locked down wholesale), so an unrestricted expression could use
`read_csv_auto`/`ATTACH`/`httpfs`/etc. to read arbitrary local files or
other tenants' data, or reach the network -- regardless of how the
expression's own identifiers/values are quoted.

Originally introduced for `api_gateway/routers/etl.py`'s `custom_sql`
transform step (BUG-053); reused for `pipeline/engine.py`'s ADD_COLUMN and
CUSTOM_SQL steps (BUG-082/083), which had never had this guard applied.

Residual risk, not silently accepted: this is a keyword blocklist, not a
sandboxed connection -- a sufficiently creative DuckDB syntax variant not
covered by the pattern could in principle slip through. Closing this with
certainty would need a genuinely separate, externally-access-disabled
connection for these steps.
"""
from __future__ import annotations

import re

_BLOCKED_PATTERN = re.compile(
    r"\b(read_csv(_auto)?|read_parquet|read_json(_auto)?|read_ndjson|"
    r"read_text|read_blob|glob|attach|detach|copy|pragma|install|load|"
    r"httpfs)\b",
    re.IGNORECASE,
)


def validate_sql_expression(expr: str) -> None:
    """Raise ``ValueError`` if ``expr`` references a blocked file/network
    access function or table, or a bare URI."""
    if _BLOCKED_PATTERN.search(expr):
        raise ValueError(
            "expression may not reference file/network access functions "
            "(read_csv, read_parquet, ATTACH, COPY, PRAGMA, INSTALL, LOAD, etc.)"
        )
    if "://" in expr:
        raise ValueError("expression may not reference URIs")
