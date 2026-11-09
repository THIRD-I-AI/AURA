"""ADR-002 phase 2b: the heal step of batch pipeline runs (pipeline/engine.py, pipeline/heal.py).

Every source a run loads goes through UASR /uasr/heal as the pipeline's tenant before
any transform sees it; healed rows replace the source rows, quarantined rows go to the
per-pipeline ``_aura_quarantine_<pipeline_id>`` table and never to the sink, and a run
UASR could not check is FAILED with its sink untouched.

The real PipelineEngine with a DuckDB-file source and a DuckDB sink, and the real
/uasr/baseline and /uasr/heal handlers in-process behind the real JWTAuthMiddleware
(as tests/test_heal_phase1_monitor_livecheck.py does), on an isolated SQLite file. The
faults and their ground truth come from the phase-2a benchmark (tests/accuracy). No
FastAPI lifespan is driven and nothing leaves the process.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import duckdb
import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import metadata_store.db as _metadata_db
from pipeline import engine as engine_module
from pipeline import heal as heal_module
from pipeline.engine import DUCKDB_SINK_FILE, PipelineEngine, _typed_arrow_table
from pipeline.models import (
    Pipeline,
    PipelineSink,
    PipelineSource,
    PipelineStatus,
    ProcessingStep,
    SinkType,
    SourceType,
    StepType,
)
from shared.auth import decode_access_token
from shared.middleware import JWTAuthMiddleware
from shared.storage.base import tenant_slug
from tests.accuracy.datasets import make_dataset, sample, strict_equal
from tests.accuracy.faults import build_trial
from uasr import service
from uasr.actuator_agent import SynthesisActuatorAgent
from uasr.db import get_session_factory, init_uasr_db
from uasr.reflector_agent import DiagnosticReflectorAgent

pytestmark = pytest.mark.pipeline_heal

_REAL_CLIENT = httpx.AsyncClient


async def _no_llm(self, *a, **k):
    return None


class _Uasr:
    """The real UASR handlers, recording each call; ``fail_heal_at`` answers the
    n-th /uasr/heal call (0-based) with a 503 instead."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.fail_heal_at: Optional[int] = None
        app = FastAPI()
        app.add_middleware(JWTAuthMiddleware)

        @app.post("/uasr/baseline")
        async def baseline(request: Request):
            body = await request.json()
            self._record("baseline", request, body)
            return await service.register_baseline(service.BaselineRequest(**body), request)

        @app.post("/uasr/heal")
        async def heal(request: Request):
            body = await request.json()
            self._record("heal", request, body)
            if self.fail_heal_at is not None and len(self.heals()) - 1 >= self.fail_heal_at:
                return JSONResponse({"detail": "canary state unavailable"}, status_code=503)
            async with get_session_factory()() as db:
                return await service.heal_batch(service.IngestRequest(**body), request, db=db)

        self.app = app

    def _record(self, path: str, request: Request, body: Dict[str, Any]) -> None:
        self.calls.append({
            "path": path, "tenant": (request.state.user or {}).get("org_id"),
            "source_id": body["source_id"], "rows": len(body.get("rows") or []),
            "metadata": body.get("metadata") or {},
            "authorization": request.headers.get("authorization"),
        })

    def heals(self, source_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return [c for c in self.calls if c["path"] == "heal" and (source_id is None or c["source_id"] == source_id)]


@pytest.fixture
async def uasr(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    monkeypatch.setattr(engine_module, "OUTPUT_DIR", str(tmp_path / "processed"))
    from shared.storage import reset_storage_backend
    reset_storage_backend()
    # No LLM in CI: the proposal path an unproven fault takes would reach the network.
    monkeypatch.setattr(SynthesisActuatorAgent, "_llm_shim", _no_llm)
    monkeypatch.setattr(DiagnosticReflectorAgent, "_llm_diagnosis", _no_llm)
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    _metadata_db.DATABASE_URL = f"sqlite+aiosqlite:///{tmp_path / 'uasr.db'}"
    _metadata_db._engine = None
    _metadata_db._session_factory = None
    await init_uasr_db()

    u = _Uasr()
    monkeypatch.setattr(heal_module.httpx, "AsyncClient",
                        lambda **kw: _REAL_CLIENT(transport=httpx.ASGITransport(app=u.app), **kw))
    yield u
    if _metadata_db._engine is not None:
        await _metadata_db._engine.dispose()  # BUG-008: an undisposed aiosqlite engine hangs pytest
    _metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory = original
    reset_storage_backend()


# ── helpers ────────────────────────────────────────────────────────────────────

def _columns(rows: List[Dict[str, Any]]) -> List[str]:
    return list(dict.fromkeys(k for r in rows for k in r))


def _put_source(tmp_path, tenant: str, name: str, rows: List[Dict[str, Any]], sql: Optional[str] = None) -> None:
    """A tenant's uploaded .duckdb file holding table ``t`` -- built from ``rows``, or by ``sql``."""
    from shared.storage import get_storage_backend

    path = tmp_path / f"build_{uuid.uuid4().hex[:8]}.duckdb"
    con = duckdb.connect(str(path))
    try:
        if sql is not None:
            con.execute(f"CREATE TABLE t AS {sql}")
        else:
            con.register("src_rows", _typed_arrow_table(_columns(rows), rows))
            con.execute("CREATE TABLE t AS SELECT * FROM src_rows")
    finally:
        con.close()
    get_storage_backend().write(tenant, name, path.read_bytes())


def _pipeline(pid: str, source: str = "src.duckdb", steps: Optional[List[ProcessingStep]] = None) -> Pipeline:
    return Pipeline(
        id=pid, name="heal test",
        source=PipelineSource(type=SourceType.DUCKDB, connection={"database": source}, table="t"),
        steps=steps or [],
        sink=PipelineSink(type=SinkType.DUCKDB, table="out"),
    )


def _db_path(tmp_path, tenant: str) -> str:
    return os.path.join(str(tmp_path / "processed"), tenant_slug(tenant), DUCKDB_SINK_FILE)


def _read(tmp_path, tenant: str, table: str) -> Optional[List[Dict[str, Any]]]:
    path = _db_path(tmp_path, tenant)
    if not os.path.exists(path):
        return None
    con = duckdb.connect(path, read_only=True)
    try:
        exists = con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]).fetchone()
        if not exists:
            return None
        cur = con.execute(f'SELECT * FROM "{table}"')
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]
    finally:
        con.close()


def _types(tmp_path, tenant: str, table: str) -> Dict[str, str]:
    con = duckdb.connect(_db_path(tmp_path, tenant), read_only=True)
    try:
        return {r[0]: r[1] for r in con.execute(f'DESCRIBE "{table}"').fetchall()}
    finally:
        con.close()


async def _run(pipeline: Pipeline, tenant: str):
    return await PipelineEngine().execute(pipeline, tenant=tenant)


async def _baseline_then(tmp_path, uasr, tenant: str, baseline: List[Dict[str, Any]], batch: List[Dict[str, Any]]):
    """Run 1 registers the source's baseline; run 2 loads ``batch`` and is healed."""
    pid = f"pipe_{uuid.uuid4().hex[:8]}"
    _put_source(tmp_path, tenant, "src.duckdb", baseline)
    first = await _run(_pipeline(pid), tenant)
    assert first.status == PipelineStatus.SUCCESS, first.error
    _put_source(tmp_path, tenant, "src.duckdb", batch)
    uasr.calls.clear()
    return pid, await _run(_pipeline(pid), tenant)


def _trial(fault: str, seed: int):
    t = build_trial(fault, seed)
    t.snapshot = None  # a pipeline source declares no schema snapshot
    return t


# ── clean data ─────────────────────────────────────────────────────────────────

async def test_clean_run_flows_through_unchanged(tmp_path, uasr):
    ds = make_dataset(11)
    pid, run = await _baseline_then(tmp_path, uasr, "acme", ds.baseline, ds.truth)

    assert run.status == PipelineStatus.SUCCESS, run.error
    src = run.heal.sources[0]
    assert (src.mode, src.rows_in, src.rows_out, src.rows_quarantined, src.repairs, src.alerts) == (
        "healed", len(ds.truth), len(ds.truth), 0, [], [])
    assert strict_equal(_read(tmp_path, "acme", "out"), ds.truth)
    assert _read(tmp_path, "acme", f"_aura_quarantine_{pid}") is None
    assert run.heal.quarantine_table is None


async def test_clean_typed_source_keeps_its_types_and_values(tmp_path, uasr):
    # DATE, TIMESTAMP and DECIMAL travel to UASR as text or numbers, but a page UASR
    # returns unchanged keeps its original DuckDB rows: the sink gets the source exactly.
    sql = ("SELECT range AS id, DATE '2024-01-01' + CAST(range % 300 AS INTEGER) AS day, "
           "TIMESTAMP '2024-01-01 08:00:00' + to_minutes(CAST(range * 7 % 900 AS BIGINT)) AS at, "
           "CAST(10 + (range * 37 % 1000) / 7.0 AS DECIMAL(12, 2)) AS price, "
           "CASE WHEN range % 3 = 0 THEN 'eu' WHEN range % 3 = 1 THEN 'us' ELSE 'apac' END AS region "
           "FROM range({n})")
    tenant, pid = "acme", f"pipe_{uuid.uuid4().hex[:8]}"
    _put_source(tmp_path, tenant, "src.duckdb", [], sql=sql.format(n=900))
    assert (await _run(_pipeline(pid), tenant)).status == PipelineStatus.SUCCESS
    _put_source(tmp_path, tenant, "src.duckdb", [], sql=sql.format(n=600))
    run = await _run(_pipeline(pid), tenant)

    assert run.status == PipelineStatus.SUCCESS, (run.error, run.heal)
    assert run.heal.sources[0].mode == "healed"
    assert _types(tmp_path, tenant, "out") == {
        "id": "BIGINT", "day": "DATE", "at": "TIMESTAMP", "price": "DECIMAL(12,2)", "region": "VARCHAR"}
    con = duckdb.connect()
    expected = con.execute(sql.format(n=600)).fetchall()
    got = [tuple(r.values()) for r in _read(tmp_path, tenant, "out")]
    assert got == expected


async def test_first_run_registers_the_baseline_instead_of_healing(tmp_path, uasr):
    ds = make_dataset(12)
    pid = f"pipe_{uuid.uuid4().hex[:8]}"
    _put_source(tmp_path, "acme", "src.duckdb", ds.baseline)
    run = await _run(_pipeline(pid), "acme")

    assert run.status == PipelineStatus.SUCCESS, run.error
    assert run.heal.sources[0].mode == "baseline_registered"
    assert [c["path"] for c in uasr.calls] == ["baseline"]
    assert uasr.calls[0]["metadata"] == {"complete_run": True, "run_rows": len(ds.baseline)}
    assert strict_equal(_read(tmp_path, "acme", "out"), ds.baseline)
    # The registered baseline is kept: run 2 is healed against it, not re-registered.
    run2 = await _run(_pipeline(pid), "acme")
    assert run2.heal.sources[0].mode == "healed"


async def test_preview_is_not_healed(tmp_path, uasr):
    ds = make_dataset(13)
    _put_source(tmp_path, "acme", "src.duckdb", ds.baseline)
    run = await PipelineEngine().execute(_pipeline(f"pipe_{uuid.uuid4().hex[:8]}"), preview_only=True, tenant="acme")
    assert run.status == PipelineStatus.SUCCESS and run.heal is None
    assert uasr.calls == []


# ── repairs and quarantine ─────────────────────────────────────────────────────

# Seeds whose fault the proven rules repair end to end through a pipeline.
@pytest.mark.parametrize("fault,seed,repair", [
    ("rename", 3, "rename"),
    ("retype_lossless", 1, "retype"),
    ("scale_up", 0, "rescale"),
])
async def test_repaired_source_column_reaches_the_sink_as_ground_truth(tmp_path, uasr, fault, seed, repair):
    t = _trial(fault, seed)
    pid, run = await _baseline_then(tmp_path, uasr, "acme", t.dataset.baseline, t.sent)

    assert run.status == PipelineStatus.SUCCESS, (run.error, run.heal)
    src = run.heal.sources[0]
    assert repair in [r["type"] for r in src.repairs], src
    assert src.rows_quarantined == 0 and all(e is not None for e in t.expected)
    assert strict_equal(_read(tmp_path, "acme", "out"), t.expected)


def _quarantine(tmp_path, tenant: str, pid: str) -> List[Dict[str, Any]]:
    return _read(tmp_path, tenant, f"_aura_quarantine_{pid}") or []


@pytest.mark.parametrize("fault,seed", [("decoy_x7", 0), ("retype_lossy", 2)])
async def test_unprovable_fault_is_quarantined_and_never_reaches_the_sink(tmp_path, uasr, fault, seed):
    t = _trial(fault, seed)
    pid, run = await _baseline_then(tmp_path, uasr, "acme", t.dataset.baseline, t.sent)

    assert run.status == PipelineStatus.DEGRADED, (run.error, run.heal)
    held = _quarantine(tmp_path, "acme", pid)
    sink = _read(tmp_path, "acme", "out")
    must_hold = {i for i, e in enumerate(t.expected) if e is None}
    assert must_hold, "the trial has no unprovable row"

    # Row accounting: every source row is in the sink or in quarantine, exactly once.
    idx = [q["row_index"] for q in held]
    assert len(idx) == len(set(idx)) and len(sink) + len(idx) == len(t.sent)
    # Every row that has no correct value is held aside, unchanged, with its reason.
    assert must_hold <= set(idx)
    for q in held:
        assert q["run_id"] == run.run_id and q["source"] == "source_data" and q["reason"]
        assert q["quarantined_at"] is not None
        assert strict_equal(json.loads(q["original_row"]), t.sent[q["row_index"]])
    # And the sink holds only ground truth, in source order.
    returned = [i for i in range(len(t.sent)) if i not in set(idx)]
    assert strict_equal(sink, [t.expected[i] for i in returned])

    assert run.heal.rows_quarantined == len(idx) == run.heal.sources[0].rows_quarantined
    assert run.heal.quarantine_table == f"_aura_quarantine_{pid}"
    assert run.model_dump()["heal"]["rows_quarantined"] == len(idx)  # surfaced in the run API


async def test_detection_only_fault_alerts_and_degrades_without_holding_rows(tmp_path, uasr):
    t = _trial("null_spike", 0)
    pid, run = await _baseline_then(tmp_path, uasr, "acme", t.dataset.baseline, t.sent)

    assert run.status == PipelineStatus.DEGRADED, (run.error, run.heal)
    assert "null_rate" in {a.get("type") for a in run.heal.sources[0].alerts}
    assert run.heal.alerts >= 1
    assert strict_equal(_read(tmp_path, "acme", "out"), t.expected)


# ── many runs ──────────────────────────────────────────────────────────────────

async def test_many_runs_clean_then_a_standing_rename(tmp_path, uasr):
    # Not just the first run after a baseline: fresh clean loads of the same process
    # raise no alarm run after run, and a rename that persists across runs (the
    # post-heal canary's watched batches) is repaired to ground truth every time.
    t = _trial("rename", 3)
    old = next(k for k in t.expected[0] if k not in t.sent[0])
    new = next(k for k in t.sent[0] if k not in t.expected[0])
    tenant, pid = "acme", f"pipe_{uuid.uuid4().hex[:8]}"
    _put_source(tmp_path, tenant, "src.duckdb", t.dataset.baseline)
    assert (await _run(_pipeline(pid), tenant)).heal.sources[0].mode == "baseline_registered"

    ordinal = len(t.dataset.baseline)
    for k in range(6):
        truth = sample(t.dataset.columns, 300 + 37 * k, random.Random(f"run:{k}"), ordinal)
        ordinal += len(truth)
        sent = truth if k < 3 else [{(new if c == old else c): v for c, v in r.items()} for r in truth]
        _put_source(tmp_path, tenant, "src.duckdb", sent)
        run = await _run(_pipeline(pid), tenant)
        assert run.status == PipelineStatus.SUCCESS, (k, run.error, run.heal)
        assert run.heal.rows_quarantined == 0 and run.heal.alerts == 0, (k, run.heal)
        assert ("rename" in [r["type"] for r in run.heal.sources[0].repairs]) == (k >= 3), (k, run.heal)
        assert strict_equal(_read(tmp_path, tenant, "out"), truth), k


# ── fail closed ────────────────────────────────────────────────────────────────

async def _sink_untouched_after(tmp_path, uasr, break_uasr) -> None:
    ds = make_dataset(14)
    pid = f"pipe_{uuid.uuid4().hex[:8]}"
    _put_source(tmp_path, "acme", "src.duckdb", ds.baseline)
    assert (await _run(_pipeline(pid), "acme")).status == PipelineStatus.SUCCESS
    before = _read(tmp_path, "acme", "out")
    _put_source(tmp_path, "acme", "src.duckdb", ds.truth)
    break_uasr()
    run = await _run(_pipeline(pid), "acme")

    assert run.status == PipelineStatus.FAILED
    assert "UASR" in run.error and "no rows were written" in run.error
    assert _read(tmp_path, "acme", "out") == before
    assert _quarantine(tmp_path, "acme", pid) == []


async def test_unreachable_uasr_fails_the_run_and_leaves_the_sink_untouched(tmp_path, uasr, monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    def down():
        monkeypatch.setattr(heal_module.httpx, "AsyncClient",
                            lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(refuse), **kw))

    await _sink_untouched_after(tmp_path, uasr, down)


async def test_uasr_error_mid_run_fails_the_run_and_leaves_the_sink_untouched(tmp_path, uasr, monkeypatch):
    # Page 0 heals, page 1 is answered 503: no page of the load may reach the sink.
    monkeypatch.setattr(heal_module, "PAGE_MAX_ROWS", 100)

    def fail_second_page():
        uasr.fail_heal_at = 1

    await _sink_untouched_after(tmp_path, uasr, fail_second_page)
    assert len(uasr.heals()) == 2


async def test_heal_answer_that_loses_a_row_fails_closed(tmp_path, uasr, monkeypatch):
    real = service.heal_batch

    async def drops_a_row(req, request, db):
        out = await real(req, request, db=db)
        out["rows"] = out["rows"][1:]
        return out

    monkeypatch.setattr(service, "heal_batch", drops_a_row)
    await _sink_untouched_after(tmp_path, uasr, lambda: None)


async def test_rows_a_failed_standing_shim_left_unproven_fail_closed(tmp_path, uasr, monkeypatch):
    # BUG-271: a deployed shim that raised leaves rows only partly transformed; UASR
    # returns them with healed=false. They were never checked, so none may be written.
    real = service.heal_batch

    async def shim_failed(req, request, db):
        out = await real(req, request, db=db)
        out.update(healed=False, degraded=True, shims_applied=0, standing_shims=1,
                   reason="a deployed shim failed to apply; rows may be only partly transformed")
        return out

    monkeypatch.setattr(service, "heal_batch", shim_failed)
    await _sink_untouched_after(tmp_path, uasr, lambda: None)


# ── tenancy ────────────────────────────────────────────────────────────────────

async def test_source_ids_baselines_and_quarantine_are_per_tenant(tmp_path, uasr):
    t = _trial("decoy_x7", 0)
    pid = f"pipe_{uuid.uuid4().hex[:8]}"
    # Tenant A registers a baseline and then sends a faulted load.
    _put_source(tmp_path, "orgA", "src.duckdb", t.dataset.baseline)
    assert (await _run(_pipeline(pid), "orgA")).heal.sources[0].mode == "baseline_registered"
    # Tenant B runs a pipeline with the same id: it meets no baseline of A's and
    # registers its own, from the very load A's baseline would have flagged.
    _put_source(tmp_path, "orgB", "src.duckdb", t.sent)
    run_b = await _run(_pipeline(pid), "orgB")
    assert run_b.heal.sources[0].mode == "baseline_registered" and run_b.status == PipelineStatus.SUCCESS
    _put_source(tmp_path, "orgA", "src.duckdb", t.sent)
    run_a = await _run(_pipeline(pid), "orgA")
    assert run_a.status == PipelineStatus.DEGRADED

    for call in uasr.calls:
        assert call["source_id"] == f"pipeline:{pid}:source_data"
        assert decode_access_token(call["authorization"][7:])["org_id"] == call["tenant"]
    assert {c["tenant"] for c in uasr.calls} == {"orgA", "orgB"}
    assert _quarantine(tmp_path, "orgA", pid)
    assert _quarantine(tmp_path, "orgB", pid) == []


# ── paging and whole runs ──────────────────────────────────────────────────────

async def test_large_source_is_paged_and_every_row_accounted_for(tmp_path, uasr, monkeypatch):
    monkeypatch.setattr(heal_module, "PAGE_MAX_ROWS", 250)
    ds = make_dataset(15, baseline_rows=1000, batch_rows=900)
    pid, run = await _baseline_then(tmp_path, uasr, "acme", ds.baseline, ds.truth)

    heals = uasr.heals()
    assert len(heals) == 4 and max(c["rows"] for c in heals) <= 250, heals
    assert sum(c["rows"] for c in heals) == len(ds.truth)
    assert run.status == PipelineStatus.SUCCESS, (run.error, run.heal)
    assert run.heal.sources[0].pages == 4
    assert strict_equal(_read(tmp_path, "acme", "out"), ds.truth)


async def test_page_is_bounded_by_cells_for_a_wide_table(tmp_path, uasr, monkeypatch):
    monkeypatch.setattr(heal_module, "PAGE_MAX_CELLS", 1000)
    cols = ", ".join(f"(range * {k} % 97) AS c{k}" for k in range(1, 21))
    tenant, pid = "acme", f"pipe_{uuid.uuid4().hex[:8]}"
    _put_source(tmp_path, tenant, "src.duckdb", [], sql=f"SELECT range AS id, {cols} FROM range(400)")
    await _run(_pipeline(pid), tenant)
    uasr.calls.clear()
    await _run(_pipeline(pid), tenant)
    heals = uasr.heals()
    assert heals and max(c["rows"] for c in heals) <= 1000 // 21
    assert sum(c["rows"] for c in heals) == 400


async def test_complete_run_is_sent_once_per_source_on_its_last_call(tmp_path, uasr, monkeypatch):
    monkeypatch.setattr(heal_module, "PAGE_MAX_ROWS", 200)
    ds = make_dataset(16, baseline_rows=800, batch_rows=700)
    left = ds.columns[0].name
    # The join source's tag alternates row by row: pages strided by rowid once
    # aliased with it (every page all "a" or all "b") and quarantined clean data.
    regions =[{"key": r[left], "tag": "a" if i % 2 else "b"} for i, r in enumerate(ds.truth)]
    tenant, pid = "acme", f"pipe_{uuid.uuid4().hex[:8]}"
    steps = [ProcessingStep(id="j1", type=StepType.JOIN,
                            config={"join_type": "LEFT", "left_key": left, "right_key": "key"},
                            join_source={"type": "duckdb", "connection": {"database": "tags.duckdb"},
                                         "table": "t"})]
    _put_source(tmp_path, tenant, "src.duckdb", ds.baseline)
    _put_source(tmp_path, tenant, "tags.duckdb", regions)
    await _run(_pipeline(pid, steps=steps), tenant)
    _put_source(tmp_path, tenant, "src.duckdb", ds.truth)
    uasr.calls.clear()
    run = await _run(_pipeline(pid, steps=steps), tenant)

    assert run.status == PipelineStatus.SUCCESS, (run.error, run.heal)
    assert [s.source for s in run.heal.sources] == ["source_data", "join_src_j1"]
    for source, n in ((f"pipeline:{pid}:source_data", len(ds.truth)), (f"pipeline:{pid}:join_src_j1", len(regions))):
        heals = uasr.heals(source)
        complete = [i for i, c in enumerate(heals) if c["metadata"].get("complete_run")]
        assert complete == [len(heals) - 1], (source, heals)
        assert heals[-1]["metadata"]["run_rows"] == n
        assert sum(c["rows"] for c in heals) == n


# ── token lifetime (review of 4bd908e) ─────────────────────────────────────────

async def test_a_run_longer_than_the_token_lifetime_still_heals_every_page(tmp_path, uasr, monkeypatch):
    # The tenant token lives 5 minutes and UASR checks it on every call. A token
    # minted once at the start of a run expired part-way through any load whose heal
    # calls took longer, so a large source answered 401 on a later page and the run
    # always FAILED. The clock both mints and checks tokens; each page moves it on
    # past a token's whole lifetime.
    import jwt.api_jwt as jwt_api

    import shared.auth as auth_module

    real_datetime = auth_module.datetime

    class _Shifted(real_datetime):
        offset = timedelta(0)

        @classmethod
        def now(cls, tz=None):
            moved = real_datetime.now(tz) + cls.offset
            return cls.fromtimestamp(moved.timestamp(), tz)

    monkeypatch.setattr(auth_module, "datetime", _Shifted)
    monkeypatch.setattr(jwt_api, "datetime", _Shifted)
    monkeypatch.setattr(heal_module, "PAGE_MAX_ROWS", 100)
    real = service.heal_batch

    async def slow_page(req, request, db):
        out = await real(req, request, db=db)
        _Shifted.offset += timedelta(minutes=6)
        return out

    ds = make_dataset(17, baseline_rows=600, batch_rows=400)
    pid = f"pipe_{uuid.uuid4().hex[:8]}"
    _put_source(tmp_path, "acme", "src.duckdb", ds.baseline)
    assert (await _run(_pipeline(pid), "acme")).status == PipelineStatus.SUCCESS
    _put_source(tmp_path, "acme", "src.duckdb", ds.truth)
    monkeypatch.setattr(service, "heal_batch", slow_page)
    uasr.calls.clear()
    run = await _run(_pipeline(pid), "acme")

    assert run.status == PipelineStatus.SUCCESS, (run.error, run.heal)
    heals = uasr.heals()
    assert len(heals) == 4 and _Shifted.offset >= timedelta(minutes=24)
    assert len({c["authorization"] for c in heals}) == 4  # a fresh token per call
    assert strict_equal(_read(tmp_path, "acme", "out"), ds.truth)


# ── ETL (/etl/execute) ─────────────────────────────────────────────────────────
# ADR-002 phase 2b puts ETL in scope ("scheduled and manual runs, ETL"). /etl/execute
# used to COPY its source straight to the destination file without ever calling UASR.

@pytest.fixture
def etl_tenant():
    """A tenant of its own: /etl/execute writes under aurabackend/data/processed/<tenant>."""
    from api_gateway.routers import etl as etl_router

    tenant = f"etl{uuid.uuid4().hex[:8]}"
    out = Path(etl_router.__file__).resolve().parents[2] / "data" / "processed" / tenant_slug(tenant)
    yield tenant, out
    shutil.rmtree(out, ignore_errors=True)


def _put_parquet(tmp_path, tenant: str, name: str, rows: List[Dict[str, Any]]) -> None:
    from shared.storage import get_storage_backend

    path = tmp_path / f"build_{uuid.uuid4().hex[:8]}.parquet"
    con = duckdb.connect()
    try:
        con.register("src_rows", _typed_arrow_table(_columns(rows), rows))
        con.execute(f"COPY (SELECT * FROM src_rows) TO '{path.as_posix()}' (FORMAT PARQUET)")
    finally:
        con.close()
    get_storage_backend().write(tenant, name, path.read_bytes())


async def _etl(tenant: str, dest: str, preview_only: bool = False) -> Dict[str, Any]:
    """POST /etl/execute through the real JWTAuthMiddleware, as ``tenant``."""
    from api_gateway.routers import etl as etl_router
    from shared.auth import create_access_token

    app = FastAPI()
    app.add_middleware(JWTAuthMiddleware)
    app.include_router(etl_router.router)
    token = create_access_token({"sub": f"user@{tenant}", "org_id": tenant})
    async with _REAL_CLIENT(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as c:
        r = await c.post("/etl/execute", headers={"Authorization": f"Bearer {token}"}, json={
            "source_file": "sales.parquet", "destination_format": "parquet",
            "destination_filename": dest, "preview_only": preview_only})
    assert r.status_code == 200, r.text
    return r.json()


def _parquet_rows(path: Path) -> List[Dict[str, Any]]:
    con = duckdb.connect()
    try:
        cur = con.execute(f"SELECT * FROM read_parquet('{path.as_posix()}')")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]
    finally:
        con.close()


async def _etl_baseline_then(tmp_path, uasr, tenant: str, baseline, batch) -> Dict[str, Any]:
    _put_parquet(tmp_path, tenant, "sales.parquet", baseline)
    first = await _etl(tenant, "first")
    assert first["status"] == "success", first
    assert first["heal"]["sources"][0]["mode"] == "baseline_registered"
    _put_parquet(tmp_path, tenant, "sales.parquet", batch)
    uasr.calls.clear()
    return await _etl(tenant, "second")


async def test_etl_clean_load_is_healed_and_written_unchanged(tmp_path, uasr, etl_tenant):
    tenant, out = etl_tenant
    ds = make_dataset(18)
    body = await _etl_baseline_then(tmp_path, uasr, tenant, ds.baseline, ds.truth)

    assert body["status"] == "success", body
    src = body["heal"]["sources"][0]
    assert (src["mode"], src["source_id"], src["rows_out"]) == ("healed", "etl:sales:sales", len(ds.truth))
    heals = uasr.heals()
    assert heals and all(c["tenant"] == tenant and c["source_id"] == "etl:sales:sales" for c in heals)
    assert sum(c["rows"] for c in heals) == len(ds.truth)
    assert strict_equal(_parquet_rows(out / "second.parquet"), ds.truth)


@pytest.mark.parametrize("fault,seed,repair", [
    ("rename", 3, "rename"),
    ("retype_lossless", 1, "retype"),
    ("scale_up", 0, "rescale"),
])
async def test_etl_repaired_fault_reaches_the_destination_as_ground_truth(tmp_path, uasr, etl_tenant, fault, seed, repair):
    tenant, out = etl_tenant
    t = _trial(fault, seed)
    body = await _etl_baseline_then(tmp_path, uasr, tenant, t.dataset.baseline, t.sent)

    assert body["status"] == "success", body
    src = body["heal"]["sources"][0]
    assert repair in [r["type"] for r in src["repairs"]], src
    assert src["rows_quarantined"] == 0 and all(e is not None for e in t.expected)
    assert strict_equal(_parquet_rows(out / "second.parquet"), t.expected)


async def test_etl_unprovable_rows_are_quarantined_and_never_written(tmp_path, uasr, etl_tenant):
    tenant, out = etl_tenant
    t = _trial("decoy_x7", 0)
    body = await _etl_baseline_then(tmp_path, uasr, tenant, t.dataset.baseline, t.sent)

    assert body["status"] == "degraded", body
    held = _read(tmp_path, tenant, "_aura_quarantine_etl_sales") or []
    written = _parquet_rows(out / "second.parquet")
    idx = [q["row_index"] for q in held]
    must_hold = {i for i, e in enumerate(t.expected) if e is None}
    assert must_hold and must_hold <= set(idx)
    # Every source row is in the destination or in quarantine, exactly once.
    assert len(idx) == len(set(idx)) and len(written) + len(idx) == len(t.sent)
    assert all(q["run_id"] == body["run_id"] and q["source"] == "sales" for q in held)
    returned = [i for i in range(len(t.sent)) if i not in set(idx)]
    assert strict_equal(written, [t.expected[i] for i in returned])
    assert body["heal"]["quarantine_table"] == "_aura_quarantine_etl_sales"
    assert body["heal"]["rows_quarantined"] == len(idx)


async def test_etl_fails_closed_when_uasr_cannot_check_the_load(tmp_path, uasr, etl_tenant):
    tenant, out = etl_tenant
    ds = make_dataset(19)
    uasr.fail_heal_at = 0
    body = await _etl_baseline_then(tmp_path, uasr, tenant, ds.baseline, ds.truth)

    assert body["status"] == "error", body
    assert "UASR" in body["error"] and "no rows were written" in body["error"]
    assert not (out / "second.parquet").exists()
    assert _read(tmp_path, tenant, "_aura_quarantine_etl_sales") is None


async def test_etl_preview_is_not_healed(tmp_path, uasr, etl_tenant):
    tenant, _ = etl_tenant
    _put_parquet(tmp_path, tenant, "sales.parquet", make_dataset(20).baseline)
    body = await _etl(tenant, "preview", preview_only=True)
    assert body["status"] == "success" and body["heal"] is None
    assert uasr.calls == []


# ── inbound hook trigger ───────────────────────────────────────────────────────

async def test_inbound_hook_run_is_healed_as_the_pipelines_tenant(tmp_path, uasr, monkeypatch):
    # A pipeline fired by an inbound hook (api_gateway/routers/inbound_hooks.py) runs
    # in the background with no request: it must still heal its source as the
    # pipeline's own tenant, and a repaired column reaches the sink as ground truth.
    import api_gateway.persistence as persistence
    import shared.tasks as tasks
    from api_gateway.routers import inbound_hooks

    t = _trial("rename", 3)
    tenant, pid = "acme", f"pipe_{uuid.uuid4().hex[:8]}"
    _put_source(tmp_path, tenant, "src.duckdb", t.dataset.baseline)
    assert (await _run(_pipeline(pid), tenant)).heal.sources[0].mode == "baseline_registered"
    _put_source(tmp_path, tenant, "src.duckdb", t.sent)
    uasr.calls.clear()

    async def get_pipeline(pipeline_id, workspace_id=None):
        assert (pipeline_id, workspace_id) == (pid, tenant)
        return _pipeline(pid).model_dump()

    async def get_pipeline_workspace_id(pipeline_id):
        return tenant

    fired = []
    monkeypatch.setattr(persistence, "get_pipeline", get_pipeline)
    monkeypatch.setattr(persistence, "get_pipeline_workspace_id", get_pipeline_workspace_id)
    monkeypatch.setattr(tasks, "fire_and_forget", lambda coro, name=None: fired.append(coro))
    answer = await inbound_hooks._fire_pipeline(pid, {}, tenant)
    assert answer["status"] == "success" and len(fired) == 1
    await fired[0]

    heals = uasr.heals()
    assert heals and {c["tenant"] for c in heals} == {tenant}
    assert {c["source_id"] for c in heals} == {f"pipeline:{pid}:source_data"}
    assert strict_equal(_read(tmp_path, tenant, "out"), t.expected)


# ── row identity ───────────────────────────────────────────────────────────────

def _drop_first_of_each_page(rewrite: bool):
    """A UASR stand-in that holds the first row of each page aside (and, with
    ``rewrite``, reports a repair so the page is rebuilt from its answer)."""
    sent: List[List[Dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/uasr/baseline":
            return httpx.Response(200, json={"status": "exists"})
        rows = body["rows"]
        sent.append(rows)
        return httpx.Response(200, json={
            "healed": True, "rows": rows[1:], "alerts": [],
            "quarantined_rows": [{"index": 0, "reason": "held", "row": rows[0]}],
            "repairs": [{"type": "rescale", "column": "v"}] if rewrite else [],
        })

    return handler, sent


@pytest.mark.parametrize("column", ["rowid", "RowID"])
@pytest.mark.parametrize("rewrite", [False, True])
async def test_a_source_column_named_rowid_does_not_lose_or_double_rows(monkeypatch, column, rewrite):
    # A source column called rowid hides DuckDB's rowid pseudo-column. The heal step
    # used rowid as each row's id, so rows with a repeated value were paged twice and
    # others never, and only 3 of 10 rows reached the healed table (review of ADR-002
    # phase 2b). Every row must be sent once and land in the table or quarantine once.
    handler, sent = _drop_first_of_each_page(rewrite)
    monkeypatch.setattr(heal_module, "PAGE_MAX_ROWS", 4)
    monkeypatch.setattr(heal_module.httpx, "AsyncClient",
                        lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    con = duckdb.connect()
    con.execute(f'CREATE TABLE src AS SELECT (i % 3) AS "{column}", i AS v FROM range(10) t(i)')
    healer = heal_module.RunHealer(con, base_url="http://uasr", auth_headers=dict,
                                   pipeline_id="p", run_id="r")
    summary = await healer.heal_table("src", "src")

    assert sorted(r["v"] for page in sent for r in page) == list(range(10))
    held = con.execute(f"SELECT row_index, original_row FROM {heal_module.QUARANTINE_STAGE}").fetchall()
    healed = con.execute("SELECT * FROM src").fetchall()
    assert sorted([json.loads(o)["v"] for _, o in held] + [v for _, v in healed]) == list(range(10))
    assert [i for i, _ in held] == [json.loads(o)["v"] for _, o in held]  # row_index is the row's position
    assert healed == sorted(healed, key=lambda r: r[1]) and all(r == (r[1] % 3, r[1]) for r in healed)
    assert [r[0] for r in con.execute("DESCRIBE src").fetchall()] == [column, "v"]
    assert (summary.rows_in, summary.rows_out, summary.rows_quarantined) == (10, len(healed), len(held))
    con.close()


async def test_clean_source_with_a_rowid_column_keeps_its_rows_and_columns(monkeypatch):
    sent: List[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/uasr/baseline":
            return httpx.Response(200, json={"status": "exists"})
        sent.extend(r["v"] for r in body["rows"])
        return httpx.Response(200, json={"healed": True, "rows": body["rows"], "alerts": [],
                                         "quarantined_rows": [], "repairs": []})

    monkeypatch.setattr(heal_module, "PAGE_MAX_ROWS", 4)
    monkeypatch.setattr(heal_module.httpx, "AsyncClient",
                        lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    con = duckdb.connect()
    con.execute("CREATE TABLE src AS SELECT 7 AS rowid, i AS v FROM range(10) t(i)")
    before = con.execute("SELECT * FROM src").fetchall()
    healer = heal_module.RunHealer(con, base_url="http://uasr", auth_headers=dict,
                                   pipeline_id="p", run_id="r")
    await healer.heal_table("src", "src")
    assert sorted(sent) == list(range(10))  # every row checked once, none twice
    assert con.execute("SELECT * FROM src").fetchall() == before
    assert [r[0] for r in con.execute("DESCRIBE src").fetchall()] == ["rowid", "v"]
    con.close()


async def test_healed_table_that_does_not_account_for_the_load_fails_closed(monkeypatch):
    handler, _ = _drop_first_of_each_page(rewrite=False)
    monkeypatch.setattr(heal_module, "PAGE_MAX_ROWS", 4)
    monkeypatch.setattr(heal_module.httpx, "AsyncClient",
                        lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    real = heal_module.RunHealer._rebuild

    def loses_a_row(self, table, *a):
        real(self, table, *a)
        self.conn.execute(f'DELETE FROM "{table}" WHERE v = (SELECT max(v) FROM "{table}")')

    monkeypatch.setattr(heal_module.RunHealer, "_rebuild", loses_a_row)
    con = duckdb.connect()
    con.execute("CREATE TABLE src AS SELECT i AS v FROM range(10) t(i)")
    healer = heal_module.RunHealer(con, base_url="http://uasr", auth_headers=dict,
                                   pipeline_id="p", run_id="r")
    with pytest.raises(heal_module.HealUnavailable, match="do not account for the load"):
        await healer.heal_table("src", "src")
    con.close()


async def test_a_source_column_with_a_reserved_name_fails_closed(monkeypatch):
    con = duckdb.connect()
    con.execute("CREATE TABLE src AS SELECT i AS __aura_rid FROM range(3) t(i)")
    healer = heal_module.RunHealer(con, base_url="http://uasr", auth_headers=dict,
                                   pipeline_id="p", run_id="r")
    with pytest.raises(heal_module.HealUnavailable, match="reserves"):
        await healer.heal_table("src", "src")
    con.close()
