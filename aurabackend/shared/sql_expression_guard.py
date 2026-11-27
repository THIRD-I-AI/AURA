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


# ── BUG-599: parser-based validation (primary). The keyword scan below is a
# fallback for when DuckDB cannot be imported; the locked DuckDB connection is
# the real control. The keyword scan was bypassable -- a quoted function name
# (``"read_text"(...)``), a dollar-quoted string hiding a stacked ``;``/CALL, and
# a no-paren or stored-view introspection call all slipped past it. DuckDB's own
# parser resolves the real function and table names and accepts only a single
# read-only query. ──

import threading

# Refused by DuckDB's own (already-normalised) function name: file/URL readers,
# every ``*_scan``, the sqlite/postgres/mysql scanners, dynamic SQL
# (query/query_table/json_execute_serialized_sql), DuckDB's logging functions
# (BUG-598) and the introspection functions that leak server paths.
_BLOCKED_FUNCTION = re.compile(
    r"^(read_\w+|\w+_scan|parquet_\w+|sniff_csv|st_read\w*|iceberg_\w+|delta_\w+|"
    r"duckdb_\w+|pragma_\w+|sqlite_\w+|postgres_\w+|mysql_\w+|glob|"
    r"query|query_table|json_execute_serialized_sql|getenv|current_setting|"
    r"enable_logging|disable_logging|truncate_duckdb_logs)$",
    re.IGNORECASE,
)

# A table reference that is really a file path (a ``FROM '/x'``/``FROM 'x.csv'``
# replacement scan) or an introspection catalog used without parens (``FROM
# duckdb_databases``, ``FROM pragma_database_list``).
_PATH_LIKE_TABLE = re.compile(
    r"[\\/]|://|^\w:|"
    r"\.(csv|parquet|json|ndjson|duckdb|db|sqlite3?|xlsx?|tsv|txt|parq|gz|zst|arrow|avro)$",
    re.IGNORECASE,
)

_PLACEHOLDER = re.compile(r"\{\{\s*\w+\s*\}\}")

_parse_lock = threading.Lock()
_parse_con = None


class _ParserUnavailable(Exception):
    """DuckDB could not be imported, so the keyword fallback must decide."""


def _parser_connection():
    global _parse_con
    if _parse_con is None:
        import duckdb
        _parse_con = duckdb.connect()
    return _parse_con


def _serialized(sql: str):
    """``sql`` parsed by DuckDB as a JSON tree, or None when it is not a single
    valid read-only query (a second statement, a non-query, or malformed)."""
    import json

    from shared.sql_identifiers import quote_literal

    try:
        with _parse_lock:
            row = _parser_connection().execute(
                "SELECT json_serialize_sql(" + quote_literal(sql) + ")"
            ).fetchone()
    except ImportError as exc:
        raise _ParserUnavailable from exc
    except Exception:
        return None
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return None


def _collect(tree, key):
    out = []

    def walk(node):
        if isinstance(node, dict):
            val = node.get(key)
            if isinstance(val, str):
                out.append(val)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(tree)
    return out


def _single_query_tree(expr: str):
    # json_serialize_sql serialises only read-only query statements (SELECT,
    # TABLE, VALUES, DESCRIBE) and reports an error for a second statement, a
    # non-query (COPY/ATTACH/UPDATE/CREATE/CALL/SET/PRAGMA/...) or anything
    # malformed -- so one clean statement already proves "a single read-only
    # query". A bare expression (a WHERE condition, a computed column) is not a
    # statement, so it is retried wrapped in a scalar SELECT; the newline keeps a
    # trailing ``--`` line comment in ``expr`` from eating the wrapper's tail.
    probe = _PLACEHOLDER.sub(" _aura_ph ", expr)
    for candidate in (probe, f"SELECT (\n{probe}\n) AS _aura_guard_col"):
        tree = _serialized(candidate)
        if tree is None or tree.get("error"):
            continue
        if len(tree.get("statements") or []) == 1:
            return tree
    return None


def _validate_with_parser(expr: str) -> None:
    # Structural checks first, so a comment, an unterminated quote or a stacked ';'
    # keep their established messages (BUG-311/BUG-220) even though the parser would
    # reject them too. These need no DuckDB.
    bare = _strip_quoted(expr)
    _reject_structure(bare)
    tree = _single_query_tree(expr)
    if tree is None:
        raise ValueError(
            "expression must be a single read-only query (no second statement, "
            "no COPY/ATTACH/CALL/PRAGMA, no unbalanced parentheses)"
        )
    # The offending name is not echoed: a replacement-scan "table" is the caller's own
    # file path, which must never come back in an error (BUG-189/BUG-284).
    for name in _collect(tree, "function_name"):
        if _BLOCKED_FUNCTION.match(name.strip()):
            raise ValueError(
                "expression may not call file/network, scanner, dynamic-SQL, "
                "logging or introspection functions"
            )
    for table in _collect(tree, "table_name"):
        if _PATH_LIKE_TABLE.search(table) or _BLOCKED_FUNCTION.match(table.strip()):
            raise ValueError(
                "expression may not reference a file path or an introspection "
                "catalog as a table (a replacement scan, e.g. FROM '/path')"
            )


def _validate_with_keywords(expr: str) -> None:
    """Keyword/structure fallback used only when ``_validate_with_parser`` raised
    ``_ParserUnavailable`` (DuckDB not importable). A blocklist, so weaker than the
    parser -- the locked DuckDB connection is the real control."""
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


def validate_sql_expression(expr: str) -> None:
    """Raise ``ValueError`` unless ``expr`` is a single read-only query that calls
    no file/network, scanner, dynamic-SQL, logging or introspection function and
    references no file-path table (BUG-599). DuckDB's own parser decides; the
    keyword scan is only a fallback for when DuckDB cannot be imported."""
    try:
        _validate_with_parser(expr)
    except _ParserUnavailable:
        _validate_with_keywords(expr)
