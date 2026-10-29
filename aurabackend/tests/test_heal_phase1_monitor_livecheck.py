"""Self-healing phase 1: MonitorAgent's UASR auth (BUG-562) and an honest live check (BUG-566).

BUG-562: MonitorAgent posted to UASR with no Authorization, so a JWT-protected UASR answered
401 and the agent, which handled only 200, reported "no drift" for a check that never ran.

BUG-566: scripts/verify_live_deployment.py's uasr_self_heal used the verdict-only /uasr/ingest,
approved its own recovery and never compared a row, so it proved nothing about healing.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from agents.specialists import monitor_agent
from shared.middleware import JWTAuthMiddleware

ROOT = Path(__file__).resolve().parents[2]

# ── BUG-562 ─────────────────────────────────────────────────────────────────


def _jwt_protected_uasr(status: int = 200) -> FastAPI:
    """A UASR stand-in behind the real JWTAuthMiddleware, as UASR runs with AURA_JWT_ENABLED."""
    app = FastAPI()
    app.add_middleware(JWTAuthMiddleware)

    @app.post("/uasr/ingest")
    async def ingest():
        if status != 200:
            return JSONResponse({"error": "boom"}, status_code=status)
        return {"drift_detected": True, "drift_type": "schema", "severity": "low",
                "recovery_id": "r1", "shim_deployed": True}

    return app


def _route_monitor_to(monkeypatch, app: FastAPI) -> None:
    real_client = httpx.AsyncClient
    monkeypatch.setattr(monitor_agent.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.ASGITransport(app=app), **kw))


def _check(batch_rows):
    agent = monitor_agent.MonitorAgent()
    return asyncio.run(agent._check_data_quality([{"source_id": "s1", "rows": batch_rows}], {}))


def test_monitor_drift_check_authenticates_to_a_jwt_protected_uasr(monkeypatch):
    _route_monitor_to(monkeypatch, _jwt_protected_uasr())
    alerts = _check([{"a": 1}, {"a": 2}])
    drift = [a for a in alerts if a["type"] == "data_drift"]
    assert len(drift) == 1 and drift[0]["source_id"] == "s1", alerts


def test_monitor_reports_a_uasr_error_instead_of_no_drift(monkeypatch):
    _route_monitor_to(monkeypatch, _jwt_protected_uasr(status=500))
    alerts = _check([{"a": 1}, {"a": 2}])
    failed = [a for a in alerts if a["type"] == "drift_check_failed"]
    assert len(failed) == 1 and "HTTP 500" in failed[0]["message"], alerts
    assert failed[0]["severity"] == "high"


def test_monitor_reports_an_unreachable_uasr(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(monitor_agent.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(refuse), **kw))
    alerts = _check([{"a": 1}])
    assert [a["type"] for a in alerts] == ["drift_check_failed"], alerts


# ── BUG-566 ─────────────────────────────────────────────────────────────────

BASELINE = [{"user_id": i, "amount": round(10 + i * 0.37, 2), "status": "active"} for i in range(1, 11)]
GROUND_TRUTH = [{**r, "region": "eu" if r["user_id"] % 2 else "us"} for r in BASELINE]


def _load_verify():
    spec = importlib.util.spec_from_file_location("verify_live", ROOT / "scripts/verify_live_deployment.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["verify_live"] = mod  # dataclasses look their module up by name
    spec.loader.exec_module(mod)
    return mod


def _verifier(heal_body):
    """A Verifier on a mock box: records every path it is asked for, answers baseline and heal."""
    mod = _load_verify()
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        assert req.headers.get("Authorization") == "Bearer t"
        if req.url.path == "/api/v1/uasr/baseline":
            return httpx.Response(200, json={"status": "registered"})
        if req.url.path == "/api/v1/uasr/heal":
            sent = json.loads(req.content)
            assert sent["source_id"].startswith("verify_")
            assert sent["rows"] == GROUND_TRUTH, "the check must send exactly the injected fault"
            return httpx.Response(200, json=heal_body)
        # The old check's ingest/approve calls land here.
        return httpx.Response(404, json={"detail": "not found"})

    v = mod.Verifier(base_url="http://box", email="e", password="p")
    v.client = httpx.Client(base_url="http://box", transport=httpx.MockTransport(handler))
    v.token = "t"
    return mod, v, seen


def test_live_check_passes_only_on_ground_truth_rows_and_never_approves():
    mod, v, seen = _verifier({"status": "deployed", "healed": True, "rows": GROUND_TRUTH})
    assert "equal ground truth" in mod.check_uasr_self_heal(v)
    assert seen == ["/api/v1/uasr/baseline", "/api/v1/uasr/heal"]
    assert not any("approve" in p or p.endswith("/ingest") for p in seen)


def test_live_check_fails_when_the_added_column_is_dropped():
    # What today's add-column template shim does (actuator_agent.py): healed=true, column gone.
    dropped = [{k: val for k, val in r.items() if k != "region"} for r in GROUND_TRUTH]
    mod, v, _ = _verifier({"status": "deployed", "healed": True, "rows": dropped})
    with pytest.raises(AssertionError, match="differ from ground truth"):
        mod.check_uasr_self_heal(v)


def test_live_check_fails_honestly_when_not_healed():
    mod, v, _ = _verifier({"status": "pending_approval", "healed": False,
                           "reason": "held for human approval", "rows": GROUND_TRUTH})
    with pytest.raises(AssertionError, match="held for human approval"):
        mod.check_uasr_self_heal(v)


def test_live_check_reports_a_failed_check_through_the_runner():
    mod, v, _ = _verifier({"status": "failed", "healed": False, "reason": "no shim", "rows": GROUND_TRUTH})
    mod.PACE_SECONDS = 0
    result = v.run("uasr_self_heal", lambda: mod.check_uasr_self_heal(v))
    assert result.status == "fail" and "no shim" in result.detail


def test_live_check_against_the_real_uasr_heal_endpoint(tmp_path):
    from starlette.requests import Request

    import metadata_store.db as _metadata_db
    from uasr import service
    from uasr.db import get_session_factory, init_uasr_db

    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    _metadata_db.DATABASE_URL = f"sqlite+aiosqlite:///{tmp_path / 'uasr.db'}"
    _metadata_db._engine = None
    _metadata_db._session_factory = None
    loop = asyncio.new_event_loop()
    responses = {}

    def request() -> Request:
        return Request({"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/x"})

    async def heal(body):
        async with get_session_factory()() as db:
            return await service.heal_batch(service.IngestRequest(**body), request(), db=db)

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if req.url.path == "/api/v1/uasr/baseline":
            out = loop.run_until_complete(service.register_baseline(service.BaselineRequest(**body), request()))
        elif req.url.path == "/api/v1/uasr/heal":
            out = loop.run_until_complete(heal(body))
        else:
            return httpx.Response(404)
        responses[req.url.path] = out = json.loads(json.dumps(out, default=str))
        return httpx.Response(200, json=out)

    try:
        loop.run_until_complete(init_uasr_db())
        mod = _load_verify()
        mod.PACE_SECONDS = 0
        v = mod.Verifier(base_url="http://box", email="e", password="p")
        v.client = httpx.Client(base_url="http://box", transport=httpx.MockTransport(handler))
        v.token = "t"
        result = v.run("uasr_self_heal", lambda: mod.check_uasr_self_heal(v))
        healed = responses["/api/v1/uasr/heal"]
        # The verdict must follow the real response's rows, not its say-so. On 2026-10-29
        # the real add-column shim dropped `region` and answered healed=true -- this check
        # reports that as a FAIL, which is the point of BUG-566.
        truly_healed = healed["healed"] is True and healed["rows"] == GROUND_TRUTH
        assert result.status == ("pass" if truly_healed else "fail"), (result, healed)
        if healed["healed"] is True and not truly_healed:
            assert "differ from ground truth" in result.detail
    finally:
        leaked = _metadata_db._engine
        if leaked is not None:
            loop.run_until_complete(leaked.dispose())
        loop.close()
        _metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory = original
