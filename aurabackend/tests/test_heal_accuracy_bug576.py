"""ADR-002 accuracy: BUG-576 -- a count word in a column's name never takes a drift
check away; and what does make a sparse integer column an identifier.

_ID_NAME_WORDS included num, number, no and nr, so a wide, unique, null-free
integer MEASURE named visit_num or total_number was classified as a keyed id, and an
identifier skips statistical drift: a reviewer's probe on seat_num and
sequence_number caught x2, x1.5 and an in-range shift in 0 of 20 trials each. Those
words name a count or an ordinal as often as a key, so they are no longer id words:
such a column is a measure unless its key or id type is declared.

PR #806 reviews: the identifier evidence is fea7658's again -- collision evidence for
a sequence, a stable text code, and for a sparse unique int a declared key, an
id-like declared type or a name whose last word names a key (customer_id,
account_key), with the 4x value bound. Two rounds of other evidence each regressed:
row order (a 99% monotone test) quarantined clean batches of sparse ids with 2% of
their rows out of order, and the value set's shape (a "sequence candidate" that a
batch past its baseline "continued") quarantined clean CDC and overlap batches and
let a unique integer measure (amount_cents) skip every measure check.

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

import numpy as np
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

# The reviewer's probe names, and the other count words the old heuristic caught.
MEASURE_NAMES = ["seat_num", "sequence_number", "visit_num", "total_number", "seat_no", "lot_nr"]
# Names whose last word names a key: still an id, by fea7658's rule.
KEY_NAMES = ["customer_id", "CustomerID", "userId", "account_key", "order_id"]
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
def test_a_count_word_in_the_name_leaves_a_wide_integer_measure_a_measure(name):
    det, _ = _detector(name)
    d = det.baseline_distributions("s")[name]
    assert d.role == "measure" and d.value_bound is None


@pytest.mark.parametrize("name", KEY_NAMES)
def test_a_name_that_names_a_key_still_makes_a_sparse_unique_int_a_keyed_id(name):
    det, _ = _detector(name)
    d = det.baseline_distributions("s")[name]
    assert d.role == "identifier" and d.value_bound is not None


@pytest.mark.parametrize("how", ["key_columns", "declared_type"])
def test_a_declared_key_or_id_type_still_makes_a_count_named_column_a_keyed_id(how):
    kw = {"key_columns": dict(metadata={"key_columns": ["visit_num"]}),
          "declared_type": dict(snapshot={"visit_num": "serial", "amount": "float"})}[how]
    det, _ = _detector("visit_num", **kw)
    d = det.baseline_distributions("s")["visit_num"]
    assert d.role == "identifier" and d.value_bound is not None


@pytest.mark.parametrize("fault", sorted(FAULTS))
@pytest.mark.parametrize("name", MEASURE_NAMES)
def test_faults_in_a_count_named_measure_are_caught_in_every_trial(name, fault):
    det, rnd = _detector(name)
    missed = []
    for t in range(TRIALS):
        rows = [{**r, name: FAULTS[fault](rnd, r[name])} for r in _wide_rows(rnd, name)]
        r = det.detect(_payload(rows, f"f{t}"), record_state=False)
        if not (r.drift_detected and name in r.affected_columns):
            missed.append((t, r.details))
    assert not missed, missed[:3]


@pytest.mark.parametrize("name", MEASURE_NAMES)
def test_clean_batches_of_a_count_named_measure_raise_nothing(name):
    det, rnd = _detector(name)
    alarms = [(t, r.details) for t in range(TRIALS)
              if (r := det.detect(_payload(_wide_rows(rnd, name), f"c{t}"), record_state=False)).drift_detected]
    assert alarms == []


def test_a_key_named_id_makes_a_copied_row_an_exact_duplicate():
    # A keyed id makes the baseline's rows unique by construction (BUG-574), so one
    # copied row in a batch is an exact alert.
    det, rnd = _detector("customer_id")
    assert det._store.peek("s").baseline_rows["unique_columns"] == ["customer_id"]
    rows = _wide_rows(rnd, "customer_id", 300)
    rows.append(dict(rows[7]))
    r = det.detect(_payload(rows, "dup"), record_state=False)
    assert r.drift_types == [DriftType.DUPLICATE], r.details


@pytest.mark.parametrize("name", ["visit_num", "visits"])
def test_a_measure_unique_by_chance_adds_no_duplicate_check(name):
    det, _ = _detector(name)
    assert det._store.peek("s").baseline_rows["unique_columns"] == []


# ── True identifiers: still identifiers, still no false alarm ───────────────────


def _seq_rows(rnd, start, n, name):
    return [{name: start + i, "ref": f"VIS-{start + i:07d}", "amount": round(rnd.uniform(5.0, 900.0), 2)}
            for i in range(n)]


@pytest.mark.parametrize("name", ["visit_num", "order_id", "sequence_number"])
def test_a_true_sequential_id_is_still_an_identifier_and_never_alarms(name):
    # A dense sequence is an id by its values (collision evidence), whatever its name.
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
@pytest.mark.parametrize("name,metadata", [("customer_id", None), ("customer_id", "declared"),
                                           ("ref_value", "declared"), ("ref_value", None)])
def test_a_scrambled_counter_never_alarms(name, metadata, order):
    # Named or declared as a key it is a keyed id; undeclared under another name it is
    # a measure (its value set is no evidence), and its clean batches still raise
    # nothing -- a run of counters k * stride mod p spreads over the population.
    meta = {"key_columns": [name]} if metadata else None
    rows = _keyed_rows(1, 800, name)
    if order == "shuffled":
        random.Random(3).shuffle(rows)
    det = DriftDetector()
    det.register_baseline("s", _payload(rows, "base", metadata=meta))
    d = det.baseline_distributions("s")[name]
    if name == "customer_id" or metadata:
        assert d.role == "identifier" and d.value_bound is not None
    else:
        assert d.role == "measure"
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
@pytest.mark.parametrize("name,kw", [
    ("order_id", {}),
    ("row", {"metadata": {"key_columns": ["row"]}}),
    ("row", {"snapshot": {"row": "serial", "amount": "float"}}),
])
def test_a_gapped_auto_increment_with_key_evidence_never_alarms_in_any_row_order(name, kw, how):
    # Row order is no evidence either way (PR #806 review: 2% of rows out of order made
    # an id a measure). The batches grow the sequence to 2.7x the baseline's largest id,
    # inside the keyed id's 4x bound.
    for seed in range(TRIALS):
        rnd = random.Random(seed)
        rows, last = _gapped_rows(rnd, 1000, 800, name)
        det = DriftDetector()
        det.register_baseline("s", _payload(_reorder(rows, how, rnd), "base", **kw))
        assert det.baseline_distributions("s")[name].role == "identifier"
        for size in (60, 300, 1000):
            rows, last = _gapped_rows(rnd, last, size, name)
            r = det.detect(_payload(_reorder(rows, how, rnd), f"{seed}-{size}", **kw), record_state=False)
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


# ── PR #806 second review: a unique integer measure is no sequence ──────────────
# amount_cents: 400 unique cents in [100, 1,000,000), a batch of cents from the same
# range. ec174b9 took such a column for a "sequence candidate", and a batch with
# fewer than 10% of its values inside the baseline's range was judged as a sequence:
# no measure check, no sandbox hold, no cast proof, no canary contradiction.

_CENTS_FAULTS = [("x10, 2000-row page", 10, 0, 2000), ("x20, 2000-row page", 20, 0, 2000),
                 ("x10, 400-row page", 10, 0, 400), ("+1,000,000 offset", 1, 1_000_000, 400),
                 ("x3", 3, 0, 400)]


def _cents(rng, rnd, vals):
    return [{"amount_cents": int(v), "status": rnd.choice(["new", "paid"])} for v in vals]


@pytest.mark.parametrize("label,fac,off,n1", _CENTS_FAULTS, ids=[c[0] for c in _CENTS_FAULTS])
def test_a_fault_on_a_unique_int_measure_is_always_detected(label, fac, off, n1):
    rng = np.random.default_rng(1)
    missed = []
    for t in range(10):
        rnd = random.Random(t)
        det = DriftDetector()
        det.register_baseline("s", _payload(_cents(rng, rnd, rng.choice(np.arange(100, 1_000_000), 400, replace=False)), "base"))
        assert det.baseline_distributions("s")["amount_cents"].role == "measure"
        batch = rng.integers(100, 1_000_000, n1) * fac + off
        r = det.detect(_payload(_cents(rng, rnd, batch), f"f{t}"), record_state=False)
        if not (r.drift_detected and "amount_cents" in r.affected_columns):
            missed.append((t, r.details))
    assert not missed, missed[:3]


# ── Sparse ids: growth, CDC, overlap, unit bugs (both PR #806 reviews) ──────────
# A tenant's slice of a global auto-increment: sparse ids in [1.0M, 1.4M), the
# first 600 the baseline, the later ones its batches.


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


@pytest.mark.parametrize("order", ["shuffled", "in order"])
def test_a_growing_sparse_id_never_alarms_in_any_row_order(order):
    base, nxt, rnd = _population()
    if order == "shuffled":
        rnd.shuffle(base)
        rnd.shuffle(nxt)
    det = _orders_detector(base, rnd)
    r = det.detect(_payload(_orders(nxt, rnd), "b"), record_state=False)
    assert not r.drift_detected, r.details


@pytest.mark.parametrize("share", [0.01, 0.05, 0.10, 0.20, 0.50])
def test_a_batch_re_sending_old_ids_never_alarms(share):
    # Upserts / CDC updates of older orders among new ones. ec174b9 checked the whole
    # column as a measure once >= 10% of a batch was inside the baseline's range, and
    # flagged 20 of 20 batches at 10%, 20% and 50%.
    flagged = []
    for seed in range(TRIALS):
        rng = np.random.default_rng(seed)
        rnd = random.Random(seed)
        pop = np.sort(rng.choice(np.arange(1_000_000, 1_400_000), 1000, replace=False))
        det = _orders_detector(pop[:600], rnd)
        k = int(round(share * 200))
        ids = list(pop[600:800 - k]) + list(rng.choice(pop[:600], k, replace=False))
        rng.shuffle(ids)
        r = det.detect(_payload(_orders(ids, rnd), f"b{seed}"), record_state=False)
        if r.drift_detected:
            flagged.append((seed, r.details))
    assert not flagged, flagged[:3]


@pytest.mark.parametrize("window", ["rolling: half inside, half new", "status updates only", "next week only"])
def test_a_batch_overlapping_the_baseline_never_alarms(window):
    # A rolling window over the orders, or a batch of status updates to orders the
    # baseline holds: ec174b9 flagged 20 of 20 of the first two.
    flagged = []
    for seed in range(TRIALS):
        rng = np.random.default_rng(seed)
        rnd = random.Random(seed)
        pop = 1_000_000 + np.cumsum(rng.integers(1, 60, 1600))
        det = DriftDetector()
        det.register_baseline("s", _payload(_orders(pop[:400], rnd), "base"))
        ids = {"rolling: half inside, half new": pop[200:600],
               "status updates only": rng.choice(pop[200:400], 200, replace=False),
               "next week only": pop[400:600]}[window]
        r = det.detect(_payload(_orders(list(rng.permutation(ids)), rnd), f"b{seed}"), record_state=False)
        if r.drift_detected:
            flagged.append((seed, r.details))
    assert not flagged, flagged[:3]


@pytest.mark.parametrize("order", ["in order", "shuffled"])
def test_a_unit_bug_on_a_growing_sparse_id_is_a_format_change(order):
    # x1000 on order_id: past the keyed id's 4x bound, in any row order.
    base, nxt, rnd = _population()
    if order == "shuffled":
        rnd.shuffle(base)
        rnd.shuffle(nxt)
    det = _orders_detector(base, rnd)
    r = det.detect(_payload(_orders([i * 1000 for i in nxt], rnd), "b"), record_state=False)
    assert r.drift_detected and r.drift_vector.get("type") == "format_change", r.details
    assert "order_id" in r.affected_columns


def test_a_keyed_id_repeated_in_a_batch_is_no_exact_duplicate():
    # Insert + update of one new order in the same batch: an undeclared keyed id may
    # repeat (fea7658); exact duplicates on a value are for a declared key.
    base, nxt, rnd = _population()
    det = _orders_detector(base, rnd)
    rows = _orders(nxt, rnd)
    rows.append({**rows[10], "status": "paid" if rows[10]["status"] != "paid" else "new"})
    r = det.detect(_payload(rows, "b"), record_state=False)
    assert DriftType.DUPLICATE not in r.drift_types, r.details


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
async def test_heal_never_returns_the_faulted_count_named_measure(_heal_db, name, fault):
    for seed in range(5):
        trial = _trial(name, seed, fault)
        result = classify(trial, await heal(trial))
        assert result.outcome != WRONG, (seed, result.why)


@pytest.mark.parametrize("name", ["seat_num", "visit_num"])
async def test_heal_returns_clean_count_named_measure_batches_untouched(_heal_db, name):
    for seed in range(5):
        trial = _trial(name, seed)
        result = classify_clean(trial, await heal(trial))
        assert result.outcome == CORRECT, (seed, result.why)


def _cents_trial(seed, fac, off, n1) -> Trial:
    rng = np.random.default_rng(100 + seed)
    rnd = random.Random(seed)
    cols = [Column("amount_cents", "count", 0.0, {"dtype": "int"}),
            Column("status", "categorical", 0.0, {"dtype": "str"})]
    base = _cents(rng, rnd, rng.choice(np.arange(100, 1_000_000), 400, replace=False))
    truth = _cents(rng, rnd, rng.integers(100, 1_000_000, n1))
    ds = Dataset(seed=seed, columns=cols, baseline=base, truth=truth)
    sent = [{**r, "amount_cents": r["amount_cents"] * fac + off} for r in truth]
    # A x10**n is restorable (the truth); an offset is not: those rows must be held.
    expected = copy_rows(truth) if off == 0 else [None] * len(truth)
    return Trial(fault=f"x{fac}+{off}", seed=seed, dataset=ds, sent=sent, expected=expected,
                 snapshot=dict(ds.schema), tenant=None)


@pytest.mark.parametrize("fac,off,n1", [(1, 1_000_000, 400), (10, 0, 2000), (20, 0, 2000)],
                         ids=["+1,000,000 offset", "x10, 2000-row pages", "x20, 2000-row pages"])
async def test_heal_never_returns_a_faulted_unique_int_measure(_heal_db, fac, off, n1):
    # ec174b9 returned the offset healed=true, and x10 / x20 unrepaired in 7 and 4 of
    # 10 runs (the reviewer's N1, N1b).
    wrong = []
    for seed in range(10):
        trial = _cents_trial(seed, fac, off, n1)
        result = classify(trial, await heal(trial))
        if result.outcome == WRONG:
            wrong.append((seed, result.why))
    assert not wrong, wrong[:3]


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


async def test_heal_returns_a_clean_cdc_batch_whole(_heal_db):
    # 200 rows, 40 of them updates of older orders (the reviewer's N2): ec174b9 held
    # all 200.
    rng = np.random.default_rng(3)
    rnd = random.Random(3)
    pop = np.sort(rng.choice(np.arange(1_000_000, 1_400_000), 1000, replace=False))
    ids = list(pop[600:760]) + list(rng.choice(pop[:600], 40, replace=False))
    rng.shuffle(ids)
    cols = [Column("order_id", "count", 0.0, {"dtype": "int"}), Column("amount", "uniform", 0.0, {"dtype": "float"}),
            Column("status", "categorical", 0.0, {"dtype": "str"})]
    ds = Dataset(seed=3, columns=cols, baseline=_orders(pop[:600], rnd), truth=_orders(ids, rnd))
    trial = Trial(fault="clean", seed=3, dataset=ds, sent=copy_rows(ds.truth), expected=copy_rows(ds.truth),
                  snapshot=dict(ds.schema), tenant=None)
    result = classify_clean(trial, await heal(trial))
    assert result.outcome == CORRECT, result.why
