"""BUG-224: ForestDRLearner must run single-process.

econml's ForestDRLearner defaults to n_jobs=-1, which forked one joblib worker
per CPU core (22 on a dev laptop, ~250MB each) inside the single uvicorn
worker, and parallelised the BLB inference the docstring promises is
single-threaded for byte-identical replays."""
import pytest

from counterfactual_service import engine
from counterfactual_service.engine import dowhy_available, econml_available, run_estimators
from counterfactual_service.schemas import InterventionSpec, OutcomeSpec
from tests._synthetic_data import synthetic_dag_full, synthetic_dataset

pytestmark = pytest.mark.skipif(
    not (dowhy_available() and econml_available()), reason="dowhy + econml required"
)


def _args():
    return (
        synthetic_dataset(n=400),
        InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        synthetic_dag_full(),
    )


@pytest.mark.asyncio
async def test_forest_dr_learner_is_constructed_with_n_jobs_1(monkeypatch):
    real = engine.ForestDRLearner
    seen = {}

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)

    monkeypatch.setattr(engine, "ForestDRLearner", spy)
    [est] = await run_estimators(*_args(), methods=["forest_dr"])
    assert est.error is None, est.error
    assert seen.get("n_jobs") == 1


@pytest.mark.asyncio
async def test_forest_dr_is_byte_identical_across_runs():
    a = await run_estimators(*_args(), methods=["forest_dr"])
    b = await run_estimators(*_args(), methods=["forest_dr"])
    assert a[0].model_dump(exclude={"elapsed_ms"}) == b[0].model_dump(exclude={"elapsed_ms"})


def _record_parallel_requests(monkeypatch):
    from joblib import _parallel_backends as pb

    real = pb.LokyBackend.configure
    requested = []

    def spy(self, n_jobs=1, *a, **k):
        requested.append(n_jobs)
        return real(self, n_jobs, *a, **k)

    monkeypatch.setattr(pb.LokyBackend, "configure", spy)
    return requested


def test_gcm_attribution_never_requests_a_parallel_process_pool(monkeypatch):
    """The real culprit behind ~22 x 250MB worker processes per pre-push run:
    dowhy gcm's model selection ran Parallel(n_jobs=-1)."""
    import numpy as np
    import pandas as pd

    from causal_service import discovery

    if not discovery.dowhy_available():
        pytest.skip("dowhy required")
    requested = _record_parallel_requests(monkeypatch)
    rng = np.random.default_rng(0)
    a = rng.normal(size=200)
    train = pd.DataFrame({"a": a, "b": rng.normal(size=200), "y": 2 * a + rng.normal(size=200) * 0.1})
    anom = pd.DataFrame({"a": [3.0, 3.5], "b": [0.1, 0.2], "y": [6.0, 7.0]})

    attrs, method, _, _ = discovery.attribute(
        train, anom, target="y", candidates=["a", "b"], edges=None, method="gcm",
        top_k=3, enforce_stationarity=False,
    )
    assert method == "gcm" and attrs
    assert [n for n in requested if n not in (1, None)] == []
