"""
Sec-4 — RateLimitMiddleware X-Forwarded-For opt-in (unit tests).

XFF is spoofable by any client. The middleware must honour it only
when explicitly opted in via the ``trust_forwarded_for`` constructor
flag (driven by ``AURA_TRUST_FORWARDED_FOR`` in production).

These tests exercise ``_client_ip`` directly with a fake Request rather
than going through the HTTP stack — the IP-extraction logic is purely a
function of headers + client.host and doesn't need a full FastAPI route
to verify.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fake_request(*, forwarded: str | None, client_host: str = "10.0.0.1") -> object:
    """Minimal duck-typed Request — just enough for ``_client_ip``."""
    headers = {}
    if forwarded is not None:
        headers["X-Forwarded-For"] = forwarded
    return SimpleNamespace(
        headers=headers,
        client=SimpleNamespace(host=client_host),
    )


def _build_middleware(*, trust_forwarded_for: bool):
    from shared.middleware import RateLimitMiddleware
    from shared.rate_limit import InMemoryBackend

    # The wrapped-app argument can be any callable for the unit test —
    # we only exercise the IP-extraction method, not the dispatch path.
    async def _noop_app(scope, receive, send):  # pragma: no cover
        pass

    return RateLimitMiddleware(
        app=_noop_app,
        requests_per_window=10,
        window_seconds=60,
        backend=InMemoryBackend(),
        trust_forwarded_for=trust_forwarded_for,
    )


def test_x_forwarded_for_ignored_by_default():
    mw = _build_middleware(trust_forwarded_for=False)
    req = _fake_request(forwarded="1.2.3.4", client_host="10.0.0.1")
    assert mw._client_ip(req) == "10.0.0.1"


def test_x_forwarded_for_honoured_when_opted_in():
    mw = _build_middleware(trust_forwarded_for=True)
    req = _fake_request(forwarded="1.2.3.4", client_host="10.0.0.1")
    assert mw._client_ip(req) == "1.2.3.4"


def test_x_forwarded_for_rightmost_token_used_when_chain_present():
    # BUG-116: XFF is comma-separated when there are multiple proxies in
    # the path, and each proxy in a standard chain APPENDS its own
    # address rather than overwriting the header -- so the leftmost
    # token is whatever the ORIGINAL CLIENT put there, attacker-
    # controlled. This repo's single-reverse-proxy topology means the
    # rightmost token is the one the trusted proxy itself appended, and
    # is the only one safe to use as the rate-limit key.
    mw = _build_middleware(trust_forwarded_for=True)
    req = _fake_request(forwarded="1.2.3.4, 10.0.0.5, 10.0.0.6")
    assert mw._client_ip(req) == "10.0.0.6"


def test_x_forwarded_for_client_cannot_rotate_bucket_via_leftmost_spoofing():
    # The exact exploit BUG-116 describes: an attacker rotates the
    # leftmost (client-controlled) entry on every request while the
    # trusted proxy's own appended entry stays constant -- the
    # rate-limit key must stay pinned to that constant entry.
    mw = _build_middleware(trust_forwarded_for=True)
    req1 = _fake_request(forwarded="1.2.3.4, 10.0.0.9")
    req2 = _fake_request(forwarded="9.9.9.9, 10.0.0.9")
    assert mw._client_ip(req1) == mw._client_ip(req2) == "10.0.0.9"


def test_no_forwarded_header_falls_back_to_client_host():
    mw = _build_middleware(trust_forwarded_for=True)
    req = _fake_request(forwarded=None, client_host="10.0.0.7")
    assert mw._client_ip(req) == "10.0.0.7"


def test_missing_client_returns_unknown():
    mw = _build_middleware(trust_forwarded_for=False)
    req = SimpleNamespace(headers={}, client=None)
    assert mw._client_ip(req) == "unknown"
