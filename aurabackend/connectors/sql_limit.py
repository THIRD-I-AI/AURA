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
