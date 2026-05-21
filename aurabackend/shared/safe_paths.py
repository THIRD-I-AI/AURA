"""
Path-traversal-safe filesystem joins.

Sec-2 #36-#41 fix. Any endpoint that takes a user-supplied filename
component (e.g. `etl_download(filename: str)`) must NEVER do a raw
`base / filename` join. A malicious value like `../../etc/passwd` or
`/etc/passwd` escapes the intended sandbox.

`safe_join` resolves both sides, then asserts containment using
`os.path.commonpath` — the pattern CodeQL's `py/path-injection` query
recognises as a sanitizer. (An equivalent `Path.relative_to` check is
correct but the CodeQL standard model doesn't recognise it; using
`commonpath` here lets the helper's return value clear the taint flow
for every caller.)

Returns the resolved real path on success; raises PathTraversalError
otherwise.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Union


class PathTraversalError(ValueError):
    """Raised when a candidate path resolves outside the allowed base."""


def safe_join(base: Union[str, Path], user_input: str) -> Path:
    """
    Join `user_input` onto `base` and verify the result is still inside
    `base` after resolution. Symlinks are followed.

    Rejects:
      * empty / None user input
      * absolute paths (`/foo`, `C:\\foo`) — the caller already passed the base
      * paths that resolve outside the base (`../../etc/passwd`)

    Args:
        base: trusted root directory.
        user_input: untrusted relative path component(s).

    Returns:
        Resolved real path under `base`.

    Raises:
        PathTraversalError on any of the above violations.
    """
    if not user_input or not isinstance(user_input, str):
        raise PathTraversalError("filename must be a non-empty string")
    if os.path.isabs(user_input):
        raise PathTraversalError("filename must be relative, not absolute")
    # Disallow any explicit parent-dir hop even before resolution — purely
    # defensive; .realpath() catches it too, but cheap to fail fast.
    parts = Path(user_input).parts
    if any(p == ".." for p in parts):
        raise PathTraversalError("filename must not contain parent-directory references")

    # Use os.path.realpath + os.path.commonpath — the canonical
    # CodeQL-recognised sanitizer pattern for py/path-injection. The
    # Path.relative_to alternative is semantically equivalent but the
    # standard model doesn't trace taint through it.
    base_real = os.path.realpath(str(base))
    candidate_real = os.path.realpath(os.path.join(base_real, user_input))
    # commonpath raises ValueError if the two paths are on different
    # drives (Windows); treat that as escape too.
    try:
        common = os.path.commonpath([base_real, candidate_real])
    except ValueError as exc:
        raise PathTraversalError(
            "resolved path escapes the allowed base directory"
        ) from exc
    if common != base_real:
        raise PathTraversalError(
            "resolved path escapes the allowed base directory"
        )
    return Path(candidate_real)
