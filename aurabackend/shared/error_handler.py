"""
Error sanitisation for API responses.

Sec-2 #11-#35 fix. Many router catch blocks were returning
`{"error": str(exc)}` or raising `HTTPException(detail=str(exc))`.
That leaks SQL fragments, file paths, stack-trace-derived module
names, and other internals to unauthenticated clients (CWE-209).

`sanitize_error` logs the full exception detail server-side (so
operators can still diagnose) and returns a generic message for the
API response. Domain errors that derive from `AuraError` are
intentionally safe to expose — their `.message` is a curated string —
so we let those through unchanged.

Usage:
    except Exception as exc:
        return {"status": "error", "error": sanitize_error(
            exc, logger=logger, context="etl execute"
        )}

Or for HTTPException:
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=sanitize_error(exc, logger=logger),
        )
"""
from __future__ import annotations

import logging
from typing import Optional

from shared.exceptions import AuraError

_DEFAULT_FALLBACK = "Internal server error"


def sanitize_error(
    exc: BaseException,
    *,
    logger: Optional[logging.Logger] = None,
    context: str = "",
    fallback: str = _DEFAULT_FALLBACK,
) -> str:
    """
    Log `exc` at error level (with traceback) and return a sanitized
    string safe to send to API callers.

    For `AuraError` subclasses the `.message` is treated as already-
    sanitized (the domain-error author curated it). For any other
    exception type the response is the generic `fallback` — the raw
    `str(exc)` NEVER reaches the response.

    Args:
        exc: the exception that was caught.
        logger: where to record the full traceback. If None, a module
            logger is used so we never silently drop diagnostics.
        context: short human-readable phrase like "etl execute" or
            "save dashboard" — prepended to the log message so the
            operator can find the offending call site quickly.
        fallback: response string for non-domain exceptions.

    Returns:
        A string safe to put in an API response body or HTTP detail
        header.
    """
    log = logger or logging.getLogger("aura.error_handler")
    prefix = f"{context}: " if context else ""
    log.error(
        "%s%s: %s", prefix, exc.__class__.__name__, exc, exc_info=True,
    )
    if isinstance(exc, AuraError):
        return exc.message
    return fallback
