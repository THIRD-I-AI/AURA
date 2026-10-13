"""Routes declare for themselves that they are reachable without a login.

The auth middleware used to learn which paths were public from a hand-kept list in
``shared/middleware.py``, far from the handlers it described. A handler written as
intentionally unauthenticated stayed behind the login gate until someone remembered
the list -- BUG-005, BUG-016/017, BUG-037, BUG-321 and BUG-432 are all that one
mistake. ``@public_route`` puts the declaration on the handler, and the middleware
asks the application's own route table.

Matching is by the route's compiled path pattern and method, never by prefix:
``/artifacts/{hash}/verify`` being public must not open ``/artifacts/{hash}``.

A public route is exempt from authentication only. It stays behind the rate limiter.
"""
from __future__ import annotations

from typing import Any, Callable, List, NamedTuple, Pattern, Set

_ATTR = "__aura_public_reason__"
_CACHE = "_aura_declared_public_routes"


class PublicRoute(NamedTuple):
    path: str
    methods: Set[str]
    pattern: Pattern[str]
    reason: str


def public_route(reason: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark a route handler as reachable without authentication.

    ``reason`` says what protects the route instead of a login (an unguessable
    token in the path, public key material, an HMAC check, ...). It is required so
    the choice is stated where a reviewer will read it.
    """
    if not reason or not reason.strip():
        raise ValueError("public_route needs a reason: what guards this route instead of a login?")

    def mark(handler: Callable[..., Any]) -> Callable[..., Any]:
        setattr(handler, _ATTR, reason.strip())
        return handler

    return mark


def declared_public_routes(app: Any) -> List[PublicRoute]:
    """Every route of ``app`` whose handler carries ``@public_route``."""
    found: List[PublicRoute] = []
    for route in getattr(app, "routes", []):
        reason = getattr(getattr(route, "endpoint", None), _ATTR, None)
        pattern = getattr(route, "path_regex", None)
        if reason and pattern is not None:
            found.append(PublicRoute(route.path, set(getattr(route, "methods", None) or ()), pattern, reason))
    return found


def is_declared_public(app: Any, method: str, path: str) -> bool:
    """Does ``method path`` match a route that declared itself public?

    The route table is fixed once the application has started, so the list is built
    on first use and kept on ``app.state``.
    """
    state = getattr(app, "state", None)
    routes = getattr(state, _CACHE, None) if state is not None else None
    if routes is None:
        routes = declared_public_routes(app)
        if state is not None:
            setattr(state, _CACHE, routes)
    return any(method in r.methods and r.pattern.match(path) for r in routes)
