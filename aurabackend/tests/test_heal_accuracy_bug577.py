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
