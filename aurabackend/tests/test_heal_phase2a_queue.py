"""ADR-002 phase 2a item 3 / BUG-565: the in-process MAPE-K queue and the canary.

The worker used to read only Kafka (default localhost:9092), and the free-tier box
has no broker, so it retried forever and consumed nothing. Now /uasr/heal and
/uasr/ingest feed a bounded asyncio queue inside UASR, Kafka starts only when
UASR_KAFKA_SERVERS is set, and /uasr/mapek/status reports what is really running.

The canary (ADR-002 safety net step 2, uasr/repair_canary.py) watches each PROVEN
repair a source gets -- rename, retype, rescale -- over its next N batches: a batch
that drifts on the repair's own column, and that the repair does not bring back,
rolls it back (it is blocked for the source and persisted as ROLLED_BACK); N clean
batches keep it. The handler decides it under a per-source lock before answering,
so a lagging or overflowing queue loses only announcements.

Real DriftDetector, RecoveryLoop, RepairCanary, handlers and SQLite DB throughout;
the FastAPI lifespan is not driven (``_start_mapek`` is what it calls). The only
stand-ins are for the network: no LLM call, and no Kafka broker.
"""
from __future__ import annotations

import asyncio
import copy
import os
import random
import sys
import tempfile
import time
import uuid

import pytest
from sqlalchemy import func, select, text
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
from uasr import repair_canary, service  # noqa: E402
from uasr.actuator_agent import SynthesisActuatorAgent  # noqa: E402
from uasr.db import get_session_factory, init_uasr_db  # noqa: E402
from uasr.drift_detector import DriftDetector  # noqa: E402
from uasr.mapek_worker import MAPEKConfig, MAPEKWorker, QueueItem  # noqa: E402
from uasr.models import BatchPayload, DriftDetectionResult, DriftEvent, RepairWatch  # noqa: E402
from uasr.recovery_loop import RecoveryLoop, RecoveryLoopConfig  # noqa: E402
from uasr.repair_canary import RepairCanary, load_watches  # noqa: E402

N = 3
SCHEMA = {"customer_id": "int", "amount": "float", "status": "str"}
RESCALE = ("rescale", "amount", "100.0")


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_heal2a2_queue_{uuid.uuid4().hex[:8]}.db")
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


async def _no_llm(self, *a, **k):
    return None


@pytest.fixture(autouse=True)
async def _uasr(monkeypatch):
    """Fresh real detector, recovery loop and canary (N batches); queue input."""
    await init_uasr_db()
    detector = DriftDetector()
    monkeypatch.setattr(service, "_detector", detector)
    monkeypatch.setattr(service, "_loop", RecoveryLoop(detector=detector, config=RecoveryLoopConfig()))
    monkeypatch.setattr(service, "_canary", RepairCanary(N))
    monkeypatch.setattr(service, "_mapek_worker", None)
    monkeypatch.setattr(service, "_mapek_bootstrap_task", None)
    monkeypatch.setattr(service, "_SHARED_SHIM_REGISTRY", False)
    monkeypatch.setenv("UASR_MAPEK_ENABLED", "true")
    monkeypatch.delenv("UASR_KAFKA_SERVERS", raising=False)
    # No LLM in CI: the proposal path for an unproven fault falls back to templates.
    monkeypatch.setattr(SynthesisActuatorAgent, "_llm_shim", _no_llm)
    yield
    if service._mapek_worker is not None:
        await service._mapek_worker.stop()


def _as(tenant: str | None) -> Request:
    scope = {"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/x"}
    if tenant:
        scope["state"] = {"user": {"org_id": tenant, "role": "member"}}
    return Request(scope)


def _rows(n: int, seed: int) -> list[dict]:
    rnd = random.Random(seed)
    return [{
        "customer_id": rnd.randint(1, 10_000),
        "amount": round(rnd.uniform(10.0, 500.0), 2),
        "status": rnd.choice(["active", "inactive", "pending"]),
    } for _ in range(n)]


def _x100(rows: list[dict]) -> list[dict]:
    """Upstream started sending amount in cents: the fault the rescale repair fixes."""
    return [{**r, "amount": r["amount"] * 100} for r in rows]


def _shifted_x100(rows: list[dict]) -> list[dict]:
    """In cents AND shifted: dividing by 100 no longer brings amount back."""
    return [{**r, "amount": (r["amount"] + 300.0) * 100} for r in rows]


def _source() -> str:
    return f"src_{uuid.uuid4().hex[:8]}"


def _register(source: str, seed: int = 1) -> None:
    service._detector.register_baseline(source, BatchPayload(
        source_id=source, columns=list(SCHEMA), rows=_rows(400, seed), schema_snapshot=SCHEMA))


async def _heal(source: str, rows: list[dict], tenant: str | None = None, schema=None) -> dict:
    req = service.IngestRequest(source_id=source, rows=copy.deepcopy(rows), schema_snapshot=schema)
    async with get_session_factory()() as db:
        return await service.heal_batch(req, _as(tenant), db=db)


async def _ingest(source: str, rows: list[dict]) -> dict:
    async with get_session_factory()() as db:
        return await service.ingest_batch(
            service.IngestRequest(source_id=source, rows=copy.deepcopy(rows)), _as(None), db=db)


async def _watch_rows(source: str) -> list[RepairWatch]:
    return await load_watches(source)


async def _drift_events(source: str) -> int:
    async with get_session_factory()() as db:
        return (await db.execute(
            select(func.count()).select_from(DriftEvent).where(DriftEvent.source_id == source))).scalar_one()


async def _drained(expected_processed: int, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = await service.mapek_status(_as(None))
        if st["processed"] >= expected_processed and st["depth"] == 0:
            return st
        await asyncio.sleep(0.01)
    raise AssertionError(f"queue did not drain: {await service.mapek_status(_as(None))}")


def _accounted(out: dict, sent: list) -> None:
    """Returned + quarantined rows hold every input row exactly once."""
    indices = sorted(q["index"] for q in out["quarantined_rows"])
    assert len(indices) == len(set(indices))
    assert len(out["rows"]) + len(indices) == len(sent)
    for q in out["quarantined_rows"]:
        assert q["row"] == sent[q["index"]] and q["reason"]


async def _deploy_rescale(source: str, seed: int = 100) -> dict:
    sent = _x100(_rows(300, seed))
    out = await _heal(source, sent)
    assert out["healed"] is True and [r["type"] for r in out["repairs"]] == ["rescale"]
    assert [(w["type"], w["column"], w["param"]) for w in out["canary"]["opened"]] == [RESCALE]
    return out


# ── heal / ingest feed the queue, the worker consumes ──────────────────────

async def test_heal_and_ingest_enqueue_and_the_worker_consumes():
    await service._start_mapek()
    source = _source()
    _register(source)

    assert (await _ingest(source, _rows(300, 2)))["drift_detected"] is False
    assert (await _heal(source, _rows(300, 3)))["drift_detected"] is False
    await _deploy_rescale(source)

    st = await _drained(3)
    assert st["input"] == "queue" and st["running"] is True and st["paused"] is False
    assert st["processed"] == 3 and st["dropped"] == 0 and st["last_error"] is None


# ── the canary: a regression rolls back, a good repair is kept ─────────────

async def test_a_regressing_repair_is_rolled_back_within_n_batches_and_its_rows_never_return():
    source = _source()
    _register(source)
    deployed = await _deploy_rescale(source)

    # The next batch is in cents again but no longer fits the baseline once divided:
    # the drift is on the very column the repair changed, and the repair does not fix it.
    regressing = _shifted_x100(_rows(300, 101))
    verdict = await _heal(source, regressing)

    (rolled,) = verdict["canary"]["rolled_back"]
    assert (rolled["type"], rolled["column"], rolled["param"]) == RESCALE
    assert rolled["recovery_id"] == deployed["recovery_id"] and rolled["batches_seen"] < N
    assert verdict["rows"] == [] and verdict["healed"] is False
    _accounted(verdict, regressing)
    (row,) = await _watch_rows(source)
    assert row.status == "rolled_back" and row.reason

    # After the verdict the repair never touches a caller's rows again, even on a batch
    # it would prove: the batch is quarantined as sent, with the reason.
    after = _x100(_rows(300, 102))
    out = await _heal(source, after)
    assert out["rows"] == [] and out["repairs"] == [] and out["healed"] is False
    _accounted(out, after)
    assert "rolled back by the post-heal canary" in out["quarantined_rows"][0]["reason"]
    assert out["canary"]["opened"] == []


async def test_a_good_repair_is_kept_after_n_clean_batches():
    source = _source()
    _register(source)
    await _deploy_rescale(source)

    for i in range(N):
        truth = _rows(300, 110 + i)
        out = await _heal(source, _x100(truth))
        assert out["healed"] is True and out["canary"]["rolled_back"] == []
        assert out["rows"] == truth
    assert [(w["type"], w["column"]) for w in out["canary"]["kept"]] == [("rescale", "amount")]
    (row,) = await _watch_rows(source)
    assert row.status == "deployed" and row.batches_seen == N
    assert service._canary.watches(source) == []

    # Kept: later batches are still repaired, and no second watch opens for it.
    later = await _heal(source, _x100(_rows(300, 120)))
    assert later["healed"] is True and later["canary"]["opened"] == []


async def test_a_clean_batch_counts_for_the_canary():
    """Upstream fixed its units: nothing for the repair to regress on."""
    source = _source()
    _register(source)
    await _deploy_rescale(source)

    for i in range(N):
        out = await _heal(source, _rows(300, 130 + i))
        assert out["drift_detected"] is False
    assert (await _watch_rows(source))[0].status == "deployed"


async def test_unrelated_drift_does_not_roll_back_a_correct_repair():
    source = _source()
    _register(source)
    await _deploy_rescale(source)

    # amount is in cents again (the repair proves again), AND customer_id shifted by a
    # million, a second fault no rule can prove: the batch is quarantined for it, but
    # that is not the rescale's regression.
    sent = [{**r, "customer_id": r["customer_id"] + 1_000_000} for r in _x100(_rows(300, 140))]
    out = await _heal(source, sent)

    assert out["rows"] == [] and out["healed"] is False
    _accounted(out, sent)
    assert "customer_id" in out["quarantined_rows"][0]["reason"]
    assert out["canary"]["rolled_back"] == []
    (watch,) = service._canary.watches(source)
    assert watch.batches_seen == 1 and watch.status == "watching"
    assert service._canary.blocked(source) == frozenset()


# ── concurrency: the watch is read and acted on under the source's lock ────

async def test_concurrent_clean_heals_for_one_source_each_count_once():
    source = _source()
    _register(source)
    await _deploy_rescale(source)

    outs = await asyncio.gather(*(_heal(source, _x100(_rows(300, 150 + i))) for i in range(N)))

    assert all(o["healed"] is True for o in outs)
    assert sum(len(o["canary"]["kept"]) for o in outs) == 1
    (row,) = await _watch_rows(source)
    assert row.status == "deployed" and row.batches_seen == N


@pytest.mark.parametrize("bad_first", [True, False])
async def test_concurrent_heals_for_one_source_cannot_lose_a_verdict(bad_first, monkeypatch):
    """Review of 9180b5b, finding 1: a watch read before an await and acted on after
    it. The source lock is FIFO and taken before the first await, so the heal that
    arrives first is judged first, and the other is judged on the state it left."""
    source = _source()
    _register(source)
    await _deploy_rescale(source)
    real_persist = repair_canary._persist

    async def slow_persist(watches):
        # A slow disk: widens the window in which an unlocked second heal would read
        # the watch before the first one's verdict took effect.
        await asyncio.sleep(0.2)
        return await real_persist(watches)

    monkeypatch.setattr(repair_canary, "_persist", slow_persist)
    good = _x100(_rows(300, 160))
    bad = _shifted_x100(_rows(300, 161))

    if bad_first:
        out_bad, out_good = await asyncio.gather(_heal(source, bad), _heal(source, good))
    else:
        out_good, out_bad = await asyncio.gather(_heal(source, good), _heal(source, bad))

    assert len(out_bad["canary"]["rolled_back"]) == 1 and out_good["canary"]["rolled_back"] == []
    (row,) = await _watch_rows(source)
    assert row.status == "rolled_back"
    if bad_first:
        # The verdict came first, so the good batch never gets the rejected repair.
        assert out_good["rows"] == [] and row.batches_seen == 0
        assert "rolled back by the post-heal canary" in out_good["quarantined_rows"][0]["reason"]
    else:
        # The repair still stood for the good batch, and its clean verdict was counted.
        assert out_good["healed"] is True and len(out_good["rows"]) == len(good)
        assert row.batches_seen == 1
    for out, sent in ((out_good, good), (out_bad, bad)):
        _accounted(out, sent)


# ── one watch per deployed repair ──────────────────────────────────────────

async def test_two_deployments_for_one_source_each_get_their_own_canary():
    source = _source()
    _register(source)
    await _deploy_rescale(source)
    retyped = {**SCHEMA, "customer_id": "str"}

    def second_fault(seed):
        return [{**r, "customer_id": str(r["customer_id"])} for r in _x100(_rows(300, seed))]

    out = await _heal(source, second_fault(170), schema=retyped)
    assert out["healed"] is True
    assert [(w["type"], w["column"]) for w in out["canary"]["opened"]] == [("retype", "customer_id")]
    watches = {w.signature[0]: w for w in service._canary.watches(source)}
    assert set(watches) == {"rescale", "retype"}, "the second deployment ended the first one's watch"
    assert watches["rescale"].batches_seen == 1 and watches["retype"].batches_seen == 0

    for seed in (171, 172):
        await _heal(source, second_fault(seed), schema=retyped)
    status = {r.repair_type: r for r in await _watch_rows(source)}
    assert status["rescale"].status == "deployed" and status["rescale"].batches_seen == N
    assert status["retype"].status == "watching" and status["retype"].batches_seen == 2

    # Now the retyped column regresses: customer_id casts back, but its values are off
    # the baseline. Only the retype is rolled back; the kept rescale stands.
    bad = [{**r, "customer_id": str(int(r["customer_id"]) + 1_000_000)} for r in second_fault(173)]
    out = await _heal(source, bad, schema=retyped)
    assert [(w["type"], w["column"]) for w in out["canary"]["rolled_back"]] == [("retype", "customer_id")]
    status = {r.repair_type: r.status for r in await _watch_rows(source)}
    assert status == {"rescale": "deployed", "retype": "rolled_back"}
    assert {s[0] for s in service._canary.blocked(source)} == {"retype"}


# ── the queue: lag and drop-oldest lose no fault and no rollback ───────────

async def test_queue_lag_and_drop_oldest_lose_no_fault_and_no_rollback():
    worker = MAPEKWorker(MAPEKConfig(), detector=service._detector, recovery_loop=service._loop)
    await worker.start_queue(maxsize=1)
    service._mapek_worker = worker
    worker.pause("test: a stalled consumer")
    source = _source()
    _register(source)

    await _deploy_rescale(source)
    await _heal(source, _shifted_x100(_rows(300, 180)))      # the rollback
    await _heal(source, _x100(_rows(300, 181)))               # quarantined: blocked
    await _ingest(source, _shifted_x100(_rows(300, 182)))      # a fault seen via ingest

    st = worker.status()
    assert st["dropped"] >= 3 and st["depth"] == 1 and st["processed"] == 0
    # Every fault is a drift event and the rollback is in the DB, whatever the queue lost.
    assert await _drift_events(source) == 4
    (row,) = await _watch_rows(source)
    assert row.status == "rolled_back"
    assert service._canary.blocked(source) == frozenset({RESCALE})
    status = await service.mapek_status(_as(None))
    assert status["paused"] is True
    # The canary loads every source's state on its first batch, so earlier tests'
    # sources (same untenanted caller) are listed too; this source's is the one asked.
    mine = [w for w in status["canary"]["rolled_back"] if w["source_id"] == source]
    assert [w["column"] for w in mine] == ["amount"]

    worker.resume()
    st = await _drained(1)
    assert st["processed"] == 1 and st["dropped"] == 3


async def test_submit_never_blocks_and_drops_the_oldest():
    worker = MAPEKWorker(MAPEKConfig(), detector=service._detector, recovery_loop=service._loop)
    await worker.start_queue(maxsize=2)
    worker.pause("test")
    drift = DriftDetectionResult(source_id="s", batch_id="b")
    for i in range(5):
        worker.submit(QueueItem("ingest", "s", f"b{i}", drift))
    assert worker.status()["dropped"] == 3 and worker.status()["depth"] == 2
    assert [worker._queue.get_nowait().batch_id for _ in range(2)] == ["b3", "b4"]
    await worker.stop()


# ── durability: the DB says ROLLED_BACK before a reload can look ──────────

async def test_a_rollback_is_persisted_and_a_reload_never_resurrects_the_repair():
    source = _source()
    _register(source)
    await _deploy_rescale(source)
    await _heal(source, _shifted_x100(_rows(300, 190)))

    # A restart: a fresh canary hydrated from the DB alone.
    reloaded = RepairCanary(N)
    reloaded.hydrate(await load_watches())
    assert reloaded.blocked(source) == frozenset({RESCALE})
    assert reloaded.watches(source) == []
    service._canary = reloaded

    sent = _x100(_rows(300, 191))
    out = await _heal(source, sent)
    assert out["rows"] == [] and out["repairs"] == []
    _accounted(out, sent)


async def test_a_watch_survives_a_reload_with_its_progress():
    source = _source()
    _register(source)
    await _deploy_rescale(source)
    await _heal(source, _x100(_rows(300, 195)))

    reloaded = RepairCanary(N)
    reloaded.hydrate(await load_watches())
    (watch,) = reloaded.watches(source)
    assert watch.signature == RESCALE and watch.batches_seen == 1


async def test_a_rollback_whose_write_failed_still_holds_and_is_retried():
    source = _source()
    _register(source)
    await _deploy_rescale(source)
    async with get_session_factory()() as db:
        await db.execute(text("ALTER TABLE uasr_repair_watches RENAME TO uasr_repair_watches_away"))
        await db.commit()
    try:
        verdict = await _heal(source, _shifted_x100(_rows(300, 200)))
        assert len(verdict["canary"]["rolled_back"]) == 1
        # Not in the DB yet, but in force: re-applying a rejected repair is the worse error.
        assert service._canary.blocked(source) == frozenset({RESCALE})
        assert len(service._canary.unpersisted(source)) == 1
        out = await _heal(source, _x100(_rows(300, 201)))
        assert out["rows"] == [] and out["repairs"] == []
    finally:
        async with get_session_factory()() as db:
            await db.execute(text("ALTER TABLE uasr_repair_watches_away RENAME TO uasr_repair_watches"))
            await db.commit()

    # The next batch for the source writes the rollback first.
    await _heal(source, _rows(300, 202))
    assert service._canary.unpersisted(source) == []
    (row,) = await _watch_rows(source)
    assert row.status == "rolled_back"


# ── review of 2ce9d08: a failed startup load, two replicas on one watch ────

async def test_a_failed_startup_load_fails_closed_until_the_canary_loads(monkeypatch):
    """Finding 1: the startup load failed, so the canary started empty, and nothing
    ever loaded it again: a repair rolled back before the restart was proven and
    applied to callers' rows again. Now /uasr/heal retries the load before each batch
    and answers 503 while it cannot read it."""
    from fastapi import HTTPException

    source = _source()
    _register(source)
    await _deploy_rescale(source)
    await _heal(source, _shifted_x100(_rows(300, 210)))
    assert service._canary.blocked(source) == frozenset({RESCALE})

    real_load = repair_canary.load_watches

    async def db_down(source_id=None):
        raise RuntimeError("database is locked")

    # A restart whose startup read of the canary state fails.
    monkeypatch.setattr(service, "_canary", RepairCanary(N))
    monkeypatch.setattr(repair_canary, "load_watches", db_down)
    await service._restore_repair_watches()

    sent = _x100(_rows(300, 211))
    with pytest.raises(HTTPException) as err:
        await _heal(source, sent)
    assert err.value.status_code == 503

    # The DB is back: the next batch loads the state first, and the rollback holds.
    monkeypatch.setattr(repair_canary, "load_watches", real_load)
    out = await _heal(source, sent)
    assert out["rows"] == [] and out["repairs"] == [] and out["healed"] is False
    _accounted(out, sent)
    assert out["canary"]["opened"] == []
    rows = await _watch_rows(source)
    assert [r.status for r in rows] == ["rolled_back"]


REGRESSED_EVIDENCE = {"failed_columns": ["amount"], "proven": set(), "columns": list(SCHEMA),
                      "retype_seen": None, "scale_seen": None}


@pytest.mark.parametrize("clean_before", [0, N - 1])
async def test_a_replica_that_judged_clean_cannot_overwrite_another_replicas_rollback(clean_before):
    """Finding 2: with the shared registry two replicas can hydrate the same watching
    row. A rolls it back; B, on its own batch, judges it clean and used to merge
    'watching' (or 'deployed' at N) over the rollback, so the DB, B and every later
    reload brought the rolled-back repair back. The rollback must win."""
    source = _source()
    _register(source)
    await _deploy_rescale(source)
    for i in range(clean_before):
        await _heal(source, _x100(_rows(300, 220 + i)))

    replica_a, replica_b = RepairCanary(N), RepairCanary(N)
    for replica in (replica_a, replica_b):
        replica.hydrate(await load_watches())
        assert [w.signature for w in replica.watches(source)] == [RESCALE]

    async with replica_a.lock(source):
        assert len((await replica_a.observe(source, REGRESSED_EVIDENCE, None, [])).rolled_back) == 1
    async with replica_b.lock(source):
        await replica_b.observe(source, None, None, [])

    (row,) = await _watch_rows(source)
    assert row.status == "rolled_back"
    # B lost the compare-and-set and reloaded: it blocks the repair too.
    assert replica_b.blocked(source) == frozenset({RESCALE}) and replica_b.watches(source) == []
    reloaded = RepairCanary(N)
    reloaded.hydrate(await load_watches())
    assert reloaded.blocked(source) == frozenset({RESCALE})


async def test_a_stale_replica_cannot_turn_a_watchs_progress_back():
    source = _source()
    _register(source)
    await _deploy_rescale(source)
    stale = RepairCanary(N)
    stale.hydrate(await load_watches())
    await _heal(source, _x100(_rows(300, 230)))
    await _heal(source, _rows(300, 231))
    (row,) = await _watch_rows(source)
    assert row.batches_seen == 2

    # The stale replica counts its own clean batch from 0: it must not write 1 over 2.
    async with stale.lock(source):
        await stale.observe(source, None, None, [])
    (row,) = await _watch_rows(source)
    assert row.batches_seen == 2 and row.status == "watching"
    (watch,) = stale.watches(source)
    assert watch.batches_seen == 2


# ── tenant isolation ───────────────────────────────────────────────────────

async def test_a_rollback_and_the_status_stay_inside_the_tenant():
    raw = _source()
    for tenant in ("tenantA", "tenantB"):
        _register(f"{tenant}::{raw}")
        out = await _heal(raw, _x100(_rows(300, 210)), tenant=tenant)
        assert out["healed"] is True

    await _heal(raw, _shifted_x100(_rows(300, 211)), tenant="tenantA")

    assert service._canary.blocked(f"tenantA::{raw}") == frozenset({RESCALE})
    assert service._canary.blocked(f"tenantB::{raw}") == frozenset()
    out_b = await _heal(raw, _x100(_rows(300, 212)), tenant="tenantB")
    assert out_b["healed"] is True and [r["type"] for r in out_b["repairs"]] == ["rescale"]

    st_a = await service.mapek_status(_as("tenantA"))
    st_b = await service.mapek_status(_as("tenantB"))
    assert {w["source_id"] for w in st_a["canary"]["rolled_back"]} == {f"tenantA::{raw}"}
    assert st_a["canary"]["watching"] == []
    assert st_b["canary"]["rolled_back"] == []
    assert {w["source_id"] for w in st_b["canary"]["watching"]} == {f"tenantB::{raw}"}


# ── Kafka only with a broker; an honest status ─────────────────────────────

async def test_kafka_is_not_started_without_servers():
    await service._start_mapek()
    assert service._mapek_bootstrap_task is None
    assert service._mapek_worker.input == "queue" and service._mapek_worker._consumer is None


async def test_kafka_bootstrap_starts_only_when_servers_are_set(monkeypatch):
    started = asyncio.Event()

    async def no_broker_here():
        # The real bootstrap would open a network connection to the broker.
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setenv("UASR_KAFKA_SERVERS", "broker:9092")
    monkeypatch.setattr(service, "_mapek_worker_bootstrap", no_broker_here)
    monkeypatch.setattr(service, "_repair_scheduler", None)
    await service._start_mapek()
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert service._mapek_worker is None
        st = await service.mapek_status(_as(None))
        assert st["input"] == "kafka" and st["running"] is False
        assert "not connected" in st["reason"]
    finally:
        service._mapek_bootstrap_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await service._mapek_bootstrap_task


async def test_status_is_honest(monkeypatch):
    monkeypatch.setenv("UASR_MAPEK_ENABLED", "false")
    st = await service.mapek_status(_as(None))
    assert st["input"] == "none" and st["running"] is False and st["post_heal_validation_batches"] == N

    monkeypatch.setenv("UASR_MAPEK_ENABLED", "true")
    await service._start_mapek()
    assert (await service.mapek_status(_as(None)))["running"] is True

    # A consumer task that died is not "running", whatever was started.
    service._mapek_worker._task.cancel()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert (await service.mapek_status(_as(None)))["running"] is False


async def test_stop_retrieves_and_logs_the_exception_of_a_loop_that_died(caplog):
    worker = MAPEKWorker(MAPEKConfig(), detector=service._detector, recovery_loop=service._loop)
    await worker.start_queue()
    worker._task.cancel()

    async def boom():
        raise RuntimeError("consumer died")

    worker._task = asyncio.create_task(boom())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    with caplog.at_level("WARNING"):
        await worker.stop()
    assert any("consumer died" in r.getMessage() for r in caplog.records)


def test_post_heal_validation_defaults_to_three_batches(monkeypatch):
    from uasr import runtime_config as rc

    monkeypatch.delenv("UASR_POST_HEAL_VALIDATION_BATCHES", raising=False)
    assert rc.post_heal_validation_batches() == 3


# ── a rollback's block expires, and can be cleared (no human gate, ADR-002) ──

async def _age_rollbacks(source: str, hours: float) -> None:
    """Move the source's rollbacks `hours` into the past, in the DB and in memory."""
    from datetime import timedelta

    from sqlalchemy import update
    shift = timedelta(hours=hours)
    async with get_session_factory()() as db:
        for row in await load_watches(source):
            if row.status == "rolled_back":
                await db.execute(update(RepairWatch).where(RepairWatch.id == row.id).values(
                    decided_at=row.decided_at - shift, created_at=row.created_at - shift))
        await db.commit()
    reloaded = RepairCanary(N)
    reloaded.hydrate(await load_watches())
    service._canary = reloaded


async def _roll_back_rescale(source: str, seed: int) -> None:
    out = await _heal(source, _shifted_x100(_rows(300, seed)))
    assert [w["type"] for w in out["canary"]["rolled_back"]] == ["rescale"]


async def test_a_block_expires_and_the_repair_must_pass_the_canary_again():
    source = _source()
    _register(source)
    await _deploy_rescale(source)
    await _roll_back_rescale(source, 300)

    await _age_rollbacks(source, 0.5)
    assert service._canary.blocked(source) == frozenset({RESCALE})  # 1 h block still holds
    held = await _heal(source, _x100(_rows(300, 301)))
    assert held["rows"] == [] and held["repairs"] == []

    await _age_rollbacks(source, 1.0)  # now 1.5 h ago
    assert service._canary.blocked(source) == frozenset()
    out = await _heal(source, _x100(_rows(300, 302)))
    assert out["healed"] is True and [r["type"] for r in out["repairs"]] == ["rescale"]
    # Expiry applied nothing by itself: the repair was proven again and is watched again.
    assert [(w["type"], w["column"], w["param"]) for w in out["canary"]["opened"]] == [RESCALE]


async def test_repeated_rollbacks_back_off_to_longer_blocks():
    source = _source()
    _register(source)
    await _deploy_rescale(source)
    await _roll_back_rescale(source, 310)
    await _age_rollbacks(source, 2)
    await _deploy_rescale(source, seed=311)
    await _roll_back_rescale(source, 312)

    await _age_rollbacks(source, 2)
    assert service._canary.blocked(source) == frozenset({RESCALE})  # second rollback: 6 h
    await _age_rollbacks(source, 5)  # 7 h
    assert service._canary.blocked(source) == frozenset()


async def test_a_reload_after_expiry_keeps_the_new_watch_and_drops_the_block():
    source = _source()
    _register(source)
    await _deploy_rescale(source)
    await _roll_back_rescale(source, 320)
    await _age_rollbacks(source, 2)
    await _heal(source, _x100(_rows(300, 321)))  # proven again: a new watch opens

    reloaded = RepairCanary(N)
    reloaded.hydrate(await load_watches())
    assert reloaded.blocked(source) == frozenset()
    assert [w.signature for w in reloaded.watches(source)] == [RESCALE]


async def test_clearing_a_block_is_scoped_logged_and_still_needs_proof(caplog):
    raw = _source()
    source = f"tenantA::{raw}"
    _register(source)
    assert (await _heal(raw, _x100(_rows(300, 330)), tenant="tenantA"))["healed"] is True
    await _heal(raw, _shifted_x100(_rows(300, 331)), tenant="tenantA")
    assert service._canary.blocked(source) == frozenset({RESCALE})
    kind, column, param = RESCALE
    req = service.ClearRepairBlockRequest(source_id=raw, type=kind, column=column, param=param)

    with pytest.raises(service.HTTPException) as exc:
        await service.clear_repair_block(req, _as("tenantB"))  # another tenant's source
    assert exc.value.status_code == 404
    assert service._canary.blocked(source) == frozenset({RESCALE})

    with caplog.at_level("WARNING", logger="uasr.repair_canary"):
        assert (await service.clear_repair_block(req, _as("tenantA")))["cleared"] == 1
    assert "cleared by tenantA" in caplog.text
    assert [r.status for r in await load_watches(source) if r.status != "watching"] == ["cleared"]

    reloaded = RepairCanary(N)
    reloaded.hydrate(await load_watches())
    assert reloaded.blocked(source) == frozenset()
    out = await _heal(raw, _x100(_rows(300, 332)), tenant="tenantA")
    assert out["healed"] is True and [w["type"] for w in out["canary"]["opened"]] == ["rescale"]
