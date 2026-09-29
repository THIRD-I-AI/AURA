"""BUG-246: two counterfactual jobs overlapping in one process must not trample each
other's seeded numpy RNG -- each must reproduce the solo result byte-for-byte."""
import asyncio

import pytest

from counterfactual_service.engine import dowhy_available, run_estimators
from counterfactual_service.schemas import InterventionSpec, OutcomeSpec
from tests._synthetic_data import synthetic_dag_full, synthetic_dataset

pytestmark = pytest.mark.skipif(not dowhy_available(), reason="dowhy required")


def _key(ests):
    return sorted((e.method, round(e.point, 6), round(e.ci_lower, 6), round(e.ci_upper, 6))
                  for e in ests if e.error is None)


@pytest.mark.asyncio
async def test_overlapping_jobs_reproduce_the_solo_result(monkeypatch):
    # generous per-estimator budget: this test is about RNG isolation, not speed
    monkeypatch.setenv("AURA_CF_ESTIMATOR_TIMEOUT_S", "600")
    df = synthetic_dataset(n=300, seed=0xdeadbeef)
    t = InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0)
    o = OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31"))
    args = (df, t, o, synthetic_dag_full())

    solo = _key(await run_estimators(*args, methods=["ipw"]))
    a, b = await asyncio.gather(run_estimators(*args, methods=["ipw"]),
                                run_estimators(*args, methods=["ipw"]))
    assert solo, "ipw must produce an estimate"
    assert _key(a) == solo and _key(b) == solo, (solo, _key(a), _key(b))
