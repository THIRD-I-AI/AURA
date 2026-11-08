"""BUG-575: residual wrong results of /uasr/heal (ADR-002: never guess -- quarantine).

Each case is a class the accuracy benchmark (tests/accuracy/) found returned with values
that are not the truth:

* a x2.5 fault on a heavy-tailed column the detector passes (benchmark decoy_x2.5,
  seed 146): nearly all the column's mass sits in one merged histogram bin and its mean
  moves under 2 sigma, so the batch came back healed=true;
* a second fault on another column passing the sandbox's re-check after the first
  repair was proven -- a heavy-tailed column x2.5 behind a proven rename;
* rows a repair held aside hiding another column's fault -- an int column /10**4 fails
  the int cast on all but its missing values, and the few rows left carried a x2.5
  fault on another column past every check;
* a factor that is not a power of ten "proven" as one -- x1050 on a wide column passes
  every distribution check at x1000, but its restored values have more decimals than
  the column ever records;
* a rescale restoring an int column as floats -- 407 "restored" as 40.7 by x10.

Real DriftDetector, RecoveryLoop and /uasr/heal handler; an isolated SQLite file; the
FastAPI lifespan is never driven; no LLM call is reachable (the proposal path for an
unproven fault falls back to templates).
"""
from __future__ import annotations

import asyncio
import math
import os
import random
import sys
import tempfile
import uuid

import pytest
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
from uasr import service  # noqa: E402
from uasr.actuator_agent import (  # noqa: E402
    SynthesisActuatorAgent,
    location_shift,
    prove_scale,
    restore_scaled,
)
from uasr.db import get_session_factory, init_uasr_db  # noqa: E402
from uasr.drift_detector import DriftDetector  # noqa: E402
from uasr.models import BatchPayload  # noqa: E402

SCHEMA = {"key": "int", "retries": "int", "offset": "float", "basket": "float", "region": "str"}


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_bug575_{uuid.uuid4().hex[:8]}.db")
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
async def _tables(monkeypatch):
    await init_uasr_db()
    monkeypatch.setattr(SynthesisActuatorAgent, "_llm_shim", _no_llm)
    yield


def _poisson(rnd: random.Random, lam: float) -> int:
    threshold, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rnd.random()
        if p <= threshold:
            return k
        k += 1


def _rows(n: int, rnd: random.Random, first_key: int, retries_mean: float = 6.0, retries_null: float = 0.0):
    # Keys spread over a fixed population (a customer id), so a clean batch matches.
    return [{
        "key": 1 + (7919 * (first_key + i)) % 1_000_003,
        "retries": None if rnd.random() < retries_null else _poisson(rnd, retries_mean),
        "offset": round(rnd.gauss(0.0, 12.0), 2),
        # Heavy-tailed: nearly all mass sits in the first bins of a linear histogram.
        "basket": round(rnd.lognormvariate(5.0, 1.2), 2),
        "region": rnd.choice(["eu", "us", "apac", "latam"]),
    } for i in range(n)]


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/x"})


async def _heal(source: str, baseline, rows, schema=None) -> dict:
    service._detector.register_baseline(
        source, BatchPayload(source_id=source, columns=list(SCHEMA), rows=baseline, schema_snapshot=SCHEMA))
    req = service.IngestRequest(source_id=source, rows=rows, schema_snapshot=schema, metadata={})
    async with get_session_factory()() as db:
        return await service.heal_batch(req, _request(), db=db)


def _no_wrong_value_returned(out: dict, sent: list, truth: list) -> None:
    """ADR-002: rows go to the destination whatever ``healed`` says, so every returned
    row must be its truth; and rows + quarantined_rows hold every row sent, once."""
    held = sorted(q["index"] for q in out["quarantined_rows"])
    assert len(held) + len(out["rows"]) == len(sent)
    returned = [i for i in range(len(sent)) if i not in set(held)]
    for j, i in enumerate(returned):
        assert out["rows"][j] == truth[i] and type(out["rows"][j]["basket"]) is float, (i, out["rows"][j])
    for q in out["quarantined_rows"]:
        assert q["row"] == sent[q["index"]]


async def test_a_second_fault_behind_a_proven_rename_is_quarantined_not_healed():
    rnd = random.Random(580)
    baseline = _rows(840, rnd, 0)
    truth = _rows(160, rnd, 840)
    sent = []
    for r in truth:
        row = {("offset_v2" if k == "offset" else k): v for k, v in r.items()}
        row["basket"] = row["basket"] * 2.5  # no power of ten explains x2.5
        sent.append(row)
    # Precondition of the case: the detector's histogram check alone does not see the
    # x2.5 on this heavy-tailed column (the batch with no rename is not drift at all).
    alone = DriftDetector()
    alone.register_baseline("p", BatchPayload(source_id="p", columns=list(SCHEMA), rows=baseline))
    unrenamed = [{**r, "basket": r["basket"] * 2.5} for r in truth]
    assert not alone.detect(BatchPayload(source_id="p", columns=list(SCHEMA), rows=unrenamed)).drift_detected

    out = await _heal(f"bug575_{uuid.uuid4().hex[:8]}", baseline, sent)

    assert out["drift_detected"] is True
    assert out["healed"] is False
    assert out["rows"] == [] and len(out["quarantined_rows"]) == len(sent)
    assert "basket" in out["reason"]
    _no_wrong_value_returned(out, sent, truth)


@pytest.mark.parametrize("seed", [
    5763,  # was returned healed=true with the x2.5 values
    5756,  # was returned healed=false (a null-rate alert on the kept rows), x2.5 values still in rows
])
async def test_rows_held_by_a_repair_do_not_hide_another_columns_fault(seed):
    rnd = random.Random(seed)
    # Counts with no zeros and a few missing values: /10**4 leaves no value an int
    # but 0, so only the rows with no count survive the int cast.
    baseline = _rows(900, rnd, 0, retries_mean=40.0, retries_null=0.006)
    truth = _rows(440, rnd, 900, retries_mean=40.0, retries_null=0.006)
    sent = [{**r, "retries": None if r["retries"] is None else r["retries"] / 10 ** 4,
             "offset": r["offset"] * 2.5} for r in truth]
    snapshot = {**SCHEMA, "retries": "float"}
    survivors = [r for r in sent if r["retries"] is None or float(r["retries"]).is_integer()]
    # Precondition: too few rows survive for any check of the x2.5 column to see it on them.
    assert 0 < len(survivors) <= 8

    out = await _heal(f"bug575_{uuid.uuid4().hex[:8]}", baseline, sent, snapshot)

    assert out["healed"] is False
    assert out["rows"] == [] and len(out["quarantined_rows"]) == len(sent)
    _no_wrong_value_returned(out, sent, truth)


async def test_a_heavy_tailed_column_x2_5_the_detector_passes_is_quarantined_not_healed():
    rnd = random.Random(580)
    baseline = _rows(840, rnd, 0)
    truth = _rows(160, rnd, 840)
    sent = [{**r, "basket": r["basket"] * 2.5} for r in truth]
    # Precondition: the detector alone calls this batch clean.
    alone = DriftDetector()
    alone.register_baseline("p", BatchPayload(source_id="p", columns=list(SCHEMA), rows=baseline))
    assert not alone.detect(BatchPayload(source_id="p", columns=list(SCHEMA), rows=sent)).drift_detected

    out = await _heal(f"bug575_{uuid.uuid4().hex[:8]}", baseline, sent)

    assert out["drift_detected"] is True and out["drift_type"] == "statistical"
    assert out["healed"] is False
    assert out["rows"] == [] and len(out["quarantined_rows"]) == len(sent)
    assert "basket" in out["reason"]
    _no_wrong_value_returned(out, sent, truth)


async def test_clean_batches_still_come_back_healed():
    """ADR-002: zero false alarms on clean data -- the rank test passes clean batches
    of every size, the heavy-tailed column included."""
    rnd = random.Random(581)
    baseline = _rows(900, rnd, 0)
    first = 900
    for n in (25, 160, 700, 2500):
        truth = _rows(n, rnd, first)
        first += n
        out = await _heal(f"bug575_{uuid.uuid4().hex[:8]}", baseline, [dict(r) for r in truth])
        assert out["drift_detected"] is False and out["healed"] is True, out.get("reason")
        assert out["rows"] == truth


_HEIGHT_SCHEMA = {"key": "int", "height": "float"}


def _height_rows(n: int, rnd: random.Random):
    # A measurement recorded to one decimal, wide enough that a 5% factor hides in it.
    return [{"key": rnd.randrange(1, 10 ** 6), "height": round(rnd.gauss(2800.0, 392.0), 1)} for _ in range(n)]


async def _heal_height(baseline, sent) -> dict:
    source = f"bug575_{uuid.uuid4().hex[:8]}"
    service._detector.register_baseline(source, BatchPayload(
        source_id=source, columns=list(_HEIGHT_SCHEMA), rows=baseline, schema_snapshot=_HEIGHT_SCHEMA))
    req = service.IngestRequest(source_id=source, rows=sent, schema_snapshot=None, metadata={})
    async with get_session_factory()() as db:
        return await service.heal_batch(req, _request(), db=db)


async def test_a_factor_that_is_not_a_power_of_ten_is_not_proven_as_one():
    rnd = random.Random(100)
    baseline = _height_rows(900, rnd)
    truth = _height_rows(184, rnd)
    sent = [{**r, "height": r["height"] * 1050} for r in truth]
    # Precondition: the column's distribution alone cannot tell x1050 from x1000.
    det = DriftDetector()
    det.register_baseline("h", BatchPayload(source_id="h", columns=list(_HEIGHT_SCHEMA), rows=baseline))
    power, _ = prove_scale([r["height"] for r in sent], det.baseline_distributions("h")["height"])
    assert power == 3

    out = await _heal_height(baseline, sent)

    assert out["healed"] is False
    assert out["rows"] == [] and len(out["quarantined_rows"]) == len(sent)
    assert "precision (1 decimal place(s))" in out["reason"]


async def test_a_real_x1000_on_the_same_column_is_still_repaired():
    rnd = random.Random(178)
    baseline = _height_rows(900, rnd)
    truth = _height_rows(184, rnd)
    sent = [{**r, "height": r["height"] * 1000} for r in truth]

    out = await _heal_height(baseline, sent)

    assert out["healed"] is True, out.get("reason")
    assert out["rows"] == truth and [r["type"] for r in out["repairs"]] == ["rescale"]


@pytest.mark.parametrize("value, power, kind, decimals, expected", [
    (407, 1, "int", None, None),          # not 10 x any int: a x11 or x10.5 fault, not x10
    (500, 1, "int", None, 50),
    (0.37, -2, "int", None, 37),          # an int column scaled down arrives as floats
    (0.375, -2, "int", None, None),
    (500, 1, "float", None, 50.0),        # a float column's value arrived as a JSON int
    (4.07, -1, "float", None, 40.7),
    (178815.0, 3, "float", 1, None),      # 178.815: three decimals in a one-decimal column
    (170300.0, 3, "float", 1, 170.3),
    (12.34, -2, "float", 2, 1234.0),
])
def test_restore_scaled_restores_a_value_of_the_baselines_kind_and_precision(value, power, kind, decimals, expected):
    got = restore_scaled(value, power, kind, decimals)
    assert got == expected and type(got) is type(expected)


def test_the_baseline_records_the_columns_precision():
    rnd = random.Random(5)
    det = DriftDetector()
    rows = [{"money": round(rnd.uniform(1, 100), 2), "raw": rnd.random(), "n": rnd.randrange(9)} for _ in range(300)]
    det.register_baseline("d", BatchPayload(source_id="d", columns=["money", "raw", "n"], rows=rows))
    dists = det.baseline_distributions("d")
    assert (dists["money"].decimals, dists["raw"].decimals, dists["n"].decimals) == (2, None, 0)


def test_location_shift_flags_a_moved_heavy_tail_but_no_clean_batch_or_identifier():
    rnd = random.Random(7)
    det = DriftDetector()
    det.register_baseline("m", BatchPayload(source_id="m", columns=list(SCHEMA), rows=_rows(800, rnd, 0)))
    dists = det.baseline_distributions("m")
    batch = _rows(200, rnd, 800)
    assert location_shift([v["basket"] * 2.5 for v in batch], dists["basket"]) is not None
    # 400 clean batches of 20 to 3000 rows, every numeric column: no flag.
    for k in range(400):
        clean = _rows(rnd.choice([20, 60, 200, 900, 3000]), rnd, 1000 + 3000 * k)
        for col in ("retries", "offset", "basket"):
            assert location_shift([r[col] for r in clean], dists[col]) is None, (k, col)
    # A sequential key moves past the baseline every batch; it is not a measure (BUG-573).
    assert location_shift([10 ** 6 + i for i in range(200)], dists["key"]) is None
