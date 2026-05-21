"""
Path-traversal-safe filesystem joins.

Sec-2 #36-#41 fix. Any endpoint that takes a user-supplied filename
component (e.g. `etl_download(filename: str)`) must NEVER do a raw
`base / filename` join. A malicious value like `../../etc/passwd` or
`/etc/passwd` escapes the intended sandbox.

`safe_join` resolves the candidate, then asserts that the resolved
path is still under the resolved base. Returns the resolved Path on
success; raises PathTraversalError otherwise.

The check uses `Path.relative_to` after resolving both sides — this
catches symlink-following too (resolution chases symlinks; comparison
happens in real-path space).
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
        Resolved Path under `base`.

    Raises:
        PathTraversalError on any of the above violations.
    """
    if not user_input or not isinstance(user_input, str):
        raise PathTraversalError("filename must be a non-empty string")
    if os.path.isabs(user_input):
        raise PathTraversalError("filename must be relative, not absolute")
    # Disallow any explicit parent-dir hop even before resolution — purely
    # defensive; .resolve() catches it too, but cheap to fail fast.
    parts = Path(user_input).parts
    if any(p == ".." for p in parts):
        raise PathTraversalError("filename must not contain parent-directory references")

    base_resolved = Path(base).resolve()
    candidate = (base_resolved / user_input).resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError as exc:
        raise PathTraversalError(
            "resolved path escapes the allowed base directory"
        ) from exc
    return candidate
