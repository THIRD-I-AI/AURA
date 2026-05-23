"""
Sec-4 — RateLimitMiddleware X-Forwarded-For opt-in.

The middleware must NOT trust X-Forwarded-For by default (it's
spoofable by any client). It honours the header only when explicitly
opted in via the ``trust_forwarded_for`` constructor flag, which the
service-factory wires up from ``AURA_TRUST_FORWARDED_FOR``.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _build_app(*, trust_forwarded_for: bool):
    from fastapi import FastAPI, Request

    from shared.middleware import RateLimitMiddleware
    from shared.rate_limit import InMemoryBackend

    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        requests_per_window=10,
        window_seconds=60,
        backend=InMemoryBackend(),
        trust_forwarded_for=trust_forwarded_for,
    )

    @app.get("/_ip")
    async def echo_ip(request: Request):
        # Reach into the middleware to test the IP extraction; we mirror
        # what _client_ip does so we can verify the spoof attempt fails.
        # Calling the middleware's private method directly is fine here
        # — the test is specifically about that method's behaviour.
        for mw in app.user_middleware:
            cls = getattr(mw, "cls", None)
            if cls is not None and cls.__name__ == "RateLimitMiddleware":
                # Build a minimal middleware instance just to call the
                # method. The dispatched-app and FastAPI app aren't
                # used by _client_ip.
                inst = cls(
                    app=app,
                    requests_per_window=10,
                    window_seconds=60,
                    backend=InMemoryBackend(),
                    trust_forwarded_for=trust_forwarded_for,
                )
                return {"ip": inst._client_ip(request)}
        return {"ip": None}

    return app


def test_x_forwarded_for_ignored_by_default():
    from fastapi.testclient import TestClient
    app = _build_app(trust_forwarded_for=False)
    client = TestClient(app)
    resp = client.get("/_ip", headers={"X-Forwarded-For": "1.2.3.4"})
    assert resp.status_code == 200
    # Spoofed XFF should NOT be used as the rate-limit key.
    assert resp.json()["ip"] != "1.2.3.4"


def test_x_forwarded_for_honoured_when_opted_in():
    from fastapi.testclient import TestClient
    app = _build_app(trust_forwarded_for=True)
    client = TestClient(app)
    resp = client.get("/_ip", headers={"X-Forwarded-For": "1.2.3.4"})
    assert resp.status_code == 200
    assert resp.json()["ip"] == "1.2.3.4"
