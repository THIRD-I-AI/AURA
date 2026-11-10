"""BUG-578 (UASR per-source memory is bounded) and BUG-579 (no false alarm on a clean
micro-batch).

BUG-579: on a clean stream cut into micro-batches of 1-20 rows, ~40% of 5-row batches
were quarantined. A one-row batch is no sample of a distribution: the >2 sigma mean
rule fired on one boolean True or one lognormal tail value, the plug-in KL of a few
labels sat past zeta, the semantic distance of a 5-row batch past its threshold, and
the G test's chi-square tail was reached by two rows past the baseline's max. Each
check now abstains unless the batch's rows establish the difference at 1e-6, while
what one or two rows DO prove -- a type change, a rename, a repeated key -- still
fires.

BUG-578: the canary's per-source locks, watches and rollbacks and the detector's
per-source locks stayed in memory forever. Locks now live only while used, and the
canary keeps at most ``max_resident`` idle sources, reloading any other from
uasr_repair_watches on its next sync, so a dropped source behaves exactly as if it
had stayed.

Real DriftDetector, RecoveryLoop, RepairCanary, /uasr/heal handler and SQLite DB; no
lifespan, no network (the LLM proposal path is stubbed).
"""
from __future__ import annotations

import asyncio
import copy
import gc
import logging
import os
import random
import tempfile
import threading
import tracemalloc
import uuid
from types import SimpleNamespace

import pytest
from starlette.requests import Request

import metadata_store.db as _metadata_db
from tests.accuracy.datasets import copy_rows, make_dataset
from uasr import repair_canary, service
from uasr.actuator_agent import SynthesisActuatorAgent
from uasr.db import get_session_factory, init_uasr_db
from uasr.drift_detector import DriftDetector
from uasr.models import BatchPayload, DriftType
from uasr.recovery_loop import RecoveryLoop, RecoveryLoopConfig
from uasr.repair_canary import RepairCanary, load_watches
from uasr.state_store import SqliteStateStore

N = 3
SCHEMA = {"customer_id": "int", "amount": "float", "status": "str"}
RESCALE = ("rescale", "amount", "100.0")
REGRESSED = {"failed_columns": ["amount"], "proven": set(), "columns": list(SCHEMA),
             "retype_seen": None, "scale_seen": None}


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    path = os.path.join(tempfile.gettempdir(), f"aura_bug578_{uuid.uuid4().hex[:8]}.db")
    _metadata_db.DATABASE_URL = f"sqlite+aiosqlite:///{path}"
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
        os.remove(path)
    except OSError:
        pass


async def _no_llm(self, *a, **k):
    return None


@pytest.fixture(autouse=True)
async def _uasr(monkeypatch):
    await init_uasr_db()
    detector = DriftDetector()
    monkeypatch.setattr(service, "_detector", detector)
    monkeypatch.setattr(service, "_loop", RecoveryLoop(detector=detector, config=RecoveryLoopConfig()))
    monkeypatch.setattr(service, "_canary", RepairCanary(N))
    monkeypatch.setattr(service, "_mapek_worker", None)
    monkeypatch.setattr(service, "_SHARED_SHIM_REGISTRY", False)
    monkeypatch.setattr(SynthesisActuatorAgent, "_llm_shim", _no_llm)
    yield


def _as(tenant=None) -> Request:
    scope = {"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/x"}
    if tenant:
        scope["state"] = {"user": {"org_id": tenant, "role": "member"}}
    return Request(scope)


def _rows(n: int, seed: int) -> list:
    rnd = random.Random(seed)
    return [{"customer_id": rnd.randint(1, 10_000), "amount": round(rnd.uniform(10.0, 500.0), 2),
             "status": rnd.choice(["active", "inactive", "pending"])} for _ in range(n)]


def _x100(rows):
    return [{**r, "amount": r["amount"] * 100} for r in rows]


def _shifted_x100(rows):
    return [{**r, "amount": (r["amount"] + 300.0) * 100} for r in rows]


def _source() -> str:
    return f"src_{uuid.uuid4().hex[:8]}"


def _register(source: str) -> None:
    service._detector.register_baseline(source, BatchPayload(
        source_id=source, columns=list(SCHEMA), rows=_rows(400, 1), schema_snapshot=SCHEMA))


async def _heal(source: str, rows: list) -> dict:
    req = service.IngestRequest(source_id=source, rows=copy.deepcopy(rows))
    async with get_session_factory()() as db:
        return await service.heal_batch(req, _as(), db=db)


# ── BUG-579: clean micro-batches never alarm ─────────────────────────────────

SIZES = (1, 2, 5, 10, 20)


@pytest.mark.parametrize("snapshot", [False, True])
def test_clean_micro_batches_of_every_size_raise_no_drift(snapshot):
    """Before the fix, at 5 rows a batch: 141 of 320 batches drifted (statistical on a
    label column or a boolean, semantic), every one of them quarantined by /uasr/heal."""
    det = DriftDetector()
    loop = RecoveryLoop(detector=det, config=RecoveryLoopConfig())
    found = []
    for seed in range(24):
        ds = make_dataset(seed, batch_rows=100)
        cols = list(ds.schema)
        schema = ds.schema if snapshot else None
        for k in SIZES:
            sid = f"clean_{seed}_{k}"
            det.register_baseline(sid, BatchPayload(source_id=sid, columns=cols, rows=ds.baseline,
                                                    schema_snapshot=schema))
            for i in range(0, len(ds.truth), k):
                b = BatchPayload(source_id=sid, batch_id=f"b{i}", columns=cols,
                                 rows=copy_rows(ds.truth[i:i + k]), schema_snapshot=schema)
                r = det.detect(b)
                if r.drift_detected:
                    found.append((seed, k, i, [t.value for t in r.drift_types], r.details))
                elif loop.unexplained_shift(r, b) is not None:
                    found.append((seed, k, i, "rank shift"))
    assert found == []


async def test_clean_micro_batches_through_heal_quarantine_nothing():
    found = []
    for seed in (0, 1, 4, 6):  # shapes that alarmed before: labels, booleans, a lognormal
        ds = make_dataset(seed, batch_rows=40)
        cols = list(ds.schema)
        for k in SIZES:
            sid = _source()
            service._detector.register_baseline(sid, BatchPayload(source_id=sid, columns=cols, rows=ds.baseline))
            for i in range(0, len(ds.truth), k):
                sent = copy_rows(ds.truth[i:i + k])
                req = service.IngestRequest(source_id=sid, columns=cols, rows=copy.deepcopy(sent))
                async with get_session_factory()() as db:
                    out = await service.heal_batch(req, _as(), db=db)
                if out["quarantined_rows"] or out["degraded"] or out["rows"] != sent:
                    found.append((seed, k, i, out.get("drift_type"), out.get("reason")))
    assert found == []


def _one_row_source(det: DriftDetector, key: bool = False):
    ds = make_dataset(3, batch_rows=10, require=lambda cs: any(c.kind == "count" for c in cs))
    sid = f"prov_{uuid.uuid4().hex[:6]}"
    meta = {"key_columns": [ds.columns[0].name]} if key else {}
    det.register_baseline(sid, BatchPayload(source_id=sid, columns=list(ds.schema), rows=ds.baseline,
                                            schema_snapshot=ds.schema, metadata=meta))
    return ds, sid


def _detect(det, sid, ds, rows, **kw):
    return det.detect(BatchPayload(source_id=sid, columns=list(rows[0]), rows=rows,
                                   schema_snapshot=kw.get("schema"), metadata=kw.get("metadata") or {}))


def test_what_one_or_two_rows_prove_is_still_caught():
    det = DriftDetector()
    ds, sid = _one_row_source(det)
    row = dict(ds.truth[0])
    count = next(c.name for c in ds.columns if c.kind == "count")
    measure = next(c.name for c in ds.columns if c.kind in ("normal", "uniform", "lognormal", "money", "centred"))

    renamed = {(f"{k}_v2" if k == measure else k): v for k, v in row.items()}
    r = _detect(det, sid, ds, [renamed])
    assert DriftType.SCHEMA in r.drift_types, r.details

    retyped = {**row, count: f"{row[count]} units"}
    r = _detect(det, sid, ds, [retyped])
    assert r.drift_detected and r.drift_types[0] in (DriftType.SCHEMA, DriftType.STATISTICAL), r.details

    r = _detect(det, sid, ds, [dict(row), dict(row)])
    assert DriftType.DUPLICATE in r.drift_types, r.details

    # A unit fault is proven by a handful of rows all far past the baseline's range.
    scaled = [{**x, measure: x[measure] * 1000 if x[measure] else 1e9} for x in copy_rows(ds.truth[:5])]
    r = _detect(det, sid, ds, scaled)
    assert DriftType.STATISTICAL in r.drift_types and measure in r.drift_vectors["statistical"]["affected_columns"]


def test_extreme_outliers_in_a_large_batch_still_fire():
    """The mean rule no longer fires by itself; values far past what the baseline's
    spread allows (Chebyshev) still prove contamination."""
    rnd = random.Random(7)
    det = DriftDetector(default_zeta=0.05)
    det.register_baseline("o", BatchPayload(source_id="o", columns=["v"],
                                            rows=[{"v": rnd.gauss(100.0, 10.0)} for _ in range(300)]))
    rows = [{"v": rnd.gauss(100.0, 10.0)} for _ in range(295)] + [{"v": 1500.0}] * 5
    assert det.detect(BatchPayload(source_id="o", columns=["v"], rows=rows), record_state=False).drift_detected
    one = [{"v": rnd.gauss(100.0, 10.0)} for _ in range(4)] + [{"v": 150.0}]
    assert not det.detect(BatchPayload(source_id="o", columns=["v"], rows=one), record_state=False).drift_detected


def test_micro_batches_do_not_raise_zeta():
    """A 1-row batch's KL measures its size, not the source's noise; recording it
    lifted zeta until drift in a full batch passed."""
    det = DriftDetector()
    ds = make_dataset(4, batch_rows=200)
    cols = list(ds.schema)
    det.register_baseline("z", BatchPayload(source_id="z", columns=cols, rows=ds.baseline))
    for r in ds.truth[:100]:
        det.detect(BatchPayload(source_id="z", columns=cols, rows=[dict(r)]))
    assert det._store.peek("z").kl_history == []
    det.detect(BatchPayload(source_id="z", columns=cols, rows=copy_rows(ds.truth[100:200])))
    assert len(det._store.peek("z").kl_history) == 1


# ── BUG-578: locks live only while used ──────────────────────────────────────

def test_detector_locks_are_dropped_when_idle_and_still_exclude():
    det = DriftDetector()
    inside = {"s": 0}
    overlap = []

    def work(tid: int) -> None:
        for i in range(300):
            sid = "shared" if i % 2 else f"t{tid}_{i}"
            with det._lock_for(sid):
                if sid == "shared":
                    inside["s"] += 1
                    if inside["s"] != 1:
                        overlap.append(tid)
                    inside["s"] -= 1

    threads = [threading.Thread(target=work, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert overlap == []
    assert det._source_locks == {}


async def _open_watch(canary: RepairCanary, sid: str) -> None:
    repair = SimpleNamespace(type="rescale", column="amount", proof={"factor": 100.0})
    async with canary.lock(sid):
        await canary.sync(sid, shared=False)
        assert (await canary.observe(sid, None, f"rec_{sid}", [repair])).opened


async def _touch(canary: RepairCanary, sids) -> None:
    for sid in sids:
        async with canary.lock(sid):
            await canary.sync(sid, shared=False)


def _in_memory(canary: RepairCanary, sid: str) -> bool:
    return any(sid in d for d in (canary._resident, canary._watches, canary._kept,
                                  canary._rolled_back, canary._rollback_times))


async def test_memory_stays_bounded_over_thousands_of_sources(tmp_path):
    """Each source registers a baseline, is checked, and opens a canary watch, as a
    source's first healed batch does. Unbounded, that was ~1.5 KB of canary state
    and two locks per source for the life of the process."""
    canary = RepairCanary(N, max_resident=100)
    det = DriftDetector(state_store=SqliteStateStore(str(tmp_path / "state.db")))
    base = _rows(40, 3)

    async def one(i: int) -> None:
        sid = f"mem_{i}"
        det.register_baseline(sid, BatchPayload(source_id=sid, columns=list(SCHEMA), rows=base))
        assert not det.detect(BatchPayload(source_id=sid, columns=list(SCHEMA), rows=_rows(3, i))).drift_detected
        await _open_watch(canary, sid)

    for i in range(300):  # warm caches (SQLAlchemy statement cache, interned strings)
        await one(i)
    gc.collect()
    # pytest keeps every captured log record in memory; that is the harness, not UASR.
    logging.disable(logging.CRITICAL)
    tracemalloc.start()
    try:
        await one(300)
        gc.collect()
        start = tracemalloc.get_traced_memory()[0]
        for i in range(301, 1800):
            await one(i)
        gc.collect()
        grown = tracemalloc.get_traced_memory()[0] - start
    finally:
        tracemalloc.stop()
        logging.disable(logging.NOTSET)
    assert len(canary._resident) <= 100 and len(canary._watches) <= 100
    assert canary._locks == {} and det._source_locks == {}
    assert grown < 512 * 1024, f"{grown / 1024:.0f} KB for 1500 sources"
    assert len(await load_watches()) >= 1800  # all still durable


async def test_a_dropped_source_comes_back_with_its_watch_and_its_rollback():
    canary = RepairCanary(N, max_resident=2)
    watched, rolled = _source(), _source()
    await _open_watch(canary, watched)
    async with canary.lock(watched):
        await canary.observe(watched, None, None, [])  # one clean batch
    await _open_watch(canary, rolled)
    async with canary.lock(rolled):
        assert (await canary.observe(rolled, REGRESSED, None, [])).rolled_back
    before = ([w.to_dict() for w in canary.watches(watched)], canary.blocked(rolled),
              canary.block_until(rolled, RESCALE))

    await _touch(canary, [_source() for _ in range(5)])
    assert not _in_memory(canary, watched) and not _in_memory(canary, rolled)

    await _touch(canary, [watched, rolled])
    assert ([w.to_dict() for w in canary.watches(watched)], canary.blocked(rolled),
            canary.block_until(rolled, RESCALE)) == before
    async with canary.lock(watched):
        out = await canary.observe(watched, None, None, [])
    assert [w.batches_seen for w in out.advanced] == [2]
    # The status view still lists the dropped source's rollback.
    await _touch(canary, [_source() for _ in range(5)])
    view = await canary.visible(None)
    assert any(w["source_id"] == rolled and w["blocked"] for w in view["rolled_back"])


async def test_a_cleared_rollback_reloads_with_the_same_backoff():
    """clear() forgets the repair's rollback times in memory; a reload used to bring
    an earlier rollback back, so the next block was 6 h instead of 1 h."""
    canary = RepairCanary(N, max_resident=1)
    sid = _source()
    await _open_watch(canary, sid)
    async with canary.lock(sid):
        await canary.observe(sid, REGRESSED, None, [])
        await canary.clear(sid, RESCALE, "ops", None)
    await _open_watch(canary, sid)
    async with canary.lock(sid):
        await canary.observe(sid, REGRESSED, None, [])
    before = canary.block_until(sid, RESCALE) - canary._rolled_back[sid][RESCALE].decided_at
    await _touch(canary, [_source()])
    assert not _in_memory(canary, sid)
    await _touch(canary, [sid])
    assert canary.block_until(sid, RESCALE) - repair_canary._utc(canary._rolled_back[sid][RESCALE].decided_at) == before


async def test_eviction_racing_a_heal_of_the_same_source_loses_no_verdict(monkeypatch):
    """A rollback is being written for S (its heal holds S's lock) while a second heal
    of S waits for the lock and other sources churn the cache past its size. S must
    stay in memory until both are done, and the waiting heal must see the rollback."""
    canary = RepairCanary(N, max_resident=1)
    sid = _source()
    await _open_watch(canary, sid)
    gate, writing = asyncio.Event(), asyncio.Event()
    real = repair_canary._persist

    async def slow_persist(watches):
        if any(w.source_id == sid and w.status == repair_canary.ROLLED_BACK for w in watches):
            writing.set()
            await gate.wait()
        return await real(watches)

    monkeypatch.setattr(repair_canary, "_persist", slow_persist)

    async def first():
        async with canary.lock(sid):
            await canary.sync(sid, shared=False)
            return await canary.observe(sid, REGRESSED, None, [])

    async def second():
        async with canary.lock(sid):
            await canary.sync(sid, shared=False)
            return canary.blocked(sid)

    a = asyncio.create_task(first())
    await writing.wait()
    b = asyncio.create_task(second())
    await asyncio.sleep(0)
    await _touch(canary, [_source() for _ in range(10)])
    assert sid in canary._resident and sid in canary._locks
    gate.set()
    assert (await a).rolled_back
    assert await b == frozenset({RESCALE})
    assert sid not in canary._locks

    await _touch(canary, [_source() for _ in range(3)])
    assert not _in_memory(canary, sid)
    await _touch(canary, [sid])
    assert canary.blocked(sid) == frozenset({RESCALE})


async def test_a_dropped_source_gets_the_same_heal_answers(monkeypatch):
    """End to end through /uasr/heal: the same batches give the same rows, repairs and
    canary verdicts whether the sources stayed in memory or were dropped in between."""

    def norm(out: dict, sid: str):
        out = copy.deepcopy(out)
        for key in ("batch_id", "gate", "drift_event_id", "recovery_id", "latency_seconds"):
            out.pop(key, None)
        for group in (out.get("canary") or {}).values():
            for w in group:
                for k in ("watch_id", "recovery_id", "source_id"):
                    w.pop(k, None)
        return repr(out).replace(sid, "<src>")

    async def scenario(evict: bool):
        canary = RepairCanary(N, max_resident=1)
        monkeypatch.setattr(service, "_canary", canary)
        outs = []
        watched, rolled = _source(), _source()
        for sid in (watched, rolled):
            _register(sid)
            outs.append(norm(await _heal(sid, _x100(_rows(300, 100))), sid))
        outs.append(norm(await _heal(watched, _x100(_rows(300, 101))), watched))
        outs.append(norm(await _heal(rolled, _shifted_x100(_rows(300, 102))), rolled))
        if evict:
            await _touch(canary, [_source() for _ in range(3)])
            assert not _in_memory(canary, watched) and not _in_memory(canary, rolled)
        outs.append(norm(await _heal(watched, _x100(_rows(300, 103))), watched))
        outs.append(norm(await _heal(rolled, _x100(_rows(300, 104))), rolled))
        return outs

    kept, dropped = await scenario(False), await scenario(True)
    assert kept == dropped
    assert "'status': 'quarantined'" in kept[-1] or "'rows': []" in kept[-1]


# ── BUG-579 review: what detect() alone must still catch ─────────────────────

# (rows, mean, std) of a batch against 400 rows of N(100, 10): a 2-3 sigma shift whose
# values stay inside the baseline's range. Before the fix 13-15 of 20 seeds drifted.
INSIDE_RANGE_SHIFTS = [(20, 125.0, 10.0), (30, 122.0, 5.0), (10, 130.0, 10.0)]


@pytest.mark.parametrize("n,mu,sd", INSIDE_RANGE_SHIFTS)
def test_a_shift_inside_the_baselines_range_drifts_on_detect_alone(n, mu, sd):
    """/uasr/ingest, the MAPE-K worker and the recovery loop call detect() with no
    rank test after it. Chebyshev cannot prove a shift whose values all sit inside
    the baseline's range, and below 40 rows _aligned_drift_p tests no interior
    bins, so these batches came back clean; the rank-sum test proves them."""
    hits = 0
    for seed in range(20):
        rnd = random.Random(seed)
        det = DriftDetector()
        det.register_baseline("r", BatchPayload(source_id="r", columns=["x"],
                                                rows=[{"x": rnd.gauss(100.0, 10.0)} for _ in range(400)]))
        batch = BatchPayload(source_id="r", columns=["x"],
                             rows=[{"x": rnd.gauss(mu, sd)} for _ in range(n)])
        hits += det.detect(batch, record_state=False).drift_detected
    assert hits >= 18, f"{hits}/20"


async def test_ingest_reports_a_proven_shift_as_drift():
    """/uasr/ingest answers {status: clean} on detect()'s verdict alone."""
    clean = 0
    for seed in range(20):
        source = _source()
        rnd = random.Random(seed)
        service._detector.register_baseline(source, BatchPayload(
            source_id=source, columns=["x"], rows=[{"x": rnd.gauss(100.0, 10.0)} for _ in range(400)]))
        req = service.IngestRequest(source_id=source, rows=[{"x": rnd.gauss(125.0, 10.0)} for _ in range(20)])
        async with get_session_factory()() as db:
            out = await service.ingest_batch(req, _as(), db=db)
        clean += out.get("status") == "clean"
    assert clean <= 2, f"{clean}/20 shifted batches answered clean"


# ── BUG-579 re-review: exact small-sample rank test; interior rank test ──────

# (rows, mean, std) of a batch against 400 rows of N(100, 10), with the paths that must
# catch it. 2-8 rows ~3 sigma out: the |z| > 5 normal cut cannot be reached below ~9
# rows (|z| <= sqrt(3 n1)), so before the fix 5/20 and 10/20 drifted on every path,
# /uasr/heal included, though the exact Mann-Whitney p was 3e-8..7e-14. 35-40 rows
# just under 2 sigma inside the range: the rank test ran only past 2 sigma, and
# _aligned_drift_p tests no interior bins below 40 rows, so detect() alone (/uasr/ingest,
# the MAPE-K worker, the recovery loop) caught 16/20 and 15/20 (rank z 7.5-8.9).
REVIEW_REPROS = [(5, 130.0, 3.0, True), (8, 130.0, 5.0, True),
                 (40, 121.0, 10.0, False), (35, 121.0, 10.0, False)]


def _gauss_case(seed: int, n: int, mu: float, sd: float):
    rnd = random.Random(seed)
    base = [{"x": rnd.gauss(100.0, 10.0)} for _ in range(400)]
    return base, [{"x": rnd.gauss(mu, sd)} for _ in range(n)]


@pytest.mark.parametrize("n,mu,sd,via_heal", REVIEW_REPROS)
async def test_review_repros_drift_on_detect_and_heal(n, mu, sd, via_heal):
    detected = healed_clean = 0
    for seed in range(20):
        base, rows = _gauss_case(seed, n, mu, sd)
        det = DriftDetector()
        det.register_baseline("r", BatchPayload(source_id="r", columns=["x"], rows=base))
        detected += det.detect(BatchPayload(source_id="r", columns=["x"], rows=copy.deepcopy(rows)),
                               record_state=False).drift_detected
        if via_heal:
            sid = _source()
            service._detector.register_baseline(sid, BatchPayload(source_id=sid, columns=["x"], rows=base))
            out = await _heal(sid, rows)
            healed_clean += not out["quarantined_rows"] and out["rows"] == rows
    assert detected == 20, f"detect(): {detected}/20"
    assert healed_clean == 0, f"/uasr/heal returned {healed_clean}/20 shifted batches as clean"


def test_exact_rank_p_is_the_permutation_distribution_with_ties():
    """Brute force over every subset of a small tied pool: the DP is the exact
    conditional (tie-respecting) permutation p-value, and doubling one tail keeps
    it valid two-sided."""
    import itertools
    rnd = random.Random(3)
    for _ in range(60):
        t = [rnd.randint(0, 3) for _ in range(rnd.randint(2, 5))]
        if sum(t) < 3:
            continue
        pool = [j for j, c in enumerate(t) for _ in range(c)]
        n1 = rnd.randint(1, len(pool) - 1)
        chosen = rnd.sample(range(len(pool)), n1)
        p = [sum(1 for i in chosen if pool[i] == j) for j in range(len(t))]
        q = [c - x for c, x in zip(t, p)]
        cum, rank = 0, []
        for c in t:
            rank.append(cum + (c + 1) / 2)
            cum += c
        w_obs = sum(x * r for x, r in zip(p, rank))
        sums = [sum(rank[pool[i]] for i in comb) for comb in itertools.combinations(range(len(pool)), n1)]
        for upper in (True, False):
            tail = sum((w >= w_obs - 1e-9) if upper else (w <= w_obs + 1e-9) for w in sums)
            want = min(1.0, 2 * tail / len(sums))
            assert DriftDetector._exact_rank_p(p, q, upper) == pytest.approx(want, rel=1e-9, abs=1e-12)


# Clean micro-batches of every shape the exact test must not misread: a normal, two
# heavy tails, a 15% boolean and a small-integer count (both nearly all ties).
SWEEP_COLS = ["normal", "lognormal", "pareto", "flag", "count"]
SIZE_CLASSES = [(lo, lo + 4) for lo in range(1, 51, 5)]  # 1-5, 6-10, ..., 46-50
SWEEP_SEEDS = 200


def _sweep_rows(rng, n: int) -> list:
    import numpy as np
    a, b = rng.normal(100.0, 10.0, n), rng.lognormal(0.0, 2.0, n)
    c, d, e = rng.pareto(1.2, n) + 1.0, rng.random(n) < 0.15, rng.poisson(3, n)
    return [{"normal": float(a[i]), "lognormal": float(b[i]), "pareto": float(c[i]),
             "flag": bool(d[i]), "count": int(e[i])} for i in range(n)]


def _sweep_cases():
    """(seed, size class, baseline rows, batch rows): SWEEP_SEEDS seeds per size class."""
    import numpy as np
    for lo, hi in SIZE_CLASSES:
        for seed in range(SWEEP_SEEDS):
            rng = np.random.default_rng([lo, seed])
            yield seed, (lo, hi), _sweep_rows(rng, 400), _sweep_rows(rng, lo + seed % (hi - lo + 1))


def test_clean_micro_batch_sweep_raises_no_drift_on_detect():
    det = DriftDetector()
    loop = RecoveryLoop(detector=det, config=RecoveryLoopConfig())
    found, per_class = [], {}
    for seed, cls, base, rows in _sweep_cases():
        sid = f"sweep_{cls[0]}_{seed}"
        det.register_baseline(sid, BatchPayload(source_id=sid, columns=SWEEP_COLS, rows=base))
        b = BatchPayload(source_id=sid, columns=SWEEP_COLS, rows=rows)
        r = det.detect(b, record_state=False)
        per_class[cls] = per_class.get(cls, 0) + 1
        if r.drift_detected:
            found.append((cls, seed, len(rows), r.details))
        elif loop.unexplained_shift(r, b) is not None:
            found.append((cls, seed, len(rows), "rank shift"))
    assert all(v >= SWEEP_SEEDS for v in per_class.values()) and len(per_class) == len(SIZE_CLASSES)
    assert found == []


async def test_clean_micro_batch_sweep_quarantines_nothing_through_heal():
    found = []
    for seed, cls, base, rows in _sweep_cases():
        sid = _source()
        service._detector.register_baseline(sid, BatchPayload(source_id=sid, columns=SWEEP_COLS, rows=base))
        req = service.IngestRequest(source_id=sid, columns=SWEEP_COLS, rows=copy.deepcopy(rows))
        async with get_session_factory()() as db:
            out = await service.heal_batch(req, _as(), db=db)
        if out["quarantined_rows"] or out["degraded"] or out["rows"] != rows:
            found.append((cls, seed, len(rows), out.get("drift_type"), out.get("reason")))
    assert found == []


def test_end_rows_p_value_covers_every_split_of_them():
    """Two clean rows of a 2-row batch both outside a 2,000-row baseline's range: the
    exact chance is 3 splits (both low, one each, both high) x 2/2002 x 1/2001 =
    1.5e-6, not past 1e-6. The single-split product (5e-7) fired on it."""
    exact = 3 * (2 / 2002) * (1 / 2001)
    p = DriftDetector._aligned_drift_p([1, 0, 1], [2000])
    assert p >= exact * (1 - 1e-9)
    assert p >= 1e-6
    # Many rows far outside are still proven.
    assert DriftDetector._aligned_drift_p([5, 0, 5], [2000]) < 1e-6


async def test_status_view_reads_only_the_callers_tenant_rows():
    """BUG-578 review: visible() read every tenant's watch history; the tenant filter
    is now in SQL, with LIKE wildcards in the tenant escaped."""
    canary = RepairCanary(N)
    mine = f"t_{uuid.uuid4().hex[:6]}"
    lookalike = mine.replace("_", "X", 1)  # matches "t_..." if '_' were a wildcard
    a, b, c = f"{mine}::s1", f"{lookalike}::s1", f"{mine}x::s1"
    for sid in (a, b, c):
        await _open_watch(canary, sid)
    rows = await load_watches(tenant=mine)
    assert {r.source_id for r in rows} == {a}
    fresh = RepairCanary(N)
    view = await fresh.visible(mine)
    listed = {w["source_id"] for k in ("watching", "kept", "rolled_back") for w in view.get(k, [])}
    assert listed == {a}, view
