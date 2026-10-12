"""BUG-206: the audit trail must record who made the request."""
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app(monkeypatch):
    import shared.audit_log as audit_log
    from shared.middleware import AuditLogMiddleware, JWTAuthMiddleware

    calls = []
    monkeypatch.setattr(audit_log, "AUDIT_ENABLED", True)
    monkeypatch.setattr(audit_log, "audit_request", lambda **kw: calls.append(kw))

    app = FastAPI()

    @app.get("/api/v1/things")
    def things():
        return {"ok": True}

    # same order as service_factory: JWT gate first, audit added after (so it is outermost)
    app.add_middleware(JWTAuthMiddleware)
    app.add_middleware(AuditLogMiddleware)
    return app, calls


def test_audit_entry_carries_the_authenticated_user(monkeypatch):
    from shared.auth import create_access_token

    app, calls = _app(monkeypatch)
    token = create_access_token({"sub": "user-42", "email": "u@example.com", "role": "user"})
    resp = TestClient(app).get("/api/v1/things", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert [c["user"] for c in calls] == ["user-42"]


def test_unauthenticated_request_is_audited_with_no_user(monkeypatch):
    app, calls = _app(monkeypatch)
    resp = TestClient(app).get("/api/v1/things")
    assert resp.status_code == 401
    assert [c["user"] for c in calls] == [""]


def test_the_audit_write_does_not_run_on_the_event_loop(monkeypatch):
    """BUG-315: the append (lock + write + fsync) ran inline in dispatch, stalling the
    one event loop for every request."""
    import asyncio
    import threading

    import shared.audit_log as audit_log
    from shared.middleware import AuditLogMiddleware

    seen = {}
    monkeypatch.setattr(audit_log, "AUDIT_ENABLED", True)
    monkeypatch.setattr(audit_log, "audit_request",
                        lambda **kw: seen.update(thread=threading.get_ident(), **kw))

    app = FastAPI()

    @app.get("/api/v1/things")
    async def things():
        seen["loop_thread"] = threading.get_ident()
        seen["loop"] = asyncio.get_running_loop()
        return {"ok": True}

    app.add_middleware(AuditLogMiddleware)
    resp = TestClient(app).get("/api/v1/things")

    assert resp.status_code == 200
    assert seen["path"] == "/api/v1/things" and seen["status"] == 200
    assert seen["thread"] != seen["loop_thread"]


def test_a_request_that_crashes_is_still_audited(monkeypatch):
    # BUG-412: an unhandled exception skipped the audit write entirely, so the 500s an
    # audit trail most needs were missing from it.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from shared import audit_log
    from shared.middleware import AuditLogMiddleware

    calls = []
    monkeypatch.setattr(audit_log, "AUDIT_ENABLED", True)
    monkeypatch.setattr(audit_log, "audit_request", lambda **kw: calls.append(kw))
    app = FastAPI()
    app.add_middleware(AuditLogMiddleware)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("unexpected")

    resp = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert resp.status_code == 500
    assert [(c["path"], c["status"]) for c in calls] == [("/boom", 500)]


def test_a_throttled_request_is_not_given_an_audit_record(monkeypatch):
    # BUG-413: throttled requests are unbounded in number; each used to cost a locked,
    # fsync'd audit record.
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    from shared import audit_log
    from shared.middleware import AuditLogMiddleware

    calls = []
    monkeypatch.setattr(audit_log, "AUDIT_ENABLED", True)
    monkeypatch.setattr(audit_log, "audit_request", lambda **kw: calls.append(kw))
    app = FastAPI()
    app.add_middleware(AuditLogMiddleware)

    @app.get("/limited")
    async def limited():
        return JSONResponse({"detail": "slow down"}, status_code=429)

    assert TestClient(app).get("/limited").status_code == 429
    assert calls == []
