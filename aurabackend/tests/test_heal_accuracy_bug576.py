"""ADR-002 accuracy: BUG-576 -- a column's name never takes a drift check away.

_ID_NAME_WORDS included num, number, no and nr, so a wide, unique, null-free
integer MEASURE named visit_num or total_number was classified as a keyed id, and an
identifier skips statistical drift: a reviewer's probe on seat_num and
sequence_number caught x2, x1.5 and an in-range shift in 0 of 20 trials each. Now a
column is an identifier only on value evidence (a sequence, a stable text code) or a
declaration (key_columns, an id-like declared type); a name hint may only ADD a
check -- it makes the baseline's rows unique for the duplicate check.

Real DriftDetector throughout, and the real /uasr/heal handler (through the accuracy
benchmark's harness) on an isolated SQLite file; the FastAPI lifespan is never driven.
"""
from __future__ import annotations

import asyncio
import os
import random
import sys
import tempfile
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
from tests.accuracy.datasets import Column, Dataset, copy_rows  # noqa: E402
from tests.accuracy.faults import Trial  # noqa: E402
from tests.accuracy.harness import CORRECT, WRONG, classify, classify_clean, heal  # noqa: E402
from uasr.actuator_agent import SynthesisActuatorAgent  # noqa: E402
from uasr.db import init_uasr_db  # noqa: E402
from uasr.drift_detector import DriftDetector  # noqa: E402
from uasr.models import BatchPayload, DriftType  # noqa: E402
from uasr.reflector_agent import DiagnosticReflectorAgent  # noqa: E402

# The reviewer's probe names, and the id-word names the old heuristic also caught.
MEASURE_NAMES = ["seat_num", "sequence_number", "visit_num", "total_number", "seat_no", "lot_nr",
                 "CustomerID", "account_key"]
TRIALS = 20

FAULTS = {
    "x2": lambda rnd, v: v * 2,
    "x1.5": lambda rnd, v: int(v * 1.5),
    "in-range shift": lambda rnd, v: rnd.randint(5_000_000, 10_000_000),
}


def _payload(rows, bid="b", snapshot=None, metadata=None) -> BatchPayload:
    return BatchPayload(source_id="s", batch_id=bid, columns=list(rows[0]), rows=rows,
                        schema_snapshot=snapshot, metadata=metadata or {})


def _wide_rows(rnd: random.Random, name: str, n: int = 500):
    """A wide integer measure: unique by chance, null-free, on a sparse range."""
    return [{name: rnd.randint(1000, 10_000_000), "amount": round(rnd.uniform(5.0, 900.0), 2)}
            for _ in range(n)]


def _detector(name: str, seed: int = 0, **kw):
    rnd = random.Random(seed)
    det = DriftDetector()
    det.register_baseline("s", _payload(_wide_rows(rnd, name), "base", **kw))
    return det, rnd


@pytest.mark.parametrize("name", MEASURE_NAMES)
def test_an_id_like_name_alone_leaves_a_wide_integer_measure_a_measure(name):
    det, _ = _detector(name)
    d = det.baseline_distributions("s")[name]
    assert d.role == "measure" and d.value_bound is None


@pytest.mark.parametrize("fault", sorted(FAULTS))
@pytest.mark.parametrize("name", MEASURE_NAMES)
def test_faults_in_an_id_named_measure_are_caught_in_every_trial(name, fault):
    det, rnd = _detector(name)
    missed = []
    for t in range(TRIALS):
        rows = [{**r, name: FAULTS[fault](rnd, r[name])} for r in _wide_rows(rnd, name)]
        r = det.detect(_payload(rows, f"f{t}"), record_state=False)
        if not (r.drift_detected and name in r.affected_columns):
            missed.append((t, r.details))
    assert not missed, missed[:3]


@pytest.mark.parametrize("name", MEASURE_NAMES)
def test_clean_batches_of_an_id_named_measure_raise_nothing(name):
    det, rnd = _detector(name)
    alarms = [(t, r.details) for t in range(TRIALS)
              if (r := det.detect(_payload(_wide_rows(rnd, name), f"c{t}"), record_state=False)).drift_detected]
    assert alarms == []


@pytest.mark.parametrize("name", ["visit_num", "customer_id"])
def test_the_name_still_adds_the_exact_duplicate_check(name):
    # What the name used to give a keyed id and may still give (it adds a check): the
    # baseline's rows are unique, so one copied row in a batch is an exact alert.
    det, rnd = _detector(name)
    assert det._store.peek("s").baseline_rows["unique_columns"] == [name]
    rows = _wide_rows(rnd, name, 300)
    rows.append(dict(rows[7]))
    r = det.detect(_payload(rows, "dup"), record_state=False)
    assert r.drift_types == [DriftType.DUPLICATE], r.details


def test_without_an_id_like_name_a_chance_unique_measure_adds_no_duplicate_check():
    det, _ = _detector("visits")
    assert det._store.peek("s").baseline_rows["unique_columns"] == []


# ── True identifiers: still identifiers on value evidence, still no false alarm ──


def _seq_rows(rnd, start, n, name):
    return [{name: start + i, "ref": f"VIS-{start + i:07d}", "amount": round(rnd.uniform(5.0, 900.0), 2)}
            for i in range(n)]


@pytest.mark.parametrize("name", ["visit_num", "order_id", "sequence_number"])
def test_a_true_sequential_id_is_still_an_identifier_and_never_alarms(name):
    for seed in range(TRIALS):
        rnd = random.Random(seed)
        det = DriftDetector()
        det.register_baseline("s", _payload(_seq_rows(rnd, 1, 800, name), "base"))
        base = det.baseline_distributions("s")
        assert base[name].role == "identifier" and base["ref"].role == "identifier"
        nxt = 801
        for size in (60, 300, 3000):
            r = det.detect(_payload(_seq_rows(rnd, nxt, size, name), f"{seed}-{size}"), record_state=False)
            nxt += size
            assert not r.drift_detected, (seed, size, r.details)


def _keyed_rows(start, n, name):
    # A unique key spread over a fixed population: an affine map mod a prime.
    return [{name: 1 + (7919 * (start + i) * 104729) % 9_999_991, "amount": float(500 + (i * 37) % 400)}
            for i in range(n)]


@pytest.mark.parametrize("order", ["in order", "shuffled"])
@pytest.mark.parametrize("name", ["customer_id", "ref_value"])
@pytest.mark.parametrize("metadata", [None, "declared"])
def test_a_scrambled_counter_is_an_id_by_its_values_and_never_alarms(name, metadata, order):
    # What the name hint used to stand in for, shown by the values instead: sorted, the
    # values of k * stride mod p step by at most three sizes. Any name; declared or
    # not; in any row order (PR #806 review: no id evidence from row order).
    meta = {"key_columns": [name]} if metadata else None
    rows = _keyed_rows(1, 800, name)
    if order == "shuffled":
        random.Random(3).shuffle(rows)
    det = DriftDetector()
    det.register_baseline("s", _payload(rows, "base", metadata=meta))
    d = det.baseline_distributions("s")[name]
    assert d.role == "identifier" and d.value_bound is not None
    nxt = 801
    for t in range(TRIALS):
        for size in (60, 300, 3000):
            r = det.detect(_payload(_keyed_rows(nxt, size, name), f"{t}-{size}", metadata=meta),
                           record_state=False)
            nxt += size
            assert not r.drift_detected, (t, size, r.details)


def _gapped_rows(rnd, start, n, name):
    """An auto-increment with deleted rows: increasing, random gaps."""
    rows, v = [], start
    for _ in range(n):
        v += rnd.randint(1, 400)
        rows.append({name: v, "amount": round(rnd.uniform(5.0, 900.0), 2)})
    return rows, v


def _reorder(rows, how, rnd):
    rows = list(rows)
    if how == "shuffled":
        rnd.shuffle(rows)
    elif how == "2% swapped":
        for _ in range(len(rows) // 50):
            i = rnd.randrange(len(rows) - 1)
            rows[i], rows[i + 1] = rows[i + 1], rows[i]
    return rows


@pytest.mark.parametrize("how", ["in order", "2% swapped", "shuffled"])
@pytest.mark.parametrize("name", ["order_id", "visit_num", "row"])
def test_a_gapped_auto_increment_never_alarms_whatever_its_row_order(name, how):
    # PR #806 review: the id evidence was 99% of row steps going one way, a cliff --
    # with 2% of rows out of order (concurrent inserts) every clean batch alarmed. A
    # sparse unique int is a sequence candidate whatever its order, and a batch that
    # continues past the baseline is judged as a sequence.
    for seed in range(TRIALS):
        rnd = random.Random(seed)
        rows, last = _gapped_rows(rnd, 1000, 800, name)
        det = DriftDetector()
        det.register_baseline("s", _payload(_reorder(rows, how, rnd), "base"))
        assert det.baseline_distributions("s")[name].sequence_span is not None
        for size in (60, 300, 3000):
            rows, last = _gapped_rows(rnd, last, size, name)
            r = det.detect(_payload(_reorder(rows, how, rnd), f"{seed}-{size}"), record_state=False)
            assert not r.drift_detected, (seed, size, r.details)


def _sorted_rows(rnd, name, n, fault=None):
    """A wide integer measure exported sorted (ORDER BY): monotone like an id's row order."""
    vals = sorted(rnd.sample(range(1000, 10_000_000), n))
    if fault is not None:
        vals = sorted(SORTED_FAULTS[fault](rnd, v) for v in vals)
    return [{name: v, "amount": round(rnd.uniform(5.0, 900.0), 2)} for v in vals]


SORTED_FAULTS = {**FAULTS, "x10": lambda rnd, v: v * 10}


@pytest.mark.parametrize("fault", sorted(SORTED_FAULTS))
@pytest.mark.parametrize("name", ["revenue", "visit_num"])
def test_faults_in_a_sorted_measure_are_caught_in_every_trial(name, fault):
    # Row order alone made it a sequence id, and an id skips statistical drift. A batch
    # reaching back below the baseline's median is no sequence, so it is checked as a
    # measure: ambiguous => keep the checks.
    missed = []
    for t in range(TRIALS):
        rnd = random.Random(t)
        det = DriftDetector()
        det.register_baseline("s", _payload(_sorted_rows(rnd, name, 500), "base"))
        r = det.detect(_payload(_sorted_rows(rnd, name, rnd.randint(60, 600), fault), f"f{t}"),
                       record_state=False)
        if not (r.drift_detected and name in r.affected_columns):
            missed.append((t, r.details))
    assert not missed, missed[:3]


@pytest.mark.parametrize("name", ["revenue", "visit_num"])
def test_clean_batches_of_a_sorted_measure_raise_nothing(name):
    alarms = []
    for t in range(TRIALS):
        rnd = random.Random(1000 + t)
        det = DriftDetector()
        det.register_baseline("s", _payload(_sorted_rows(rnd, name, 500), "base"))
        for size in (60, 300, 1500):
            r = det.detect(_payload(_sorted_rows(rnd, name, size), f"c{t}-{size}"), record_state=False)
            if r.drift_detected:
                alarms.append((t, size, r.details))
    assert alarms == []


# ── Through the real /uasr/heal handler ─────────────────────────────────────────


@pytest.fixture(scope="module")
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_heal576_{uuid.uuid4().hex[:8]}.db")
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


@pytest.fixture
async def _heal_db(_isolated_metadata_db, monkeypatch):
    await init_uasr_db()
    monkeypatch.setattr(SynthesisActuatorAgent, "_llm_shim", _no_llm)
    monkeypatch.setattr(DiagnosticReflectorAgent, "_llm_diagnosis", _no_llm)
    yield


def _trial(name: str, seed: int, fault=None) -> Trial:
    rnd = random.Random(seed)
    cols = [Column(name, "count", 0.0, {"dtype": "int"}), Column("amount", "uniform", 0.0, {"dtype": "float"})]
    ds = Dataset(seed=seed, columns=cols, baseline=_wide_rows(rnd, name), truth=_wide_rows(rnd, name, 300))
    sent = copy_rows(ds.truth)
    expected = copy_rows(ds.truth)
    if fault is not None:
        for i, row in enumerate(sent):
            row[name] = FAULTS[fault](rnd, row[name])
            if row[name] != ds.truth[i][name]:
                expected[i] = None  # no power of ten explains it: the row must be held
    return Trial(fault=fault or "clean", seed=seed, dataset=ds, sent=sent, expected=expected,
                 snapshot=dict(ds.schema), tenant=None)


@pytest.mark.parametrize("fault", sorted(FAULTS))
@pytest.mark.parametrize("name", ["seat_num", "visit_num"])
async def test_heal_never_returns_the_faulted_id_named_measure(_heal_db, name, fault):
    for seed in range(5):
        trial = _trial(name, seed, fault)
        result = classify(trial, await heal(trial))
        assert result.outcome != WRONG, (seed, result.why)


@pytest.mark.parametrize("name", ["seat_num", "visit_num"])
async def test_heal_returns_clean_id_named_measure_batches_untouched(_heal_db, name):
    for seed in range(5):
        trial = _trial(name, seed)
        result = classify_clean(trial, await heal(trial))
        assert result.outcome == CORRECT, (seed, result.why)


# ── PR #806 review: id evidence that does not depend on row order ───────────────
# A tenant's slice of a global auto-increment: 900 sparse ids in [1.0M, 1.4M), the
# first 600 the baseline, the next 200 a batch. bc9e19a made such a column an id only
# when its rows came in id order (99% of steps one way); otherwise it was a measure
# and every clean batch, all past the baseline, alarmed.


def _orders(ids, rnd, col="order_id"):
    return [{col: int(i), "amount": round(rnd.lognormvariate(3, 1), 2),
             "status": rnd.choice(["new", "paid", "shipped"])} for i in ids]


def _population(seed=7):
    rnd = random.Random(seed)
    pop = sorted(rnd.sample(range(1_000_000, 1_400_000), 900))
    return pop[:600], pop[600:800], rnd


def _orders_detector(base_ids, rnd, col="order_id"):
    det = DriftDetector()
    det.register_baseline("s", _payload(_orders(base_ids, rnd, col), "base"))
    return det


@pytest.mark.parametrize("col", ["order_id", "ref"])
@pytest.mark.parametrize("order", ["shuffled", "in order"])
def test_a_growing_sparse_id_never_alarms_in_any_row_order(col, order):
    base, nxt, rnd = _population()
    if order == "shuffled":
        rnd.shuffle(base)
        rnd.shuffle(nxt)
    det = _orders_detector(base, rnd, col)
    r = det.detect(_payload(_orders(nxt, rnd, col), "b"), record_state=False)
    assert not r.drift_detected, r.details


def test_a_batch_re_sending_an_old_id_still_continues_the_sequence():
    # An upsert / CDC update of an old order among new ones: "any value back below the
    # median" made bc9e19a check the column as a measure and quarantine all 200 rows.
    base, nxt, rnd = _population()
    det = _orders_detector(base, rnd)
    ids = list(nxt)
    ids[50] = base[10]
    r = det.detect(_payload(_orders(ids, rnd), "b"), record_state=False)
    assert not r.drift_detected, r.details


@pytest.mark.parametrize("order", ["in order", "shuffled"])
def test_a_unit_bug_on_a_growing_sparse_id_is_a_format_change(order):
    # x1000 on order_id: a sequence's batch past 4x the baseline's largest id. bc9e19a
    # dropped the bound for row-ordered ids, and this was silent.
    base, nxt, rnd = _population()
    if order == "shuffled":
        rnd.shuffle(base)
        rnd.shuffle(nxt)
    det = _orders_detector(base, rnd)
    r = det.detect(_payload(_orders([i * 1000 for i in nxt], rnd), "b"), record_state=False)
    assert r.drift_detected and r.drift_vector.get("type") == "format_change", r.details
    assert "order_id" in r.affected_columns


def test_an_inferred_id_repeated_in_a_batch_is_no_exact_duplicate():
    # Insert + update of one new order in the same batch. Exact duplicates are for a
    # declared key; an inferred sparse id keeps the statistical rule (as at fea7658).
    base, nxt, rnd = _population()
    det = _orders_detector(base, rnd)
    rows = _orders(nxt, rnd)
    rows.append({**rows[10], "status": "paid" if rows[10]["status"] != "paid" else "new"})
    r = det.detect(_payload(rows, "b"), record_state=False)
    assert DriftType.DUPLICATE not in r.drift_types, r.details


async def test_heal_returns_a_clean_batch_of_a_shuffled_growing_id(_heal_db):
    base, nxt, rnd = _population()
    rnd.shuffle(base)
    rnd.shuffle(nxt)
    cols = [Column("order_id", "count", 0.0, {"dtype": "int"}), Column("amount", "uniform", 0.0, {"dtype": "float"}),
            Column("status", "categorical", 0.0, {"dtype": "str"})]
    ds = Dataset(seed=7, columns=cols, baseline=_orders(base, rnd), truth=_orders(nxt, rnd))
    trial = Trial(fault="clean", seed=7, dataset=ds, sent=copy_rows(ds.truth), expected=copy_rows(ds.truth),
                  snapshot=dict(ds.schema), tenant=None)
    result = classify_clean(trial, await heal(trial))
    assert result.outcome == CORRECT, result.why
