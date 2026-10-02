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
    r"httpfs|export|import|database)\b",
    re.IGNORECASE,
)

# BUG-220: an expression is meant to be a single, self-contained SQL *expression*
# (a WHERE condition, a computed column) -- it must never contain a second,
# top-level statement. Without this, the word-level checks above are moot: a
# caller can close out the enclosing parens/CTE and stack ``; EXPORT DATABASE
# '<path>' (FORMAT CSV); --`` (or any other statement) after a semicolon, since
# none of the patterns above look at statement *structure*, only at words that
# appear anywhere in the string. Confirmed live: DuckDB's Connection.execute()
# runs semicolon-separated statements and returns the last one's result, so the
# stacked statement really does run. A semicolon can legitimately appear only
# inside a quoted string literal or identifier, so strip those first.
#
# BUG-311: the quoted spans used to be removed with one regex, which knows nothing
# about comments. A quote inside a block comment -- ``/*'*/ ; DROP TABLE t; -- /*'*/``
# -- opened a "string" that swallowed everything up to the next quote, so the very
# text the checks below inspect was blanked out. The expression is now scanned once,
# left to right, and a comment outside a quoted span is refused: a single expression
# has no use for one.

def _strip_quoted(expr: str) -> str:
    """``expr`` with every quoted string literal / quoted identifier blanked out,
    so a semicolon or keyword inside one is not mistaken for SQL structure.

    Raises ``ValueError`` on a SQL comment or an unterminated quote."""
    out = []
    i, n = 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch in ("'", '"'):
            j = i + 1
            while True:
                j = expr.find(ch, j)
                if j == -1:
                    raise ValueError("expression has an unterminated quote")
                if expr[j:j + 2] == ch * 2:   # doubled quote = escaped quote
                    j += 2
                    continue
                break
            out.append(" ")
            i = j + 1
            continue
        if expr.startswith("/*", i) or expr.startswith("--", i):
            # Everything before the comment is still checked first, so a stacked
            # statement keeps its own, more specific error.
            _reject_structure("".join(out))
            raise ValueError("expression may not contain SQL comments")
        out.append(ch)
        i += 1
    return "".join(out)

# BUG-207: the named list above missed most of DuckDB's file-reading table functions
# (parquet_scan, read_json_objects -- the trailing word boundary defeats the read_json match --
# read_ndjson_objects, sniff_csv, parquet_metadata/schema, read_xlsx, st_read, delta/
# iceberg scans ...). Match them by *family* in call position instead of by exact name,
# and block the functions that run a string as SQL (query / query_table), which would
# otherwise let an obfuscated name ('read_' || 'csv') slip past every pattern here.
# Call position (`name (`) keeps a plain column called e.g. "read_count" legal.
_BLOCKED_CALL_PATTERN = re.compile(
    r"\b(read_\w+|\w+_scan|parquet_\w+|sniff_csv|st_read\w*|iceberg_\w+|delta_\w+|"
    r"duckdb_\w+|query|query_table|json_execute_serialized_sql|getenv|current_setting)\s*\(",
    re.IGNORECASE,
)

# BUG-114: DuckDB also opens a file directly as a table when a bare string
# literal appears where a table reference is expected -- a "replacement
# scan" -- e.g. `SELECT * FROM '/etc/passwd'` or a nested
# `(SELECT col FROM 'other-tenant.csv')`. That never matches a blocked
# keyword above and contains no "://", so it sailed through untouched while
# DuckDB actually opened and read the file. A single-quoted string is
# DuckDB's string-literal syntax; a double-quoted "identifier" after
# FROM/JOIN is a normal (legitimate) quoted table/column reference and is
# deliberately NOT blocked here.
_REPLACEMENT_SCAN_PATTERN = re.compile(r"\b(from|join)\s*\(*\s*'", re.IGNORECASE)


def _reject_structure(bare: str) -> None:
    if ";" in bare:
        raise ValueError(
            "expression may not contain a second statement "
            "(a bare ';' outside a string literal)"
        )


def validate_sql_expression(expr: str) -> None:
    """Raise ``ValueError`` if ``expr`` references a blocked file/network
    access function or table, contains a second statement, or a bare URI."""
    bare = _strip_quoted(expr)
    _reject_structure(bare)
    if _BLOCKED_PATTERN.search(bare):
        raise ValueError(
            "expression may not reference file/network access functions "
            "(read_csv, read_parquet, ATTACH, COPY, PRAGMA, INSTALL, LOAD, etc.)"
        )
    if _BLOCKED_CALL_PATTERN.search(bare):
        raise ValueError(
            "expression may not call file-reading, introspection or dynamic-SQL "
            "table functions (read_*, *_scan, parquet_*, sniff_csv, query, ...)"
        )
    if "://" in expr:
        raise ValueError("expression may not reference URIs")
    if _REPLACEMENT_SCAN_PATTERN.search(expr):
        raise ValueError(
            "expression may not reference a file path as a table "
            "(e.g. FROM '/path/to/file') -- DuckDB's replacement-scan syntax"
        )
