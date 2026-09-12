"""
DSR-008 — double_ml's silent degradation to the DoWhy linear_regression
stub when econml isn't installed had no signal anywhere: the estimate
still reported method="double_ml" with nothing distinguishing it from
a real DR-Learner run, and GET /counterfactual/info always listed
"double_ml" the same way regardless of whether econml was actually
present. forest_dr (same econml dependency) already surfaced a
structured error in this situation -- double_ml's silent fallback was
the inconsistent one.

Fixed: CounterfactualEstimate.degraded is True iff double_ml ran the
DoWhy fallback; /counterfactual/info reports econml_available so a
caller can predict the behavior before submitting a job.

The `degraded` field is pure provenance metadata and MUST be excluded
from the audit-artifact hash basis (_HASH_EXCLUDE_FIELDS) -- otherwise
adding it would break verification of every artifact signed before
this fix existed. That's the highest-risk part of this change and gets
its own dedicated test below.
"""
from __future__ import annotations

import pytest

import counterfactual_service.engine as engine
from counterfactual_service.engine import _run_one_estimator, econml_available, strip_for_hashing
from counterfactual_service.schemas import (
    CounterfactualArtifact,
    CounterfactualEstimate,
    CounterfactualQuery,
    DAGSpec,
    DatasetRef,
    InterventionSpec,
    OutcomeSpec,
)
from tests._synthetic_data import synthetic_dag_full, synthetic_dataset


def _spec():
    treatment = InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0)
    outcome = OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31"))
    dag = synthetic_dag_full()
    return treatment, outcome, dag


def test_double_ml_degraded_true_when_econml_unavailable(monkeypatch):
    """Force the no-econml branch regardless of the test environment's
    actual econml install -- deterministic either way."""
    monkeypatch.setattr(engine, "_ECONML_AVAILABLE", False)
    treatment, outcome, dag = _spec()
    df = synthetic_dataset(n=200)

    est = _run_one_estimator("double_ml", df, treatment, outcome, dag, seed=0)

    assert est.method == "double_ml"
    assert est.degraded is True, (
        "double_ml fell back to the DoWhy stub but didn't say so -- "
        "this is exactly DSR-008's silent-degradation bug"
    )


def test_double_ml_not_degraded_when_econml_available(monkeypatch):
    """The DR-Learner path must never set degraded=True -- it's the
    real doubly-robust estimator, not a fallback."""
    if not econml_available():
        pytest.skip("requires econml installed")
    treatment, outcome, dag = _spec()
    df = synthetic_dataset(n=200)

    est = _run_one_estimator("double_ml", df, treatment, outcome, dag, seed=0)

    assert est.method == "double_ml"
    assert est.degraded is False, "the real DR-Learner path must not report degraded"


def test_other_methods_never_report_degraded(monkeypatch):
    """degraded is specific to double_ml's econml dependency -- every
    other DoWhy-routed method must always report False, econml or not."""
    monkeypatch.setattr(engine, "_ECONML_AVAILABLE", False)
    treatment, outcome, dag = _spec()
    df = synthetic_dataset(n=200)

    est = _run_one_estimator("linear_regression", df, treatment, outcome, dag, seed=0)
    assert est.degraded is False


# ── The critical backward-compatibility guarantee ──────────────────────


def test_degraded_excluded_from_hash_basis():
    """Two otherwise-identical artifacts differing ONLY in one estimate's
    `degraded` flag must hash to the exact same strip_for_hashing() bytes.

    This is what makes it safe to have added this field at all: every
    artifact signed before `degraded` existed has no such key in its
    persisted JSON, and re-validating it through the current
    CounterfactualArtifact model fills in the default (False). If
    `degraded` were NOT excluded from the hash basis, that default
    would appear in the reconstructed bytes for verification even
    though it was never part of what was actually signed -- silently
    breaking verify_artifact for every pre-existing signed artifact.
    """
    treatment, outcome, dag = _spec()
    query = CounterfactualQuery(
        question="dsr008-hash-basis",
        treatment=treatment,
        outcome=outcome,
        dag=DAGSpec(edges=dag["edges"]),
        dataset=DatasetRef(source_id="dsr008"),
    )
    base_kwargs = dict(
        record_id="r1",
        query=query,
        confidence="medium",
        schema_version="v1",
        dataset_fingerprint="d" * 64,
        estimates=[
            CounterfactualEstimate(
                method="double_ml", point=1.0, ci_lower=0.5, ci_upper=1.5,
                n_samples=100, elapsed_ms=10.0, degraded=False,
            ),
        ],
        refutations=[],
    )
    not_degraded = CounterfactualArtifact(**base_kwargs)
    degraded = CounterfactualArtifact(**{
        **base_kwargs,
        "estimates": [
            CounterfactualEstimate(
                method="double_ml", point=1.0, ci_lower=0.5, ci_upper=1.5,
                n_samples=100, elapsed_ms=999.0, degraded=True,
            ),
        ],
    })

    assert strip_for_hashing(not_degraded) == strip_for_hashing(degraded), (
        "degraded (and elapsed_ms) must be excluded from the hash basis -- "
        "if this fails, adding `degraded` broke backward-compat verification"
    )
