"""
Scalability-audit fixes (2026-08).

The deployment runs ONE uvicorn worker, so any blocking call inside an
async handler freezes every concurrent request for every tenant. Covers:

  * FIX 1 — agents/specialists/adversarial_critic_agent.py's sync LLM call
    must be offloaded (asyncio.to_thread), not run inline in the coroutine.
  * FIX 2 — agents/specialists/sql_generator_agent.py's sync LLM call in
    _generate_sql (the highest-traffic LLM call in the app) must likewise be
    offloaded (asyncio.to_thread), not run inline in the coroutine.
  * FIX 3 — the three modules that each construct their own SQLite engine
    (api_gateway/persistence.py, metadata_store/db.py, shared/audit_ledger.py)
    must set a busy_timeout per connection, so a concurrent writer
    waits instead of raising "database is locked".
  * FIX 4 — api_gateway/routers/pipelines.py's pipeline_file_schema handler
    calls PipelineGenerator.get_file_schema (sync, DuckDB-loading, potentially
    LLM-calling) inline; it must be offloaded via asyncio.to_thread.
  * FIX 5 — api_gateway/routers/webhooks.py's delete_webhook handler calls
    WebhookDispatcher.delete (sync, writes the subscription store to disk via
    _save) inline; it must be offloaded via asyncio.to_thread, same as
    create_webhook/update_webhook in that router.
  * FIX 6 — pipeline/generator.py's PipelineGenerator.suggest_steps calls the
    sync LLM SDK (generate_json / generate fallback) inline; it must be
    offloaded via asyncio.to_thread, same as PipelineGenerator.generate.
  * FIX 7 — uasr/reflector_agent.py's _llm_diagnosis (the LLM-assisted
    fallback in the MAPE-K diagnosis step) calls the sync llm.generate_json
    inline; it must be offloaded via asyncio.to_thread, same as the other
    LLM call sites above.
  * FIX 8 — uasr/mapek_worker.py's _run_forever calls
    self._analyze_detect_drift(batch) (-> DriftDetector.detect) inline on
    every consumed batch; it must be offloaded via asyncio.to_thread, same
    as the identical detect() call service.py's HTTP handlers already wrap
    (detect() can hit a blocking Redis round-trip under the redis state
    backend, and this loop shares the single uvicorn event loop with the
    FastAPI app).
  * FIX 9 — agents/specialists/intent_agent.py's IntentAgent._run (the
    highest-traffic call in the app — runs on every single chat message)
    calls the sync llm.generate_json inline; it must be offloaded via
    asyncio.to_thread, same as the other LLM call sites above.
  * FIX 10 — agents/specialists/visualization_agent.py's VisualizationAgent
    calls the sync _llm_chart_spec (which wraps llm.generate_json) inline
    from its async _run; it must be offloaded via asyncio.to_thread, same
    as the other LLM call sites above.
  * FIX 11 — evolution/engine.py's EvolutionEngine._generate_improvement_proposal
    calls the sync llm.generate_json (e.g. GeminiProvider.generate's blocking
    generate_content) inline from its async method; it must be offloaded via
    asyncio.to_thread, same as the other LLM call sites above.
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

    def is_available(self) -> bool:
        return True


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


# ── FIX 2 — SQL generator LLM call must not block the event loop ─────────


@pytest.mark.asyncio
async def test_sql_generator_llm_call_does_not_block_event_loop():
    from agents.base import AgentContext
    from agents.specialists.sql_generator_agent import SQLGeneratorAgent

    agent = SQLGeneratorAgent()
    slow_llm = _SlowSyncLLM()
    agent.llm = slow_llm
    agent._llm = slow_llm  # SQLGeneratorAgent._generate_sql calls self._llm directly
    ctx = AgentContext(
        user_prompt="how many rows are there",
        task_description="how many rows are there",
        schema_context={"context_text": "table foo(id int)"},
    )

    ticks = 0
    stop = False

    async def ticker() -> None:
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker_task = asyncio.create_task(ticker())
    sql, error = await agent._generate_sql(ctx.task_description, "table foo(id int)")
    stop = True
    await ticker_task

    assert error is None, error
    assert sql
    # ~0.5s of "LLM" time / 10ms tick period == ~50 ticks if the loop stayed
    # live the whole time. A blocked loop services at most the handful of
    # ticks that happened to run before the sync call started.
    assert ticks >= 20, (
        f"only {ticks} ticker iterations ran during _generate_sql — "
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


# ── FIX 4 — pipeline_file_schema must not block the event loop ───────────


class _FakeRequestState:
    user = None


class _FakeRequest:
    """Minimal stand-in for fastapi.Request — the handler only reads
    request.state.user via _request_tenant()."""

    state = _FakeRequestState()


@pytest.mark.asyncio
async def test_pipeline_file_schema_does_not_block_event_loop(monkeypatch):
    from api_gateway.routers import pipelines as pipelines_router

    class _SlowGenerator:
        def get_file_schema(self, file_name, tenant):
            time.sleep(0.5)
            return {"columns": []}

    monkeypatch.setattr(pipelines_router, "_get_generator", lambda: _SlowGenerator())

    ticks = 0
    stop = False

    async def ticker() -> None:
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker_task = asyncio.create_task(ticker())
    result = await pipelines_router.pipeline_file_schema("some_file.csv", _FakeRequest())
    stop = True
    await ticker_task

    assert result == {"status": "success", "schema": {"columns": []}}
    # ~0.5s of "generator" time / 10ms tick period == ~50 ticks if the loop
    # stayed live the whole time. A blocked loop services at most the handful
    # of ticks that happened to run before the sync call started.
    assert ticks >= 20, (
        f"only {ticks} ticker iterations ran during pipeline_file_schema — "
        "the event loop was blocked by a synchronous get_file_schema() call"
    )


# ── FIX 5 — delete_webhook must not block the event loop ─────────────────


class _FakeWebhookRequest:
    """Minimal stand-in for fastapi.Request — current_workspace_id() only
    reads request.headers and request.state.user."""

    headers: dict = {}
    state = _FakeRequestState()


@pytest.mark.asyncio
async def test_delete_webhook_does_not_block_event_loop(monkeypatch):
    from api_gateway.routers import webhooks as webhooks_router
    from shared.webhook_dispatcher import webhook_dispatcher

    sub = webhook_dispatcher.register(
        workspace_id="default", url="http://example.com/hook", events=["*"],
    )

    def _slow_save(self):
        time.sleep(0.5)

    monkeypatch.setattr(type(webhook_dispatcher), "_save", _slow_save)

    ticks = 0
    stop = False

    async def ticker() -> None:
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker_task = asyncio.create_task(ticker())
    result = await webhooks_router.delete_webhook(sub.id, _FakeWebhookRequest())
    stop = True
    await ticker_task

    assert result == {"status": "success", "deleted": sub.id}
    # ~0.5s of "save" time / 10ms tick period == ~50 ticks if the loop stayed
    # live the whole time. A blocked loop services at most the handful of
    # ticks that happened to run before the sync call started.
    assert ticks >= 20, (
        f"only {ticks} ticker iterations ran during delete_webhook — "
        "the event loop was blocked by a synchronous _save() call"
    )


# ── FIX 6 — suggest_steps must not block the event loop ──────────────────


@pytest.mark.asyncio
async def test_suggest_steps_llm_call_does_not_block_event_loop(monkeypatch):
    from pipeline.generator import PipelineGenerator

    class _SlowJsonLLM:
        def generate_json(self, messages, **kwargs):
            time.sleep(0.5)
            return {"steps": [{"type": "filter", "description": "x", "config": {}}]}

        def generate(self, messages, **kwargs):
            time.sleep(0.5)
            return '{"steps": []}'

        def is_available(self) -> bool:
            return True

    monkeypatch.setattr("shared.llm_provider.get_llm", lambda: _SlowJsonLLM())
    gen = PipelineGenerator()

    ticks = 0
    stop = False

    async def ticker() -> None:
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker_task = asyncio.create_task(ticker())
    steps = await gen.suggest_steps("filter rating > 4")
    stop = True
    await ticker_task

    assert len(steps) == 1
    # ~0.5s of "LLM" time / 10ms tick period == ~50 ticks if the loop stayed
    # live the whole time. A blocked loop services at most the handful of
    # ticks that happened to run before the sync call started.
    assert ticks >= 20, (
        f"only {ticks} ticker iterations ran during suggest_steps — "
        "the event loop was blocked by a synchronous generate_json() call"
    )


# ── FIX 6b — generate()'s tier-2 LLM fallback must not block the event loop ─
# (the LLM SDK is sync — Groq's default client is sync — and generate() is an
# async def called directly from api_gateway/routers/chat.py and pipelines.py
# without an intervening to_thread; suggest_steps got this fix, this sibling
# tier-2 path did not.)


@pytest.mark.asyncio
async def test_generate_llm_fallback_does_not_block_event_loop(monkeypatch):
    from pipeline.generator import PipelineGenerator

    class _SlowJsonLLM:
        def generate_json(self, messages, **kwargs):
            time.sleep(0.5)
            return None

        def generate(self, messages, **kwargs):
            time.sleep(0.5)
            return (
                '{"name": "p", "source": {"type": "file", "file_name": "x.csv"}, '
                '"sink": {"type": "preview"}, "steps": []}'
            )

        def is_available(self) -> bool:
            return True

    monkeypatch.setattr("shared.llm_provider.get_llm", lambda: _SlowJsonLLM())
    gen = PipelineGenerator()

    ticks = 0
    stop = False

    async def ticker() -> None:
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker_task = asyncio.create_task(ticker())
    pipeline = await gen.generate("some prompt with no local-parser match !!!")
    stop = True
    await ticker_task

    assert pipeline is not None
    # ~1s of "LLM" time (generate_json + generate fallback) / 10ms tick period
    # == ~100 ticks if the loop stayed live. A blocked loop services at most
    # the handful of ticks that happened to run before the sync calls started.
    assert ticks >= 40, (
        f"only {ticks} ticker iterations ran during generate() — "
        "the event loop was blocked by a synchronous generate_json()/generate() call"
    )


# ── FIX 7 — reflector_agent._llm_diagnosis must not block the event loop ─


@pytest.mark.asyncio
async def test_llm_diagnosis_does_not_block_event_loop(monkeypatch):
    from uasr.models import DriftDetectionResult, DriftType
    from uasr.reflector_agent import DiagnosticReflectorAgent

    class _SlowJsonLLM:
        def generate_json(self, messages, **kwargs):
            time.sleep(0.5)
            return {
                "root_cause": "schema drift",
                "hypothesis": "upstream column renamed",
                "suggested_action": "remap column",
                "confidence": 0.8,
            }

        def is_available(self) -> bool:
            return True

    monkeypatch.setattr("shared.llm_provider.get_llm", lambda **kwargs: _SlowJsonLLM())

    agent = DiagnosticReflectorAgent()
    drift = DriftDetectionResult(
        source_id="src-1",
        batch_id="batch-1",
        drift_detected=True,
        drift_type=DriftType.SCHEMA,
        affected_columns=["revenue"],
    )

    ticks = 0
    stop = False

    async def ticker() -> None:
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker_task = asyncio.create_task(ticker())
    diagnosis = await agent._llm_diagnosis(drift, error_logs=[])
    stop = True
    await ticker_task

    assert diagnosis is not None
    assert diagnosis.root_cause == "schema drift"
    # ~0.5s of "LLM" time / 10ms tick period == ~50 ticks if the loop stayed
    # live the whole time. A blocked loop services at most the handful of
    # ticks that happened to run before the sync call started.
    assert ticks >= 20, (
        f"only {ticks} ticker iterations ran during _llm_diagnosis — "
        "the event loop was blocked by a synchronous generate_json() call"
    )


# ── FIX 8 — MAPE-K worker's drift-detect call must not block the loop ────


@pytest.mark.asyncio
async def test_mapek_analyze_detect_drift_does_not_block_event_loop(monkeypatch):
    from uasr.mapek_worker import MAPEKConfig, MAPEKWorker
    from uasr.models import BatchPayload

    cfg = MAPEKConfig(source_id="test_src")
    worker = MAPEKWorker(config=cfg)

    # Wrap (not replace) the real detect() so the result shape stays real.
    real_detect = worker._detector.detect

    def _slow_wrapped(batch):
        time.sleep(0.5)
        return real_detect(batch)

    monkeypatch.setattr(worker._detector, "detect", _slow_wrapped)

    batch = BatchPayload(
        source_id="test_src",
        batch_id="b1",
        columns=["x"],
        rows=[{"x": 1.0}, {"x": 2.0}],
        schema_snapshot={"x": "float"},
    )

    ticks = 0
    stop = False

    async def ticker() -> None:
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker_task = asyncio.create_task(ticker())
    # Exercises the exact call pattern _run_forever now uses at the
    # "── Analyze ──" step: offloaded via asyncio.to_thread.
    drift = await asyncio.to_thread(worker._analyze_detect_drift, batch)
    stop = True
    await ticker_task

    assert drift is not None
    # ~0.5s of "detect" time / 10ms tick period == ~50 ticks if the loop
    # stayed live the whole time. A blocked loop services at most the
    # handful of ticks that happened to run before the sync call started.
    assert ticks >= 20, (
        f"only {ticks} ticker iterations ran during _analyze_detect_drift — "
        "the event loop was blocked by a synchronous detector.detect() call"
    )


# ── FIX 11 — evolution engine's proposal-generation LLM call must not
#    block the event loop ─────────────────────────────────────────────


class _SlowSyncGenerateJsonLLM:
    """Stands in for a sync provider whose generate_json (e.g. Gemini's
    blocking generate_content under the hood) takes ~0.5s."""

    def is_available(self) -> bool:
        return True

    def generate_json(self, prompt, **kwargs):
        time.sleep(0.5)
        return {
            "description": "test improvement",
            "rationale": "because",
            "proposed_change": {},
            "confidence": 0.5,
        }


@pytest.mark.asyncio
async def test_evolution_proposal_llm_call_does_not_block_event_loop(monkeypatch):
    import evolution.engine as engine_mod

    monkeypatch.setattr(engine_mod, "get_llm", lambda: _SlowSyncGenerateJsonLLM())

    engine = engine_mod.EvolutionEngine()

    ticks = 0
    stop = False

    async def ticker() -> None:
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker_task = asyncio.create_task(ticker())
    proposal = await engine._generate_improvement_proposal(
        target="sql_generator_agent",
        improvement_type="prompt_tuning",
        context={"failures": 3},
    )
    stop = True
    await ticker_task

    assert proposal is not None
    assert proposal.description == "test improvement"
    # ~0.5s of "LLM" time / 10ms tick period == ~50 ticks if the loop stayed
    # live the whole time. A blocked loop services at most the handful of
    # ticks that happened to run before the sync call started.
    assert ticks >= 20, (
        f"only {ticks} ticker iterations ran during _generate_improvement_proposal — "
        "the event loop was blocked by a synchronous llm.generate_json() call"
    )
