"""ADR-002 accuracy: BUG-577 -- a rescale restores a float only when one float fits.

The real-data lane of the accuracy benchmark found an applied repair that was wrong:
sklearn's diabetes data holds s5 = 5.247000000000001 (the float after 5.247), and at
x100 both values become 524.7. restore_scaled picked the shorter, 5.247, and the
batch came back healed with a value the destination never had. A column recorded to
a fixed precision admits only one of the two; a column carrying full float digits
admits both, so the restoration was a guess (ADR-002: never guess -- quarantine).

Real DriftDetector and the real /uasr/heal handler (through the accuracy
benchmark's harness) on an isolated SQLite file; the FastAPI lifespan is never driven.
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
from tests.accuracy.datasets import Column, Dataset, copy_rows  # noqa: E402
from tests.accuracy.faults import Trial  # noqa: E402
from tests.accuracy.harness import CORRECT, WRONG, classify, heal  # noqa: E402
from uasr.actuator_agent import SynthesisActuatorAgent, restore_scaled  # noqa: E402
from uasr.db import init_uasr_db  # noqa: E402
from uasr.reflector_agent import DiagnosticReflectorAgent  # noqa: E402

NOISY = 5.247000000000001
assert NOISY == math.nextafter(5.247, math.inf) and NOISY * 100 == 5.247 * 100


@pytest.mark.parametrize("truth", [5.247, NOISY])
def test_a_value_two_floats_scale_to_has_no_restoration_in_a_full_digit_column(truth):
    assert restore_scaled(truth * 100, 2, "float", None) is None


def test_a_fixed_precision_column_still_restores_it():
    # Recorded to 4 places, only 5.247 is a value of the column.
    got = restore_scaled(NOISY * 100, 2, "float", 4)
    assert got == 5.247 and type(got) is float


@pytest.mark.parametrize("truth, power", [(0.07, 2), (123.45, 2), (40.7, -1), (19.99, 3), (1e-3, 2)])
def test_a_value_only_one_float_scales_to_is_still_restored_in_a_full_digit_column(truth, power):
    sent = truth * 10 ** power if power > 0 else truth / 10 ** -power
    assert restore_scaled(sent, power, "float", None) == truth


@pytest.fixture(scope="module")
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_heal577_{uuid.uuid4().hex[:8]}.db")
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


def _rows(rnd: random.Random, n: int, noisy_at=None):
    # Four recorded decimals, with the odd float-noise value a real export carries --
    # enough to leave the column with full float digits (decimals None).
    rows = [{"s5": round(rnd.uniform(3.2, 6.2), 4), "bp": round(rnd.uniform(60.0, 130.0), 2)} for _ in range(n)]
    for i in noisy_at or ():
        rows[i]["s5"] = NOISY
    return rows


async def test_heal_never_returns_a_guessed_restoration(_heal_db):
    for seed in range(5):
        rnd = random.Random(seed)
        cols = [Column("s5", "normal", 0.0, {"dtype": "float"}), Column("bp", "normal", 0.0, {"dtype": "float"})]
        ds = Dataset(seed=seed, columns=cols, baseline=_rows(rnd, 400, noisy_at=[3]),
                     truth=_rows(rnd, 200, noisy_at=[17]))
        sent = copy_rows(ds.truth)
        for row in sent:
            row["s5"] = row["s5"] * 100
        trial = Trial(fault="scale_up", seed=seed, dataset=ds, sent=sent, expected=copy_rows(ds.truth),
                      snapshot=dict(ds.schema), tenant=None)
        out = await heal(trial)
        result = classify(trial, out)
        assert result.outcome != WRONG, (seed, result.why)
        # The ambiguous row is held aside, never returned as 5.247.
        assert 17 in {q["index"] for q in out["quarantined_rows"]}


# ── Values past the baseline's range are judged exactly, not by chi-square ─────
# The real-data lane: two clean batches alarmed (sk_wine seed 37: 7 of 27 proline
# values below a 106-row baseline's min; sm_modechoice seed 88 on gc). The G test
# priced the empty under/overflow bins with a 0.5 pseudo-count and a chi-square
# quantile, far outside where the approximation holds: it fired from 6 values past
# the range, which a clean batch shows about once in 23,000 -- not once in a million.


def test_the_beyond_range_tail_is_the_exchangeable_probability():
    from uasr.drift_detector import _beyond_range_tail

    want = (27 * 26 * 25 * 24 * 23 * 22 * 21) / (133 * 132 * 131 * 130 * 129 * 128 * 127)
    assert _beyond_range_tail(7, 27, 106) == pytest.approx(want)
    assert 1e-6 < want < 1e-5
    assert _beyond_range_tail(0, 27, 106) == 1.0
    assert _beyond_range_tail(28, 27, 106) == 0.0
    # The whole batch past the range (a x10 fault): far below 1e-6.
    assert _beyond_range_tail(27, 27, 106) < 1e-20


def test_a_few_clean_values_past_a_small_baselines_range_are_not_significant():
    from uasr.drift_detector import DriftDetector

    # The sk_wine batch: 106 baseline rows, 27 batch rows (one inner group), 7 of
    # them below the min. The old test called 6 or more significant.
    for below in (5, 6, 7):
        _, significant = DriftDetector._aligned_kl([below, 27 - below, *([0] * 52), 0], [2] * 53)
        assert significant is False, below


def test_mass_past_the_range_is_still_significant():
    from uasr.drift_detector import DriftDetector

    base = [2] * 53  # 106 rows over 53 bins
    _, scaled = DriftDetector._aligned_kl([0, *([0] * 53), 27], base)
    assert scaled is True
    _, half_over = DriftDetector._aligned_kl([0, *([1] * 13), *([0] * 40), 14], base)
    assert half_over is True


def test_an_in_range_shift_is_still_significant():
    from uasr.drift_detector import DriftDetector

    base = [20] * 50  # 1,000 rows, flat
    shifted = [0] * 25 + [40] * 25  # 1,000 rows all in the upper half
    _, significant = DriftDetector._aligned_kl([0, *shifted, 0], base)
    assert significant is True
    _, clean = DriftDetector._aligned_kl([0, *base, 0], base)
    assert clean is False


# ── A lossless cast proves the representation, not the values ──────────────────
# The real-data lane: COMPAS priors_count x2.5 (seeds 5, 21, 40, 41, 47). The int
# column arrived as floats; the whole-valued ones (an even count x2.5) cast back
# to int exactly and were returned, 5 for a true 2. A retype now also needs the
# cast values to pass the distribution proof every other repair passes.


def _counts(rnd: random.Random, n: int):
    # A heavy-tailed int count with many zeros, as priors_count is, plus a float.
    return [{"n": min(int(rnd.expovariate(0.3)), 38), "bp": round(rnd.uniform(60.0, 130.0), 2)}
            for _ in range(n)]


def _count_trial(seed: int, sent_fn, expected_fn):
    rnd = random.Random(seed)
    cols = [Column("n", "count", 0.0, {"dtype": "int"}), Column("bp", "normal", 0.0, {"dtype": "float"})]
    ds = Dataset(seed=seed, columns=cols, baseline=_counts(rnd, 400), truth=_counts(rnd, 250))
    sent = copy_rows(ds.truth)
    for i, row in enumerate(sent):
        row["n"] = sent_fn(i, row["n"])
    expected = [expected_fn(i, r) for i, r in enumerate(copy_rows(ds.truth))]
    snap = dict(ds.schema)
    snap["n"] = "float"
    return Trial(fault="decoy_x2.5", seed=seed, dataset=ds, sent=sent, expected=expected,
                 snapshot=snap if seed % 2 else None, tenant=None)


async def test_a_scaled_int_column_is_not_retyped_back_from_its_whole_values(_heal_db):
    for seed in range(6):
        trial = _count_trial(seed, lambda i, v: v * 2.5, lambda i, r: r if r["n"] == 0 else None)
        out = await heal(trial)
        result = classify(trial, out)
        assert result.outcome != WRONG, (seed, result.why)
        # Every row that carried a changed value is held, the whole-valued ones too.
        changed = {i for i, r in enumerate(trial.dataset.truth) if r["n"] != 0}
        assert changed <= {q["index"] for q in out["quarantined_rows"]}, seed
        assert not any(r.get("type") == "retype" for r in out.get("repairs") or []), seed


async def test_a_genuine_retype_with_a_few_bad_values_still_heals_the_rest(_heal_db):
    bad = {3, 77, 140}
    for seed in range(4):
        trial = _count_trial(seed, lambda i, v: "n/a" if i in bad else float(v),
                             lambda i, r: None if i in bad else r)
        out = await heal(trial)
        result = classify(trial, out)
        assert result.outcome == CORRECT, (seed, result.why)
        assert {q["index"] for q in out["quarantined_rows"]} == bad
        assert any(r.get("type") == "retype" for r in out["repairs"])


def test_prove_cast_values_matches_the_baseline_or_names_why_not():
    from uasr.actuator_agent import prove_cast_values
    from uasr.drift_detector import DriftDetector
    from uasr.models import BatchPayload

    rnd = random.Random(1)
    det = DriftDetector()
    det.register_baseline("p", BatchPayload(source_id="p", columns=["n", "bp"], rows=_counts(rnd, 400)))
    dist = det.baseline_distributions("p")["n"]
    truth = [r["n"] for r in _counts(rnd, 250)]
    assert prove_cast_values(truth, dist) == (True, "")
    whole = [int(v * 2.5) for v in truth if (v * 2.5).is_integer()]
    ok, why = prove_cast_values(whole, dist)
    assert ok is False and "not only a type" in why
    assert prove_cast_values([1, 2], None)[0] is False
    assert prove_cast_values([], dist) == (True, "")


# ── A detected batch never returns a column whose change no rule proved ────────
# The real-data lane: sk_digits combo seed 39 (retype on pixel_3_5, x10^-4 on
# pixel_2_7) and sm_fair combo seed 42 (retype on occupation, x10^-1 on affairs)
# came back healed=true: the retype was proven, the scaled column read as "no drift"
# (affairs: KL 0.14 under zeta at p = 5e-12; pixel_2_7: 13 non-zero whole values
# moved into the first bin beside the zeros) and was returned scaled.


async def test_a_flagged_batch_holds_a_column_proven_different_below_zeta(_heal_db):
    for seed in range(4):
        rnd = random.Random(seed)

        def rows(n):
            # "sparse": mostly 0, some whole counts -- recorded to 0 decimals.
            return [{"k": rnd.randint(1, 9), "sparse": float(rnd.choice([0] * 9 + [rnd.randint(1, 16)]))}
                    for _ in range(n)]

        cols = [Column("k", "count", 0.0, {"dtype": "int"}), Column("sparse", "normal", 0.0, {"dtype": "float"})]
        ds = Dataset(seed=seed, columns=cols, baseline=rows(500), truth=rows(400))
        sent = copy_rows(ds.truth)
        for row in sent:
            row["k"] = str(row["k"])  # a genuine, lossless retype
            row["sparse"] = row["sparse"] / 10 ** 4
        trial = Trial(fault="combo", seed=seed, dataset=ds, sent=sent, expected=copy_rows(ds.truth),
                      snapshot=None, tenant=None)
        out = await heal(trial)
        result = classify(trial, out)
        assert result.outcome != WRONG, (seed, result.why)
        assert out["healed"] is False and "sparse" in (out.get("reason") or ""), seed


def test_unrepaired_value_drift_names_extra_decimals_and_proven_differences():
    from uasr.drift_detector import DriftDetector
    from uasr.models import BatchPayload
    from uasr.recovery_loop import RecoveryLoop

    rnd = random.Random(3)
    base = [{"w": float(rnd.choice([0, 0, 0, 2, 5, 9])), "f": round(rnd.gauss(50, 5), 2)} for _ in range(600)]
    det = DriftDetector()
    det.register_baseline("u", BatchPayload(source_id="u", columns=["w", "f"], rows=base))
    dist = det.baseline_distributions("u")
    clean = [{"w": float(rnd.choice([0, 0, 0, 2, 5, 9])), "f": round(rnd.gauss(50, 5), 2)} for _ in range(300)]
    assert RecoveryLoop._unrepaired_value_drift(clean, ["w", "f"], dist) == {}
    # Whole floats (0.0) have no decimal places; a tenth of 5 has one.
    scaled = [{**r, "w": r["w"] / 10} for r in clean]
    assert set(RecoveryLoop._unrepaired_value_drift(scaled, ["w", "f"], dist)) == {"w"}
    shifted = [{**r, "f": round(r["f"] + 4.0, 2)} for r in clean]
    found = RecoveryLoop._unrepaired_value_drift(shifted, ["w", "f"], dist)
    assert set(found) == {"f"} and "differ from the baseline" in found["f"]
    # One whole value far past the range (sk_digits x10^4: 1.0 -> 10000.0). Merge of
    # heal-hardening: no sample-std (Chebyshev) bound; it is held as a unit-sized step
    # past anything the baseline holds (max 9 here).
    one_far = [dict(r) for r in clean]
    one_far[0]["w"] = 90000.0
    found = RecoveryLoop._unrepaired_value_drift(one_far, ["w", "f"], dist)
    assert set(found) == {"w"} and "unit error" in found["w"]
    # One value past the range but within a unit step of it is no evidence of anything.
    one_out = [dict(r) for r in clean]
    one_out[0]["w"] = 80.0
    assert RecoveryLoop._unrepaired_value_drift(one_out, ["w", "f"], dist) == {}
    # Many rows past the range are proven by the exact past-range test, whatever the
    # column's shape (ten rows past a 600-row baseline's max in a 300-row batch: p ~ 2e-4).
    many_out = [dict(r) for r in clean]
    for r in many_out[:10]:
        r["f"] = 200.0
    found = RecoveryLoop._unrepaired_value_drift(many_out, ["w", "f"], dist)
    assert set(found) == {"f"} and "past the baseline's range" in found["f"]


def test_unrepaired_value_drift_holds_no_clean_heavy_tailed_column():
    """Merge of heal-hardening: the sandbox's hold reads no sample std. Clean batches of
    heavy-tailed columns (the hardening sweeps' lognormal(0, 2), pareto(1.2)) are held
    rarely: by the exact past-range test at its 1e-3 level, or by a unit-fault reach that
    grows with the baseline's own dynamic range (a fixed 10x held 2-11% of them)."""
    import numpy as np

    from uasr.drift_detector import DriftDetector
    from uasr.models import BatchPayload
    from uasr.recovery_loop import RecoveryLoop

    held = 0
    trials = 0
    for seed in range(40):
        rng = np.random.default_rng(seed)
        base = [{"ln": float(v), "pa": float(p)} for v, p in
                zip(rng.lognormal(0, 2, 200), rng.pareto(1.2, 200) + 1)]
        det = DriftDetector()
        det.register_baseline(f"h{seed}", BatchPayload(source_id=f"h{seed}", columns=["ln", "pa"], rows=base))
        dist = det.baseline_distributions(f"h{seed}")
        for n in (5, 38, 100):
            batch = [{"ln": float(v), "pa": float(p)} for v, p in
                     zip(rng.lognormal(0, 2, n), rng.pareto(1.2, n) + 1)]
            trials += 1
            held += bool(RecoveryLoop._unrepaired_value_drift(batch, ["ln", "pa"], dist))
    assert held <= 2, f"{held} of {trials} clean heavy-tailed batches held"


@pytest.mark.parametrize("edges, decimals, holds, passes", [
    # sk_digits pixel_3_7: 1075 zeros and three 1s, whole numbers. x10^4 is held; a
    # clean pixel value is not.
    ([0.0, 1.0], 0, [10000.0], [1.0, 3.0, 9.0]),
    # A pixel whose baseline drew only zeros: whole numbers reach 10 units.
    ([0.0], 0, [10000.0, 30000.0], [1.0, 3.0, 8.0]),
    # A positive heavy tail spanning four decades reaches four decades past its max.
    ([0.01, 100.0], 2, [1e7], [5000.0, 99_000.0]),
    # A compact positive column: 10x its largest magnitude.
    ([40.0, 60.0], 1, [601.0], [100.0, 599.0]),
])
def test_the_unit_fault_reach_reads_only_the_baselines_range_and_precision(edges, decimals, holds, passes):
    from uasr.recovery_loop import RecoveryLoop

    reach = RecoveryLoop._unit_fault_reach(edges, decimals)
    assert all(abs(v) > reach for v in holds), reach
    assert all(abs(v) <= reach for v in passes), reach


async def test_a_renamed_batch_holds_one_sparse_value_scaled_x10_4(_heal_db):
    """The real-data lane's sk_digits rename + x10^4 (combos 13 and 19), self-contained:
    a pixel-like column of zeros and a few 1s gets x10^4 beside a proven rename. Its one
    non-zero value becomes 10000.0 -- one row past the range, which no exact test can
    prove -- and used to come back healed. The sandbox's unit-fault reach holds it."""
    for seed in range(4):
        rnd = random.Random(seed)

        def rows(n, ones):
            out = [{"k": rnd.randint(1, 9), "sparse": 0.0, "level": float(rnd.randint(0, 16))} for _ in range(n)]
            for i in rnd.sample(range(n), ones):
                out[i]["sparse"] = 1.0
            return out

        cols = [Column("k", "count", 0.0, {"dtype": "int"}), Column("sparse", "normal", 0.0, {"dtype": "float"}),
                Column("level", "normal", 0.0, {"dtype": "float"})]
        ds = Dataset(seed=seed, columns=cols, baseline=rows(1078, 3), truth=rows(700, 1))
        sent = copy_rows(ds.truth)
        for row in sent:
            row["lvl_x"] = row.pop("level")  # a rename the fingerprint proves
            row["sparse"] = row["sparse"] * 10 ** 4
        trial = Trial(fault="combo", seed=seed, dataset=ds, sent=sent, expected=copy_rows(ds.truth),
                      snapshot=None, tenant=None)
        out = await heal(trial)
        result = classify(trial, out)
        assert result.outcome != WRONG, (seed, result.why)
        assert "sparse" in (out.get("reason") or ""), (seed, out.get("reason"))
