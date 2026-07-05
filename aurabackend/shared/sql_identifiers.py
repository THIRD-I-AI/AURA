"""Safe SQL identifier + literal quoting.

User-controlled names (CSV column headers, uploaded file names, table stems)
get interpolated into SQL that runs against DuckDB. Splicing them raw lets a
crafted header like ``amt" ; ATTACH ...`` or a filename containing a quote
break out of the identifier/literal and inject SQL. These helpers make that
impossible: identifiers are wrapped in double quotes with embedded ``"``
doubled; string literals are wrapped in single quotes with embedded ``'``
doubled. This is the standard, dialect-portable escaping (DuckDB + Postgres).

Use ``quote_identifier`` for EVERY table/column name and ``quote_literal`` for
EVERY path/value spliced into a SQL string. Never f-string a raw name into SQL.
"""
from __future__ import annotations


def quote_identifier(name: str) -> str:
    """Quote a SQL identifier (table or column name) safely.

    >>> quote_identifier('amount')
    '"amount"'
    >>> quote_identifier('a" ; DROP TABLE t; --')
    '"a"" ; DROP TABLE t; --"'

    A NUL byte is rejected outright — it has no legitimate place in an
    identifier and can truncate the statement in some drivers.
    """
    s = str(name)
    if "\x00" in s:
        raise ValueError("identifier contains a NUL byte")
    return '"' + s.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    """Quote a SQL string literal safely (e.g. a file path for read_csv_auto).

    >>> quote_literal("/data/uploads/default/x.csv")
    "'/data/uploads/default/x.csv'"
    >>> quote_literal("x'); ATTACH 'evil")
    "'x''); ATTACH ''evil'"
    """
    s = str(value)
    if "\x00" in s:
        raise ValueError("literal contains a NUL byte")
    return "'" + s.replace("'", "''") + "'"


__all__ = ["quote_identifier", "quote_literal"]
