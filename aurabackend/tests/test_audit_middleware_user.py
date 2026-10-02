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
