"""ADR-002 phase 2a detection: BUG-571 (scale-down missed) and BUG-572 (int arriving
as float missed without a schema snapshot).

BUG-571 root cause, measured: the aligned KL (BUG-564) of a /10 or /1000 batch is
2.6-6.7 and passes its G-test, but the cold-start warmup suppressed every
statistical finding whose mean moved by <= 2 sigma. A scale-down can never move the
mean further than |mean|/sigma (1.6-1.7 for uniform 10-500), and a rescale of a
column centred on 0 does not move it at all; /uasr/heal judges each batch inside
that warmup, so there the fault was hidden every time.

Real DriftDetector, RecoveryLoop and /uasr/heal handler throughout; the database is
an isolated SQLite file as in tests/test_heal_phase2a_repairs.py, and the FastAPI
lifespan is never driven. Detection runs with the default constructor (warmup on),
which is what /uasr/heal uses.
"""
from __future__ import annotations

import asyncio
import copy
import os
import random
import sys
import tempfile
import uuid

import numpy as np
import pytest
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
from uasr import service  # noqa: E402
from uasr.actuator_agent import SynthesisActuatorAgent  # noqa: E402
from uasr.db import get_session_factory, init_uasr_db  # noqa: E402
from uasr.drift_detector import DriftDetector  # noqa: E402
from uasr.models import BatchPayload, DriftType  # noqa: E402
from uasr.state_store import SourceState  # noqa: E402

BASELINE_ROWS = 400

SHAPES = {
    "uniform": lambda r, n: r.uniform(10.0, 500.0, n),
    "normal": lambda r, n: r.normal(100.0, 15.0, n),
    "lognormal": lambda r, n: r.lognormal(3.0, 0.5, n),
    "integers": lambda r, n: r.integers(1, 1000, n).astype(float),
    "centred_on_0": lambda r, n: r.normal(0.0, 1.0, n),
    "small_range": lambda r, n: r.uniform(0.001, 0.01, n),
    "small_integers": lambda r, n: r.integers(1, 6, n).astype(float),
}
FACTORS = [10.0 ** -k for k in range(1, 7)] + [10.0 ** k for k in range(1, 7)]


def _batch(values, bid="b", col="v") -> BatchPayload:
    return BatchPayload(source_id="s", batch_id=bid, columns=[col],
                        rows=[{col: float(v)} for v in values])


def _detector(baseline: BatchPayload) -> DriftDetector:
    det = DriftDetector()
    det.register_baseline("s", baseline)
    return det


# ── BUG-571: reproduction from the registry ──────────────────────────────────


def _bug571_baseline(seed=1):
    rnd = random.Random(seed)
    return [{"amount": rnd.uniform(10, 500)} for _ in range(400)]


@pytest.mark.parametrize("factor", [0.1, 0.001, 10.0])
def test_bug571_registry_reproduction_is_detected(factor):
    rnd = random.Random(2)
    det = DriftDetector()
    det.register_baseline("s", BatchPayload(source_id="s", columns=["amount"], rows=_bug571_baseline()))
    rows = [{"amount": rnd.uniform(10, 500) * factor} for _ in range(300)]
    r = det.detect(BatchPayload(source_id="s", columns=["amount"], rows=rows))
    assert r.drift_detected and r.drift_type == DriftType.STATISTICAL, r.details
    assert r.affected_columns == ["amount"]


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_bug571_every_scale_factor_is_detected_on_every_shape(shape):
    gen = SHAPES[shape]
    misses = []
    for size in (100, 300, 1000):
        for seed in range(3):
            rng = np.random.default_rng(seed)
            det = _detector(_batch(gen(rng, BASELINE_ROWS), "base"))
            for f in FACTORS:
                r = det.detect(_batch(gen(rng, size) * f, f"{size}-{f}"), record_state=False)
                if not (r.drift_type == DriftType.STATISTICAL and r.affected_columns == ["v"]):
                    misses.append((size, seed, f))
    assert misses == [], f"{shape}: {len(misses)} missed scale faults, e.g. {misses[:5]}"


def test_bug571_a_scale_down_does_not_raise_the_adaptive_threshold():
    det = _detector(_batch(SHAPES["uniform"](np.random.default_rng(0), BASELINE_ROWS), "base"))
    rng = np.random.default_rng(1)
    for i in range(10):
        det.detect(_batch(SHAPES["uniform"](rng, 300), f"clean{i}"))
    before = list(det._store.peek("s").kl_history)
    for i in range(3):
        assert det.detect(_batch(SHAPES["uniform"](rng, 300) / 1000, f"down{i}")).drift_detected
    assert det._store.peek("s").kl_history == before


def test_bug571_clean_data_of_any_size_and_seed_raises_nothing():
    """>= 200 trials per shape, default detector, state recorded as in production."""
    hits = []
    for shape, gen in SHAPES.items():
        for t in range(30):
            rng = np.random.default_rng(50_000 + t)
            det = _detector(_batch(gen(rng, BASELINE_ROWS), "base"))
            for size in (40, 100, 400, 4000):
                for k in range(2):
                    r = det.detect(_batch(gen(rng, size), f"{t}-{size}-{k}"))
                    if r.drift_detected:
                        hits.append((shape, t, size, [d.value for d in r.drift_types], r.details))
    assert hits == [], f"{len(hits)} false alarms on clean data, e.g. {hits[:3]}"


# ── BUG-572: value kinds ─────────────────────────────────────────────────────


def _ids(rnd, n, as_float=False):
    return [{"customer_id": float(v) if as_float else v, "status": rnd.choice("abc")}
            for v in (rnd.randint(1, 10_000) for _ in range(n))]


def _kind_detector(rows, schema=None):
    det = DriftDetector()
    det.register_baseline("s", BatchPayload(source_id="s", columns=list(rows[0]), rows=rows,
                                            schema_snapshot=schema))
    return det


@pytest.mark.parametrize("baseline_schema", [{"customer_id": "int", "status": "str"}, None],
                         ids=["baseline-snapshot", "no-snapshot-anywhere"])
def test_bug572_int_column_arriving_as_floats_without_a_snapshot_is_a_type_change(baseline_schema):
    rnd = random.Random(3)
    det = _kind_detector(_ids(rnd, 400), baseline_schema)
    r = det.detect(BatchPayload(source_id="s", columns=["customer_id", "status"],
                                rows=_ids(rnd, 300, as_float=True)))
    assert r.drift_detected and r.drift_type == DriftType.SCHEMA, r.details
    assert r.drift_vector["type"] == "type_change"
    assert r.affected_columns == ["customer_id"]
    assert r.drift_vector["old_types"]["customer_id"] == (baseline_schema or {}).get("customer_id", "int")
    assert r.drift_vector["new_types"] == {"customer_id": "float"}


def test_bug572_a_number_snapshot_does_not_hide_the_kind_change():
    rnd = random.Random(4)
    det = _kind_detector(_ids(rnd, 400), {"customer_id": "number", "status": "str"})
    r = det.detect(BatchPayload(source_id="s", columns=["customer_id", "status"],
                                rows=_ids(rnd, 300, as_float=True),
                                schema_snapshot={"customer_id": "number", "status": "str"}))
    assert r.drift_vector.get("type") == "type_change" and r.affected_columns == ["customer_id"]


def test_bug572_whole_number_floats_in_a_float_column_never_alarm():
    """Kinds are compared, not values: a float column of whole numbers stays float."""
    rnd = random.Random(5)
    base = [{"qty": float(rnd.randint(1, 50))} for _ in range(400)]
    det = _kind_detector(base)
    assert det.baseline_distributions("s")["qty"].value_kind == "float"
    for i in range(20):
        whole = [{"qty": float(rnd.randint(1, 50))} for _ in range(rnd.choice([50, 300, 2000]))]
        assert not det.detect(BatchPayload(source_id="s", batch_id=f"w{i}", columns=["qty"],
                                           rows=whole)).drift_detected
        # JSON writes 5.0 as 5: ints in a float column are lossless, not a retype.
        as_ints = [{"qty": int(r["qty"])} for r in whole]
        assert not det.detect(BatchPayload(source_id="s", batch_id=f"i{i}", columns=["qty"],
                                           rows=as_ints)).drift_detected


@pytest.mark.parametrize("declared", ["float", "double", "FLOAT64"])
def test_bug572_a_declared_float_column_sent_as_json_ints_is_still_float(declared):
    """Review of f9cee99: a baseline whose whole-number floats arrived as JSON ints
    was recorded as kind "int", overriding the declared float, and every later batch
    of ordinary fractional prices raised a type_change int -> float."""
    rnd = random.Random(8)
    det = _kind_detector([{"price": rnd.randint(10, 50)} for _ in range(400)], {"price": declared})
    for i in range(20):
        frac = [{"price": round(rnd.uniform(10, 50), 2)} for _ in range(300)]
        for snap in ({"price": declared}, None):
            r = det.detect(BatchPayload(source_id="s", batch_id=f"f{i}", columns=["price"],
                                        rows=frac, schema_snapshot=snap))
            assert DriftType.SCHEMA not in r.drift_types, r.details


def test_bug572_an_undeclared_or_number_baseline_of_ints_still_detects_floats():
    """The declared float is what tolerates fractional values; without it an int
    baseline keeps its kind (BUG-572), and a declared int is not loosened."""
    for schema in (None, {"customer_id": "number", "status": "str"},
                   {"customer_id": "int", "status": "str"}):
        rnd = random.Random(9)
        det = _kind_detector(_ids(rnd, 400), schema)
        r = det.detect(BatchPayload(source_id="s", columns=["customer_id", "status"],
                                    rows=_ids(rnd, 300, as_float=True)))
        assert r.drift_vector.get("type") == "type_change", (schema, r.details)


def test_bug572_nan_is_a_missing_value_not_a_float():
    k = DriftDetector._value_kind
    assert k([1, float("nan"), 3]) == "int"
    assert k([1, np.float64("nan"), None]) == "int"
    assert k([float("nan")]) is None
    assert k([1.5, float("nan")]) == "float"
    rnd = random.Random(10)
    det = _kind_detector(_ids(rnd, 400), {"customer_id": "int", "status": "str"})
    rows = _ids(rnd, 300)
    for i in range(0, 300, 7):
        rows[i]["customer_id"] = float("nan")
    r = det.detect(BatchPayload(source_id="s", columns=["customer_id", "status"], rows=rows))
    assert DriftType.SCHEMA not in r.drift_types, r.details


def test_bug572_value_kind_inference():
    k = DriftDetector._value_kind
    assert k([1, 2, None]) == "int"
    assert k([np.int64(1)]) == "int"
    assert k([1.0, 2.5]) == "float" and k([np.float64(1.0)]) == "float"
    assert k([1, 2.5]) == "float"
    assert k([True, False]) == "bool" and k([np.bool_(True)]) == "bool"
    assert k(["a"]) == "str"
    assert k([1, "a"]) == "mixed"
    assert k([None]) is None


def test_bug572_other_kind_changes_are_type_changes_and_mixed_baselines_are_not_judged():
    det = _kind_detector([{"flag": bool(i % 2)} for i in range(200)])
    r = det.detect(BatchPayload(source_id="s", columns=["flag"], rows=[{"flag": i % 2} for i in range(200)]))
    assert r.drift_vector.get("type") == "type_change"
    assert r.drift_vector["old_types"] == {"flag": "bool"} and r.drift_vector["new_types"] == {"flag": "int"}

    mixed = _kind_detector([{"m": (i if i % 2 else str(i))} for i in range(200)])
    r = mixed.detect(BatchPayload(source_id="s", columns=["m"], rows=[{"m": float(i)} for i in range(200)]))
    assert DriftType.SCHEMA not in r.drift_types


def test_bug572_value_kind_survives_state_serialization():
    det = _kind_detector(_ids(random.Random(6), 50))
    st = SourceState.from_json(det._store.peek("s").to_json())
    assert st.baseline["customer_id"].value_kind == "int"
    assert st.baseline["status"].value_kind == "str"


def test_bug572_a_declared_retype_is_adopted_and_stays_adopted_after_the_intent():
    rnd = random.Random(7)
    det = DriftDetector(schema_intent_enabled=True)
    det.register_baseline("s", BatchPayload(source_id="s", columns=["customer_id", "status"], rows=_ids(rnd, 400)))
    det.declare_schema_intent("s", type_changes={"customer_id": "float"}, ttl_seconds=3600)
    batch = BatchPayload(source_id="s", columns=["customer_id", "status"], rows=_ids(rnd, 300, as_float=True))
    assert DriftType.SCHEMA not in det.detect(batch).drift_types
    st = det._store.load("s")
    st.schema_intent = None
    det._store.save("s", st)
    assert DriftType.SCHEMA not in det.detect(batch).drift_types


# ── End to end through /uasr/heal ────────────────────────────────────────────

SCHEMA = {"customer_id": "int", "amount": "float", "status": "str"}


@pytest.fixture(scope="module")
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_heal571_{uuid.uuid4().hex[:8]}.db")
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
    # No LLM in CI: the proposal path for an unproven fault falls back to templates.
    monkeypatch.setattr(SynthesisActuatorAgent, "_llm_shim", _no_llm)
    yield


def _rows(n: int, seed: int):
    rnd = random.Random(seed)
    return [{
        "customer_id": rnd.randint(1, 10_000),
        "amount": round(rnd.uniform(10.0, 500.0), 2),
        "status": rnd.choice(["active", "inactive", "pending"]),
    } for _ in range(n)]


def _source() -> str:
    return f"src_{uuid.uuid4().hex[:8]}"


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/x"})


async def _heal(source: str, rows) -> dict:
    req = service.IngestRequest(source_id=source, rows=rows, schema_snapshot=None, metadata={})
    async with get_session_factory()() as db:
        return await service.heal_batch(req, _request(), db=db)


def _accounted(out: dict, sent: list) -> None:
    indices = sorted(q["index"] for q in out["quarantined_rows"])
    assert len(indices) == len(set(indices))
    assert len(out["rows"]) + len(indices) == len(sent)
    for q in out["quarantined_rows"]:
        assert q["row"] == sent[q["index"]] and q["reason"]


def _assert_repaired_or_quarantined(out: dict, sent: list, truth: list) -> None:
    """Never returned unchanged with healed=true; every kept row equals its truth."""
    assert out["drift_detected"] is True, out
    _accounted(out, sent)
    if out["healed"]:
        assert out["quarantined_rows"] == [] and out["rows"] == truth
    else:
        assert out["rows"] == [] or out["rows"] != sent
        held = {q["index"] for q in out["quarantined_rows"]}
        kept_truth = [t for i, t in enumerate(truth) if i not in held]
        assert out["rows"] == kept_truth


@pytest.mark.asyncio
@pytest.mark.parametrize("power", [-1, -3])
async def test_heal_a_scaled_down_batch_is_rescaled_exactly_or_quarantined(_heal_db, power):
    source = _source()
    service._detector.register_baseline(source, BatchPayload(
        source_id=source, columns=list(SCHEMA), rows=_rows(400, 31), schema_snapshot=SCHEMA))
    truth = _rows(300, 32)
    sent = [{**r, "amount": r["amount"] * 10.0 ** power} for r in truth]

    out = await _heal(source, copy.deepcopy(sent))

    _assert_repaired_or_quarantined(out, sent, truth)
    # The rescale rule proves these (exact x10**n faults of 2-decimal values).
    assert out["healed"] is True, out["quarantined_rows"][:1]
    (rescale,) = out["repairs"]
    assert rescale["type"] == "rescale" and rescale["column"] == "amount"
    assert rescale["proof"]["factor"] == 10.0 ** power


@pytest.mark.asyncio
async def test_heal_an_int_column_arriving_as_floats_is_retyped_losslessly(_heal_db):
    source = _source()
    service._detector.register_baseline(source, BatchPayload(
        source_id=source, columns=list(SCHEMA), rows=_rows(400, 33), schema_snapshot=SCHEMA))
    truth = _rows(300, 34)
    sent = [{**r, "customer_id": float(r["customer_id"])} for r in truth]

    out = await _heal(source, copy.deepcopy(sent))

    _assert_repaired_or_quarantined(out, sent, truth)
    assert out["healed"] is True
    assert all(type(r["customer_id"]) is int for r in out["rows"])
    (retype,) = out["repairs"]
    assert retype["type"] == "retype" and retype["column"] == "customer_id"
    assert retype["proof"]["from"] == "float" and retype["proof"]["to"] == "int"


@pytest.mark.asyncio
async def test_heal_fractional_floats_in_an_int_column_are_quarantined_not_rounded(_heal_db):
    source = _source()
    service._detector.register_baseline(source, BatchPayload(
        source_id=source, columns=list(SCHEMA), rows=_rows(400, 35), schema_snapshot=SCHEMA))
    truth = _rows(300, 36)
    sent = [{**r, "customer_id": r["customer_id"] + (0.5 if i % 3 == 0 else 0.0)}
            for i, r in enumerate(truth)]

    out = await _heal(source, copy.deepcopy(sent))

    # Per-row quarantine, as for any retype (test_heal_phase2a_repairs): the
    # whole-number rows cast back exactly, the others are held as sent.
    assert out["drift_detected"] is True and out["degraded"] is True
    _accounted(out, sent)
    fractional = {i for i in range(300) if i % 3 == 0}
    assert {q["index"] for q in out["quarantined_rows"]} == fractional
    assert out["rows"] == [r for i, r in enumerate(truth) if i not in fractional]
    assert all(type(r["customer_id"]) is int for r in out["rows"])


@pytest.mark.asyncio
async def test_heal_fractional_prices_in_a_declared_float_column_sent_as_ints_pass_untouched(_heal_db):
    """Review of f9cee99, end to end: the declared-float baseline arrived as JSON
    ints; ordinary fractional amounts must not be retyped to int and quarantined."""
    source = _source()
    base = [{**r, "amount": int(round(r["amount"]))} for r in _rows(400, 37)]
    service._detector.register_baseline(source, BatchPayload(
        source_id=source, columns=list(SCHEMA), rows=base, schema_snapshot=SCHEMA))
    sent = _rows(300, 38)

    out = await _heal(source, copy.deepcopy(sent))

    assert out["quarantined_rows"] == [] and out["rows"] == sent, out.get("repairs")
    assert "schema" not in [str(t).lower() for t in out.get("drift_types", [])]
