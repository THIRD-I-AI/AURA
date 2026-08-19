"""
Scalability-audit fixes (2026-08).

The deployment runs ONE uvicorn worker, so any blocking call inside an
async handler freezes every concurrent request for every tenant. Covers:

  * FIX 1 — agents/specialists/adversarial_critic_agent.py's sync LLM call
    must be offloaded (asyncio.to_thread), not run inline in the coroutine.
  * FIX 3 — the three modules that each construct their own SQLite engine
    (api_gateway/persistence.py, metadata_store/db.py, shared/audit_ledger.py)
    must set a busy_timeout per connection, so a concurrent writer
    waits instead of raising "database is locked".
"""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest
import pytest_asyncio

# ── FIX 1 — critic LLM call must not block the event loop ────────────────


class _SlowSyncLLM:
    """Stands in for a sync httpx/OpenAI call that blocks for ~0.5s."""

    def generate(self, prompt, **kwargs):
        time.sleep(0.5)
        return '{"challenges": []}'


@pytest.mark.asyncio
async def test_critic_llm_call_does_not_block_event_loop():
    from agents.base import AgentContext
    from agents.specialists.adversarial_critic_agent import AdversarialCriticAgent

    agent = AdversarialCriticAgent()
    agent.llm = _SlowSyncLLM()  # overrides the plain attribute BaseAgent.__init__ sets
    ctx = AgentContext(
        user_prompt="critique counterfactual",
        task_description="critique",
        upstream_results={
            "estimates": [], "refutations": [], "dag": {}, "treatment": {}, "outcome": {},
        },
    )

    ticks = 0
    stop = False

    async def ticker() -> None:
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker_task = asyncio.create_task(ticker())
    result = await agent.execute(ctx)
    stop = True
    await ticker_task

    assert result.succeeded, result.error
    # ~0.5s of "LLM" time / 10ms tick period == ~50 ticks if the loop stayed
    # live the whole time. A blocked loop services at most the handful of
    # ticks that happened to run before the sync call started.
    assert ticks >= 20, (
        f"only {ticks} ticker iterations ran during the critic call — "
        "the event loop was blocked by a synchronous llm.generate() call"
    )


# ── FIX 3 — SQLite busy_timeout on all three engine hooks ────────────────


async def _pragmas(engine):
    async with engine.connect() as conn:
        mode = (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar()
        timeout = (await conn.exec_driver_sql("PRAGMA busy_timeout")).scalar()
    return mode, timeout


@pytest.mark.asyncio
async def test_gateway_persistence_sqlite_pragmas(tmp_path, monkeypatch):
    from api_gateway import persistence as gw

    db = tmp_path / f"gw_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("GATEWAY_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    gw._engine = None
    gw._session_factory = None
    try:
        engine = gw.get_engine()
        mode, timeout = await _pragmas(engine)
        # journal_mode is deliberately NOT asserted: WAL was tried and reverted.
        # It is a property of the database FILE, not the connection, and its
        # snapshot reads made a just-written row invisible to another connection —
        # which broke two counterfactual tests in full-suite runs (a job lookup
        # 404 surfacing as KeyError: 'state', and a changed artifact hash).
        # busy_timeout alone delivers what was needed: wait, do not raise.
        assert timeout > 0
    finally:
        await gw.close_database()


@pytest.mark.asyncio
async def test_metadata_store_sqlite_pragmas(tmp_path, monkeypatch):
    from metadata_store import db as meta

    db = tmp_path / f"meta_{uuid.uuid4().hex}.db"
    monkeypatch.setattr(meta, "DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    meta._engine = None
    meta._session_factory = None
    engine = meta.get_engine()
    try:
        mode, timeout = await _pragmas(engine)
        # journal_mode is deliberately NOT asserted: WAL was tried and reverted.
        # It is a property of the database FILE, not the connection, and its
        # snapshot reads made a just-written row invisible to another connection —
        # which broke two counterfactual tests in full-suite runs (a job lookup
        # 404 surfacing as KeyError: 'state', and a changed artifact hash).
        # busy_timeout alone delivers what was needed: wait, do not raise.
        assert timeout > 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_audit_ledger_sqlite_pragmas(tmp_path, monkeypatch):
    from shared import audit_ledger as L

    db = tmp_path / f"ledger_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("AURA_LEDGER_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    L._engine = None
    L._session_factory = None
    L._schema_initialized = False
    try:
        engine = L.get_engine()
        mode, timeout = await _pragmas(engine)
        # journal_mode is deliberately NOT asserted: WAL was tried and reverted.
        # It is a property of the database FILE, not the connection, and its
        # snapshot reads made a just-written row invisible to another connection —
        # which broke two counterfactual tests in full-suite runs (a job lookup
        # 404 surfacing as KeyError: 'state', and a changed artifact hash).
        # busy_timeout alone delivers what was needed: wait, do not raise.
        assert timeout > 0
    finally:
        await L.close_database()
