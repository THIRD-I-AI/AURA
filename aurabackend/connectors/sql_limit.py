"""LIMIT detection for connector queries (BUG-192)."""
import re

# A trailing ``LIMIT n`` / ``LIMIT n OFFSET m`` / ``LIMIT m, n`` clause, optionally
# followed by a semicolon.  Anchored to the end so the word "limit" inside a
# column name, alias or string literal is not mistaken for a row cap.
_TRAILING_LIMIT = re.compile(
    r"\blimit\s+(?:\d+\s*,\s*)?(?P<count>\d+|all)(?:\s*offset\s+\d+)?\s*;?\s*$", re.IGNORECASE
)
# BUG-548: the standard ``FETCH FIRST|NEXT [n] ROW|ROWS ONLY`` clause is a row cap
# too; a LIMIT appended after it is a second limit clause and a syntax error.
# WITH TIES is left out on purpose: it can return more than n rows.
_TRAILING_FETCH = re.compile(
    r"\bfetch\s+(?:first|next)\s+(?:(?P<count>\d+)\s+)?rows?\s+only\s*;?\s*$", re.IGNORECASE
)


def has_trailing_limit(query: str) -> bool:
    return bool(_TRAILING_LIMIT.search(query))


def _outside_quotes(text: str) -> bool:
    return text.count("'") % 2 == 0 and text.count('"') % 2 == 0


def _strip_trailing_noise(query: str) -> str:
    """Drop trailing whitespace, semicolons and comments."""
    while True:
        stripped = query.rstrip().rstrip(";").rstrip()
        if stripped.endswith("*/"):
            start = stripped.rfind("/*")
            if start != -1 and _outside_quotes(stripped[:start]):
                stripped = stripped[:start]
        else:
            line_start = stripped.rfind("\n") + 1
            line = stripped[line_start:]
            pos = line.find("--")
            while pos != -1 and not _outside_quotes(line[:pos]):
                pos = line.find("--", pos + 2)
            if pos != -1:
                stripped = stripped[:line_start + pos]
        if stripped == query:
            return stripped
        query = stripped


def with_row_limit(query: str, limit: int) -> str:
    """Return ``query`` capped at ``limit`` rows.

    BUG-438: the cap was concatenated onto the raw text. After a trailing semicolon
    that made two statements, which the drivers reject (and the connectors report as
    zero rows); after a trailing ``--`` comment the cap sat inside the comment and
    was never applied.

    BUG-545: a trailing LIMIT used to be kept whatever it said, so ``LIMIT ALL`` or
    ``LIMIT 999999999`` lifted the cap and the drivers' fetchall() read the whole
    table into memory. A caller's own LIMIT / FETCH FIRST is kept only when it is
    no larger than ``limit``; otherwise its row count is lowered to ``limit``.
    """
    core = _strip_trailing_noise(query)
    for clause in (_TRAILING_LIMIT, _TRAILING_FETCH):
        match = clause.search(core)
        if match is None:
            continue
        count = match.group("count")
        if count is None:  # FETCH FIRST ROW ONLY: one row
            return core
        if count.lower() != "all" and int(count) <= limit:
            return core
        return f"{core[:match.start('count')]}{limit}{core[match.end('count'):]}"
    return f"{core} LIMIT {limit}"
