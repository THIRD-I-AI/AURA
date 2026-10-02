"""BUG-262 / BUG-272: approving or rejecting a held recovery.

262: the decision was recorded under whatever name the request body gave, so any caller
could sign it as someone else.
272: the status check and the write were separate steps across awaits, so two concurrent
approvals both passed and the shim was deployed twice.

Uses the same isolated metadata DB as tests/test_uasr_approval_reaper.py."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid

import pytest
from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
from uasr import service  # noqa: E402
from uasr.db import get_session_factory, init_uasr_db  # noqa: E402
from uasr.models import DriftEvent, RecoveryRecord, RecoveryStatus  # noqa: E402

SHIM = "def transform(rows):\n    return rows\n"


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_uasr_approve_{uuid.uuid4().hex[:8]}.db")
    _metadata_db.DATABASE_URL = f"sqlite+aiosqlite:///{tmp_path}"
    _metadata_db._engine = None
    _metadata_db._session_factory = None
    yield
    leaked = _metadata_db._engine  # BUG-008: dispose before restoring, or pytest hangs at exit
    if leaked is not None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(leaked.dispose())
        finally:
            loop.close()
    _metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory = original
    try:
        os.remove(tmp_path)
    except OSError:
        pass


@pytest.fixture(autouse=True)
async def _tables():
    await init_uasr_db()
    yield


def _request(user: dict | None) -> Request:
    req = Request({"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/x"})
    if user is not None:
        req.state.user = user
    return req


async def _pending() -> tuple[str, str]:
    rec_id, source = f"ap_{uuid.uuid4().hex[:8]}", f"src_{uuid.uuid4().hex[:6]}"
    async with get_session_factory()() as db:
        db.add(DriftEvent(id=rec_id + "_drift", source_id=source, drift_type="schema"))
        db.add(RecoveryRecord(id=rec_id, drift_event_id=rec_id + "_drift", source_id=source,
                              status=RecoveryStatus.PENDING_APPROVAL.value, shim_code=SHIM))
        await db.commit()
    return rec_id, source


async def _approve(rec_id: str, user: dict | None, approver: str = "someone-else@victim.test"):
    async with get_session_factory()() as db:
        return await service.approve_recovery(
            rec_id, service.ApprovalRequest(approver=approver), _request(user), db=db)


@pytest.mark.asyncio
async def test_the_decision_is_recorded_under_the_authenticated_caller_not_the_body():
    rec_id, _ = await _pending()
    out = await _approve(rec_id, {"sub": "u-42", "email": "ops@acme.test", "org_id": "acme"})
    assert out["recovery"]["decided_by"] == "ops@acme.test"

    rejected_id, _ = await _pending()
    async with get_session_factory()() as db:
        out = await service.reject_recovery(
            rejected_id, service.RejectionRequest(approver="ceo@victim.test", reason="no"),
            _request({"sub": "u-42"}), db=db)
    assert out["recovery"]["decided_by"] == "u-42"


@pytest.mark.asyncio
async def test_without_authentication_the_body_value_is_still_used():
    rec_id, _ = await _pending()
    out = await _approve(rec_id, None, approver="local-dev")
    assert out["recovery"]["decided_by"] == "local-dev"


@pytest.mark.asyncio
async def test_concurrent_approvals_deploy_the_shim_once():
    rec_id, source = await _pending()
    before = len(service._loop.get_deployed_shims(source))

    results = await asyncio.gather(
        *[_approve(rec_id, {"sub": f"u{i}"}) for i in range(5)], return_exceptions=True)

    wins = [r for r in results if isinstance(r, dict)]
    conflicts = [r for r in results if isinstance(r, HTTPException) and r.status_code == 409]
    assert len(wins) == 1 and len(conflicts) == 4, results
    assert len(service._loop.get_deployed_shims(source)) == before + 1


@pytest.mark.asyncio
async def test_unknown_id_is_404_and_an_already_decided_one_is_409():
    with pytest.raises(HTTPException) as exc:
        await _approve("does-not-exist", {"sub": "u1"})
    assert exc.value.status_code == 404

    rec_id, _ = await _pending()
    await _approve(rec_id, {"sub": "u1"})
    with pytest.raises(HTTPException) as exc:
        await _approve(rec_id, {"sub": "u2"})
    assert exc.value.status_code == 409
