"""LIMIT detection for connector queries (BUG-192)."""
import re

# A trailing ``LIMIT n`` / ``LIMIT n OFFSET m`` / ``LIMIT m, n`` clause, optionally
# followed by a semicolon.  Anchored to the end so the word "limit" inside a
# column name, alias or string literal is not mistaken for a row cap.
_TRAILING_LIMIT = re.compile(
    r"\blimit\s+(?:\d+|all)(?:\s*(?:,\s*\d+|offset\s+\d+))?\s*;?\s*$", re.IGNORECASE
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
    """Return ``query`` capped at ``limit`` rows unless it already ends in a LIMIT.

    BUG-438: the cap was concatenated onto the raw text. After a trailing semicolon
    that made two statements, which the drivers reject (and the connectors report as
    zero rows); after a trailing ``--`` comment the cap sat inside the comment and
    was never applied.
    """
    core = _strip_trailing_noise(query)
    return core if has_trailing_limit(core) else f"{core} LIMIT {limit}"
