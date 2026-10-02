"""BUG-237: the chat 'audit' intent signed a certificate but never appended it to the
tenant's tamper-evident ledger -- an orphan with no ledger proof, never covered by
ledger verification. It also stamped an unauthenticated tenant as the string "None"."""
from __future__ import annotations

import os
import sys
import types
import uuid

import pytest
import pytest_asyncio
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers import chat as chatmod
from shared import audit_ledger as L

TABLES = {"ledger": {"columns": [{"name": "entry_id", "type": "INTEGER"}, {"name": "amount", "type": "DOUBLE"}]}}


@pytest_asyncio.fixture
async def chat_audit_env(tmp_path, monkeypatch):
    # Same isolated ledger + artifact dirs as tests/test_audit_ledger_endpoints.py.
    monkeypatch.setenv("AURA_LEDGER_DATABASE_URL",
                       f"sqlite+aiosqlite:///{tmp_path / f'l_{uuid.uuid4().hex}.db'}")
    monkeypatch.setenv("AURA_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path / "audit"))
    L._engine = None
    L._session_factory = None
    L._schema_initialized = False
    L._tenant_locks.clear()
    await L.init_database()

    async def _schema(con, tenant, use_llm=False):
        con.execute("CREATE TABLE ledger (entry_id INTEGER, amount DOUBLE)")
        con.execute("INSERT INTO ledger SELECT i, 100.0 + i * 37.5 FROM range(60) t(i)")
        return {"tables": TABLES, "context_text": "TABLE ledger(entry_id, amount)", "relationships": []}

    async def _audit_intent(self, ctx):
        return types.SimpleNamespace(succeeded=True, output={"intent": "audit"})

    monkeypatch.setattr(chatmod, "build_schema_context_cached", _schema)
    monkeypatch.setattr(chatmod.IntentAgent, "execute", _audit_intent)
    yield
    await L.close_database()


def _request(user=None) -> Request:
    req = Request({"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/chat"})
    if user is not None:
        req.state.user = user
    return req


@pytest.mark.asyncio
async def test_chat_audit_certificate_is_chained_into_the_tenants_ledger(chat_audit_env):
    resp = await chatmod.chat_endpoint(
        chatmod.ChatRequest(message="audit the ledger table"), _request({"org_id": "orgX", "sub": "u1"}))

    assert resp.status == "AuditCompleted", resp.message
    record_hash = resp.action["record_hash"]
    record = await L.record_for_cert("orgX", record_hash)
    assert record is not None, "the signed certificate has no ledger entry"
    assert record.kind == "financial_audit_completed"
    assert record.subject_id == "ledger" and record.subject_type == "dataset"


@pytest.mark.asyncio
async def test_an_unauthenticated_chat_audit_is_not_stamped_with_tenant_none(chat_audit_env):
    resp = await chatmod.chat_endpoint(chatmod.ChatRequest(message="audit the ledger table"), _request())

    assert resp.status == "AuditCompleted", resp.message
    record_hash = resp.action["record_hash"]
    assert await L.record_for_cert("default", record_hash) is not None
    assert await L.record_for_cert("None", record_hash) is None


@pytest.mark.asyncio
async def test_a_failed_ledger_append_is_an_error_not_a_certificate(chat_audit_env, monkeypatch):
    async def _down(**kwargs):
        raise RuntimeError("ledger database unreachable at 10.0.0.5")

    monkeypatch.setattr(L, "append_audit_with_retry", _down)
    resp = await chatmod.chat_endpoint(
        chatmod.ChatRequest(message="audit the ledger table"), _request({"org_id": "orgX", "sub": "u1"}))

    assert resp.status == "Error"
    assert resp.action is None
    assert "10.0.0.5" not in resp.message
