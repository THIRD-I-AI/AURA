"""Tenant scoping for UASR source ids.

A source id is ``<tenant>::<name>`` (the gateway's connector bridge already builds
it that way: ``current_workspace_id()`` is ``<tenant>`` or ``<tenant>::<folder>``).
UASR itself used to trust whatever source id a caller sent, so one tenant could
read, heal, approve or roll back another tenant's sources (BUG-262, BUG-263,
BUG-264; the same gap as BUG-072).

The rule, applied by every HTTP handler in service.py:

* writes NAMESPACE the id into the caller's tenant instead of rejecting it, so a
  caller that sends a bare ``orders`` keeps working and a caller that sends
  ``otherTenant::orders`` only ever reaches ``<self>::otherTenant::orders``;
* reads and decisions are limited to ids inside the caller's namespace, and
  answer 404 for anything else -- the same answer a missing id gets.

An unauthenticated request (auth disabled: local development, or the in-process
Kafka worker, which has no caller at all) has no tenant and is not scoped.

Import-light on purpose: no FastAPI, no models -- service.py, metrics.py and the
tests can all use it.
"""
from __future__ import annotations

from typing import Any, Optional

SEPARATOR = "::"


def caller_tenant(request: Any) -> Optional[str]:
    """The verified tenant of the request, or None when it carries no identity."""
    user = getattr(getattr(request, "state", None), "user", None)
    if not isinstance(user, dict):
        return None
    tenant = user.get("org_id") or user.get("sub")
    return str(tenant) if tenant else None


def source_tenant(source_id: Optional[str]) -> Optional[str]:
    """The tenant a source id is namespaced under; None for an un-namespaced id."""
    if not source_id or SEPARATOR not in source_id:
        return None
    return source_id.split(SEPARATOR, 1)[0]


def owns_source(tenant: Optional[str], source_id: Optional[str]) -> bool:
    """Whether ``tenant`` may see or act on ``source_id``."""
    if tenant is None:
        return True
    return bool(source_id) and source_id.startswith(tenant + SEPARATOR)


def scoped_source(tenant: Optional[str], source_id: str) -> str:
    """``source_id`` placed inside ``tenant``'s namespace (idempotent)."""
    if tenant is None or owns_source(tenant, source_id):
        return source_id
    return f"{tenant}{SEPARATOR}{source_id}"


def same_tenant(source_a: Optional[str], source_b: Optional[str]) -> bool:
    """Whether two source ids belong to the same tenant (un-namespaced ids form one group)."""
    return source_tenant(source_a) == source_tenant(source_b)
